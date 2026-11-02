import copy
import hashlib
import io
import math
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageOps, UnidentifiedImageError
from .config import STAGES, LABELS, model_paths
from .store import Store, Conflict, invalidate, now, upgrade
from . import inference

class Service:
    def __init__(self, root=None):
        self.store = Store(root) if root else Store()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.jobs = {j['id']:j for j in self.store.records('jobs')}
        for job in self.jobs.values():
            if job['status'] in ('queued','running'):
                job.update(status='failed',error='服务重启中断，请重新提交',finished_at=now())
                self.store.record('jobs',job)
        self.job_lock = threading.RLock()
        from .slides import Slides
        self.slides=Slides(self)

    def upload(self, content, filename, identifier=None):
        start=time.perf_counter()
        try:
            with Image.open(io.BytesIO(content)) as raw:
                if raw.width * raw.height > 60_000_000:
                    raise ValueError('当前入口限制为 6000 万像素；全切片请使用既有 detect_slide.py 离线切块')
                image = ImageOps.exif_transpose(raw).convert('RGB'); image.load()
        except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as exc:
            raise ValueError('图片无法读取，请上传普通图像；SDPC/SDQC 使用离线切片入口') from exc
        identifier=identifier or uuid.uuid4().hex;folder=self.store.root/'images';folder.mkdir(exist_ok=True)
        image.save(folder/f'{identifier}.png')
        row=inference.quality(image)
        p=upgrade(dict(id=identifier,name=filename[:200],width=image.width,height=image.height,
                       revision=0,stage='quality',created_at=now(),updated_at=now(),target_phase='mi',
                       confirmed={s:False for s in STAGES},results={s:[] for s in STAGES},models={}))
        row['review_status']='pending';p['results']['quality']=[row]
        model=str(model_paths()['quality']) if row['source']=='model' else 'quality-rules-v1'
        p['predictions']['quality']=self.snapshot([row],model)
        p['models']['quality']=model
        p['timings'].append(dict(stage='quality',status='completed',compute_seconds=time.perf_counter()-start,
                                 queue_seconds=0,finished_at=now(),scope='upload_decode_quality_save_image'))
        return self.store.create(p)

    def import_sdpc_crop(self,path,slide,row,identifier):
        p=self.upload(path.read_bytes(),slide['name']+' / cluster_'+str(row['id']),identifier)
        def attach(p):
            bounds=row['crop_box_l0'];box=row['box_l0']
            local=[max(0,box[0]-bounds[0]),max(0,box[1]-bounds[1]),min(p['width'],box[2]-bounds[0]),min(p['height'],box[3]-bounds[1])]
            cluster=dict(id='cluster-'+str(row['id']),label='cluster',bbox=local,confidence=row['score'],source='model',reviewed=False,review_status='pending')
            p['results']['clusters']=[cluster];p['predictions']['clusters']=self.snapshot([cluster],str(model_paths()['clusters']))
            if slide.get('detector_config',{}).get('weights_sha256'):p['predictions']['clusters']['model_sha256']=slide['detector_config']['weights_sha256']
            p['models']['clusters']=str(model_paths()['clusters'])
            p['source_slide']=dict(id=slide['id'],name=slide['name'],source_sha256=slide['source_sha256'],dimensions_l0=slide['dimensions_l0'],crop_box_l0=bounds,cluster_box_l0=box,cluster_id=row['id'],partial=not slide['summary']['full_slide_run'])
            p['input_type']='sdpc_crop';p['stage']='quality'
        return self.store.change(p['id'],p['revision'],'sdpc:extract',attach)

    @staticmethod
    def snapshot(rows,model):
        from pathlib import Path
        digest=None
        if model and Path(model).is_file():
            sha=hashlib.sha256()
            with Path(model).open('rb') as f:
                for block in iter(lambda:f.read(1024*1024),b''):sha.update(block)
            digest=sha.hexdigest()
        return dict(rows=copy.deepcopy(rows),model=model,created_at=now(),stale=False,
                    model_sha256=digest,run_id=uuid.uuid4().hex,
                    source=rows[0].get('source','unknown') if rows else ('model' if digest else 'heuristic'),
                    bootstrap=any(r.get('bootstrap',False) for r in rows))

    def image(self,identifier):
        self.store.get(identifier)
        with Image.open(self.store.root/'images'/f'{identifier}.png') as im:return im.copy()

    @staticmethod
    def active(p,stage):
        label={'clusters':'cluster','instances':'chromosome'}[stage]
        return [r for r in p['results'][stage] if r['label']==label]

    def require_upstream(self,p,stage):
        if stage not in STAGES:raise ValueError('阶段无效')
        if stage!='quality' and p['results']['quality'][0]['label']=='unusable':
            raise Conflict('不可用图片已归档，不能进入分析')
        if not all(p['confirmed'][s] for s in STAGES[:STAGES.index(stage)]):
            raise Conflict('请先人工确认上一步结果')

    def validate_rows(self,p,stage,rows):
        if not isinstance(rows,list) or len(rows)>5000:raise ValueError('标注条目数无效')
        known={r['id']:r for r in self.active(p,'clusters')}
        instances={r['id']:r for r in self.active(p,'instances')}
        previous={r['id']:r for r in p['results'][stage]};ids=set();clean=[]
        for incoming in rows:
            row=copy.deepcopy(incoming);key=row.get('id')
            if not isinstance(key,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,120}',key) or key in ids or row.get('label') not in LABELS[stage]:
                raise ValueError('标注 ID 重复、格式或标签无效')
            ids.add(key)
            if stage=='phase' and key not in known:raise ValueError('未对应有效染色体簇')
            if stage=='outliers':
                if key not in instances:raise ValueError('未对应具体染色体候选')
                row.update(instance_id=key,cluster_id=instances[key]['cluster_id'],bbox=instances[key]['bbox'])
            if stage in ('clusters','instances'):
                box=row.get('bbox',[])
                if len(box)!=4 or not all(isinstance(v,(int,float)) for v in box):raise ValueError('需要四个原图像素坐标')
                x1,y1,x2,y2=box
                if not 0<=x1<x2<=p['width'] or not 0<=y1<y2<=p['height']:raise ValueError('候选框必须在原图范围内且面积大于零')
                if stage=='instances':
                    parent=row.get('cluster_id');target={r['id'] for r in p['results']['phase'] if r['label']==p['target_phase']}
                    if parent not in known or parent not in target:raise ValueError('单条染色体必须关联目标分裂期的有效簇')
                    parent_box=known[parent]['bbox']
                    if not (math.floor(parent_box[0])<=x1<x2<=math.ceil(parent_box[2]) and math.floor(parent_box[1])<=y1<y2<=math.ceil(parent_box[3])):
                        raise ValueError('染色体框必须位于所属簇内')
                    row['instance_id']=key
                polygon=row.get('polygon')
                if previous.get(key,{}).get('bbox')!=box and key in previous:row['polygon']=None;polygon=None
                if polygon is not None:
                    if not isinstance(polygon,list) or not 3<=len(polygon)<=2000:raise ValueError('轮廓点数无效')
                    for point in polygon:
                        if len(point)!=2 or not all(isinstance(v,(int,float)) for v in point) or not (x1<=point[0]<=x2 and y1<=point[1]<=y2):raise ValueError('轮廓点超出框范围')
            if stage=='quality' and (len(rows)!=1 or key!='image'):raise ValueError('原图质量必须且只能有一条')
            old=previous.get(key)
            changed=old is None or any(row.get(k)!=old.get(k) for k in ('label','bbox','polygon','note'))
            if changed:
                # Server-owned provenance and decision records cannot be forged by row edits.
                row={k:v for k,v in row.items() if k not in ('reviewer','reviewed_at','review_reason','review_status')}
                row.update(source='human',reviewed=True,review_status='corrected',reviewed_at=now())
            elif old:row=copy.deepcopy(old)
            clean.append(row)
        if stage=='quality' and len(clean)!=1:raise ValueError('原图质量必须且只能有一条')
        return clean

    def edit(self,identifier,body):
        stage=body['stage']
        def update(p):
            self.require_upstream(p,stage);rows=self.validate_rows(p,stage,body['rows'])
            for row in rows:
                if row.get('review_status')=='corrected':
                    row['reviewer']=str(body.get('reviewer','本地复核员'))[:100]
                    row['review_reason']=str(body.get('reason','人工修正标注'))[:1000]
            invalidate(p,stage);p['results'][stage]=rows
        return self.store.change(identifier,body['revision'],'edit:'+stage,update)

    def review(self,identifier,body):
        stage=body['stage'];decision=body['decision']
        if decision not in ('confirm','reject','correct'):raise ValueError('复核动作无效')
        reviewer=str(body.get('reviewer','')).strip();reason=str(body.get('reason','')).strip()
        if not reviewer or (decision!='confirm' and not reason):raise ValueError('需要复核人；修正或驳回需填写原因')
        def update(p):
            self.require_upstream(p,stage)
            rows=copy.deepcopy(p['results'][stage]);row=next((r for r in rows if r['id']==body['row_id']),None)
            if row is None:raise ValueError('待复核记录不存在')
            if decision=='correct':
                if body.get('label') not in LABELS[stage]:raise ValueError('修正标签无效')
                row['label']=body['label']
            elif decision=='reject':
                row['label']={'clusters':'exclude','instances':'exclude','phase':'unknown','outliers':'normal'}.get(stage,row['label'])
            rows=self.validate_rows(p,stage,rows);row=next(r for r in rows if r['id']==body['row_id'])
            row.update(reviewed=True,review_status={'confirm':'confirmed','reject':'rejected','correct':'corrected'}[decision],
                       reviewer=reviewer[:100],review_reason=reason[:1000],reviewed_at=now())
            invalidate(p,stage);p['results'][stage]=rows
        return self.store.change(identifier,body['revision'],'review:'+decision+':'+stage,update)

    def confirm(self,identifier,body):
        stage=body['stage']
        def update(p):
            self.require_upstream(p,stage);rows=p['results'][stage]
            if stage=='quality' and rows[0].get('review_status')=='rejected':raise ValueError('请先修正被驳回的质量等级')
            if stage=='phase':
                if {r['id'] for r in rows}!={r['id'] for r in self.active(p,'clusters')} or any(r['label']=='unknown' for r in rows):raise ValueError('请完成每个有效簇的分裂期标注')
                if body.get('target_phase','mi')!='mi':raise ValueError('当前仅支持目标 MI')
            if stage=='outliers' and any(r['label']=='unknown' for r in rows):raise ValueError('请逐项将待确认结果修正为离群或正常后再确认')
            if stage in ('instances','outliers') and not rows and stage not in p['predictions']:
                raise ValueError('请先运行本阶段；没有目标时也需记录空结果')
            p['confirmed'][stage]=True;p['truth_complete'][stage]=bool(body.get('truth_complete',False))
            for row in rows:
                row['reviewed']=True
                if row.get('review_status') in (None,'pending'):
                    row.update(review_status='confirmed',reviewer=str(body.get('reviewer','本地复核员'))[:100],reviewed_at=now(),review_reason='阶段整体确认')
            p['stage']=STAGES[min(STAGES.index(stage)+1,len(STAGES)-1)]
            if stage=='quality' and rows[0]['label']=='unusable':p['stage']='quality'
        return self.store.change(identifier,body['revision'],'confirm:'+stage,update)

    def run(self,identifier,body):
        stage=body['stage'];project=self.store.get(identifier)
        if project['revision']!=body['revision']:raise Conflict('结果已更新，请刷新')
        self.require_upstream(project,stage)
        job_id=uuid.uuid4().hex;queued=time.perf_counter()
        with self.job_lock:
            if any(j['project_id']==identifier and j['status'] in ('queued','running') for j in self.jobs.values()):raise Conflict('该项目已有运行中的任务')
            job=dict(id=job_id,project_id=identifier,stage=stage,status='queued',created_at=now())
            self.jobs[job_id]=job;self.store.record('jobs',job)
        def work():
            start=time.perf_counter();job.update(status='running',queue_seconds=start-queued,started_at=now());self.store.record('jobs',job)
            try:
                image=self.image(identifier);model=None
                cluster_rows=self.active(project,'clusters')
                if stage=='quality':
                    rows=[inference.quality(image)];model=str(model_paths()['quality']) if rows[0]['source']=='model' else 'quality-rules-v1'
                elif stage=='clusters':rows,model=inference.clusters(image)
                elif stage=='phase':
                    rows,model=inference.phase([(r['id'],inference.crop(image,r['bbox'])) for r in cluster_rows]) if cluster_rows else ([],str(model_paths()['phase']))
                elif stage=='instances':
                    targets={r['id'] for r in project['results']['phase'] if r['label']==project['target_phase']}
                    rows,model=inference.instances(image,[r for r in cluster_rows if r['id'] in targets])
                else:
                    candidates=self.active(project,'instances')
                    crops=[(r['id'],inference.crop(image,r['bbox'])) for r in candidates]
                    rows=inference.outliers(crops) if len(crops)>=3 else [dict(id=key,label='unknown',score=None,rank=i+1,source='feature_baseline',note='不足三个候选，仅提供定位，需人工判断') for i,(key,_) in enumerate(crops)]
                    lookup={r['id']:r for r in candidates}
                    for r in rows:
                        r.update(instance_id=r['id'],cluster_id=lookup[r['id']]['cluster_id'],bbox=lookup[r['id']]['bbox'])
                        if r.get('score') is not None:r['label']='outlier' if r['score']>=3 else 'normal'
                    model=os.environ.get('OUTLIER_WEIGHTS','robust-image-features-v1')
                for r in rows:r.setdefault('review_status','pending')
                elapsed=time.perf_counter()-start
                def save(p):
                    human={r['id']:r for r in p['results'][stage] if r.get('source')=='human' or r.get('review_status') in ('confirmed','rejected','corrected')}
                    if stage in ('clusters','instances'):
                        from .metrics import iou
                        filtered=[r for r in rows if not any(iou(r['bbox'],h['bbox'])>=.5 for h in human.values())]
                    else:filtered=rows
                    merged=[human.pop(r['id'],r) for r in filtered];merged.extend(human.values())
                    invalidate(p,stage);p['results'][stage]=merged;p['models'][stage]=model
                    p['predictions'][stage]=self.snapshot(rows,model)
                    p['timings'].append(dict(job_id=job_id,stage=stage,status='completed',compute_seconds=elapsed,queue_seconds=job['queue_seconds'],finished_at=now()))
                self.store.change(identifier,project['revision'],'predict:'+stage,save)
                job['status']='completed'
            except Exception as exc:job.update(status='failed',error=str(exc))
            finally:
                job.update(compute_seconds=time.perf_counter()-start,finished_at=now());self.store.record('jobs',job)
        self.pool.submit(work)
        return copy.deepcopy(job)

    def batch(self,body):
        ids=body.get('project_ids',[]);stage=body.get('stage')
        if not isinstance(ids,list) or not 1<=len(ids)<=200 or len(set(ids))!=len(ids) or stage not in STAGES:raise ValueError('批次需 1–200 个唯一项目及有效阶段')
        record=dict(id=uuid.uuid4().hex,stage=stage,created_at=now(),items=[])
        for identifier in ids:
            try:
                p=self.store.get(identifier);job=self.run(identifier,dict(stage=stage,revision=p['revision']))
                record['items'].append(dict(project_id=identifier,job_id=job['id']))
            except (ValueError,KeyError) as exc:record['items'].append(dict(project_id=identifier,status='needs_review',error=str(exc)))
        self.store.record('batches',record)
        return self.batch_status(record['id'])

    def batch_status(self,identifier):
        batch=next((b for b in self.store.records('batches') if b['id']==identifier),None)
        if not batch:raise KeyError('批次不存在')
        for item in batch['items']:
            if 'job_id' in item:item.update(self.jobs[item['job_id']])
        statuses=[r['status'] for r in batch['items']]
        batch['status']='running' if any(s in ('queued','running') for s in statuses) else 'completed_with_issues' if any(s!='completed' for s in statuses) else 'completed'
        return batch
