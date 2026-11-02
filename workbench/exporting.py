"""Versioned result bundles: JSON, CSV, originals, overlays, crops and audit."""
import csv
import io
import json
import tempfile
import zipfile
from PIL import ImageDraw
from .metrics import evaluate, aggregate
from .inference import crop

def overlay(image,project):
    output=image.copy();draw=ImageDraw.Draw(output,'RGBA')
    abnormal={r['instance_id'] for r in project['results']['outliers'] if r['label']=='outlier' and r.get('review_status')!='rejected'}
    for row in project['results']['clusters']:
        if row['label']=='cluster':draw.rectangle(row['bbox'],outline=(30,140,120,200),width=2)
    for row in project['results']['instances']:
        if row['label']!='chromosome':continue
        flag=row['id'] in abnormal;color=(240,60,60,255) if flag else (40,140,220,200)
        if row.get('polygon'):draw.polygon([tuple(p) for p in row['polygon']],fill=(*color[:3],60),outline=color,width=2)
        draw.rectangle(row['bbox'],outline=color,width=3 if flag else 1)
        draw.text((row['bbox'][0],max(0,row['bbox'][1]-12)),row['id'],fill=color)
    return output

def json_bytes(value):return json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8')

def csv_bytes(rows):
    f=io.StringIO(newline='');fields=list(dict.fromkeys(key for row in rows for key in row)) or ['id']
    writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
    for row in rows:
        values={k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()}
        writer.writerow({k:"'"+v if isinstance(v,str) and v[:1] in ('=','+','-','@') else v for k,v in values.items()})
    return f.getvalue().encode('utf-8-sig')

def bundle(service,identifiers):
    if not 1<=len(identifiers)<=200 or len(set(identifiers))!=len(identifiers):raise ValueError('每次导出 1–200 个唯一项目')
    projects=[service.store.get(i) for i in identifiers]
    # TemporaryFile is seekable on Python 3.10 and spills large exports to disk.
    spool=tempfile.TemporaryFile(mode='w+b')
    try:
        with zipfile.ZipFile(spool,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as z:
            z.writestr('manifest.json',json_bytes(dict(schema_version=2,projects=[p['id'] for p in projects],coordinate_space='EXIF-corrected original image pixels',metrics_scope='human review agreement; not independent blinded evaluation')))
            z.writestr('aggregate_metrics.json',json_bytes(aggregate(projects)))
            combined=[]
            for p in projects:
                prefix=p['id']+'/';im=service.image(p['id'])
                z.writestr(prefix+'result.json',json_bytes(p));z.writestr(prefix+'metrics.json',json_bytes(evaluate(p)))
                z.writestr(prefix+'audit.json',json_bytes(service.store.history(p['id'])))
                z.writestr(prefix+'jobs.json',json_bytes([j for j in service.jobs.values() if j['project_id']==p['id']]))
                z.writestr(prefix+'timings.json',json_bytes(p['timings']))
                z.write(service.store.root/'images'/f"{p['id']}.png",prefix+'original.png')
                buffer=io.BytesIO();overlay(im,p).save(buffer,'PNG');z.writestr(prefix+'annotated.png',buffer.getvalue())
                rows=[]
                for stage,records in p['results'].items():
                    for row in records:
                        flat=dict(project_id=p['id'],filename=p['name'],stage=stage,**row)
                        rows.append(flat);combined.append(flat)
                        if stage in ('clusters','instances') and row['label']!='exclude':
                            buffer=io.BytesIO();crop(im,row['bbox']).save(buffer,'PNG')
                            z.writestr(prefix+stage+'/'+row['id']+'.png',buffer.getvalue())
                z.writestr(prefix+'records.csv',csv_bytes(rows))
            z.writestr('records.csv',csv_bytes(combined))
        spool.seek(0);return spool
    except Exception:
        spool.close();raise
