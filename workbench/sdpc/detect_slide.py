"""SDPC -> streamed native L1/L2 detection -> global merge -> native L0 PNG crops."""
from . import runtime
import argparse
import csv
import hashlib
import json
import math
import os
import time
from pathlib import Path

import numpy as np
from PIL import ImageEnhance
from .slide_io import Slide
from .native_decode import decode_tiles
from .progress import scan_progress

def positions(length, tile, overlap):
    if length <= tile:return [0]
    p=list(range(0,length-tile+1,tile-overlap))
    if p[-1]!=length-tile:p.append(length-tile)
    return p

def intersection(a,b):
    return max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))

def same_cluster(a,b):
    aa=(a[2]-a[0])*(a[3]-a[1]);bb=(b[2]-b[0])*(b[3]-b[1]);inter=intersection(a,b)
    if min(aa,bb)<=0 or inter<=0:return False
    iou=inter/(aa+bb-inter)
    # Containment also matches partial boundary predictions to full predictions.
    return iou>=.4 or inter/min(aa,bb)>=.8

def merge_detections(detections):
    """Spatial index avoids quadratic comparisons over an entire large slide."""
    detections=sorted(detections,key=lambda d:d['score'],reverse=True)
    parents=list(range(len(detections)));grid={};cell=2048
    def find(i):
        while parents[i]!=i:parents[i]=parents[parents[i]];i=parents[i]
        return i
    for i,d in enumerate(detections):
        b=d['box_l0'];keys=[(x,y) for x in range(int(b[0]//cell),int(b[2]//cell)+1) for y in range(int(b[1]//cell),int(b[3]//cell)+1)]
        neighbors=set(j for key in keys for j in grid.get(key,[]))
        for j in neighbors:
            if same_cluster(b,detections[j]['box_l0']):parents[find(i)]=find(j)
        for key in keys:grid.setdefault(key,[]).append(i)
    groups={}
    for i,d in enumerate(detections):groups.setdefault(find(i),[]).append(d)
    result=[]
    for members in groups.values():
        boxes=[m['box_l0'] for m in members]
        box=[min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]
        result.append(dict(box_l0=box,score=max(m['score'] for m in members),levels=sorted(set(m['level'] for m in members)),observations=len(members),needs_recenter=all(m['near_tile_edge'] for m in members),source_detections=members))
    return sorted(result,key=lambda d:(d['box_l0'][1],d['box_l0'][0]))

def detect_batch(model,items,slide,args):
    output=model.predict([im[:,:,::-1].copy() for _,im in items],imgsz=args.imgsz,conf=args.conf,iou=.5,device=args.device,verbose=False,max_det=100)
    rows=[]
    for (tile,im),prediction in zip(items,output):
        ds=slide.downsamples[tile['level']];x,y=tile['origin'];h,w=im.shape[:2]
        detections=[]
        for b,score in zip(prediction.boxes.xyxy.cpu().tolist(),prediction.boxes.conf.cpu().tolist()):
            x0,y0,x1,y1=b
            if x1<=x0 or y1<=y0:continue
            # Float coordinates remain in L0 until final lossless crop rounding.
            box=[x+x0*ds,y+y0*ds,x+x1*ds,y+y1*ds]
            detections.append(dict(box_l0=box,score=score,level=tile['level'],near_tile_edge=min(x0,y0,w-x1,h-y1)<24,tile_key=tile['key']))
        rows.append(dict(tile_key=tile['key'],detections=detections))
    return rows

def recover_boundaries(model,clusters,slide,args):
    recovered=[]
    for n,c in enumerate(clusters):
        if n%100==0:print(f'RECENTER {n}/{len(clusters)} clusters',flush=True)
        c['boundary_recovery']='not_needed'
        if c['needs_recenter']:
            level=min(c['levels']);ds=slide.downsamples[level]
            old=c['box_l0'];cx=(old[0]+old[2])/2;cy=(old[1]+old[3])/2
            side=max(args.tile,math.ceil(max(old[2]-old[0],old[3]-old[1])/ds*2))
            side=min(side,args.tile*2,*slide.dimensions[level])
            sw,sh=slide.dimensions[0]
            x=int(max(0,min(sw-side*ds,cx-side*ds/2))//ds*ds)
            y=int(max(0,min(sh-side*ds,cy-side*ds/2))//ds*ds)
            tile=dict(key=f'recenter_{n}',level=level,origin=[x,y])
            rows=detect_batch(model,[(tile,slide.read(x,y,level,side,side))],slide,args)
            matched=[d for d in rows[0]['detections'] if same_cluster(old,d['box_l0'])]
            if matched:
                b=max(matched,key=lambda d:d['score'])
                new=b['box_l0'];c['box_l0']=[min(old[0],new[0]),min(old[1],new[1]),max(old[2],new[2]),max(old[3],new[3])]
                c['score']=max(c['score'],b['score']);c['source_detections'].extend(matched)
                c['boundary_recovery']='recentered_still_at_edge' if b['near_tile_edge'] else 'recentered_complete_prediction'
            else:c['boundary_recovery']='recenter_no_match_needs_review'
        # Merge once more because recentering can reveal duplicate fragments.
        for d in c['source_detections']:recovered.append(dict(d,boundary_recovery=c['boundary_recovery']))
    merged=merge_detections(recovered)
    for c in merged:
        c['boundary_recovery']=sorted(set(d['boundary_recovery'] for d in c['source_detections']))
    return merged

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('slide',type=Path)
    p.add_argument('--weights',type=Path,default=runtime.HOME/'weights/chromosome_cluster.pt')
    p.add_argument('--output',type=Path,default=runtime.HOME/'results')
    p.add_argument('--levels',nargs='+',type=int,default=[1,2])
    p.add_argument('--tile',type=int,default=1536)
    p.add_argument('--overlap',type=int,default=512)
    p.add_argument('--imgsz',type=int,default=1024)
    p.add_argument('--conf',type=float,default=.35)
    p.add_argument('--batch',type=int,default=2)
    p.add_argument('--decode-workers',type=int,default=min(2,max(0,(os.cpu_count() or 1)-1)),help='Independent native SDK decoder processes; default up to 2, 0 means sequential')
    p.add_argument('--model-label',default='prototype chromosome-cluster detector')
    p.add_argument('--device',default='0')
    p.add_argument('--padding',type=int,default=64,help='L0 pixels surrounding the merged cluster')
    p.add_argument('--roi',nargs=4,type=int,metavar=('X0','Y0','X1','Y1'),help='Optional scan rectangle in L0 pixels')
    p.add_argument('--limit',type=int,default=0,help='Debug cap on tile count; output explicitly marked partial')
    p.add_argument('--processed',action='store_true',help='Also save gently enhanced copies; originals always retained')
    p.add_argument('--resume',action='store_true')
    args=p.parse_args()
    if not 0<=args.overlap<args.tile:p.error('overlap must be nonnegative and less than tile')
    if args.padding<0 or args.batch<1 or args.tile<64 or args.imgsz<32 or args.limit<0 or not 0<=args.decode_workers<=4:p.error('Invalid image sizes, padding, batch, decode-workers or limit')
    if not 0<args.conf<1:p.error('conf must be between zero and one')
    args.slide=args.slide.resolve();args.weights=args.weights.resolve();args.output=args.output.resolve()
    if not args.weights.is_file():raise FileNotFoundError(f'Trained weights missing: {args.weights}')
    if not args.slide.is_file():raise FileNotFoundError(args.slide)
    from ultralytics import YOLO
    model=YOLO(str(args.weights))
    out=args.output;out.mkdir(parents=True,exist_ok=True)
    checkpoint=out/'tiles.jsonl';configfile=out/'run_config.json'
    signature=dict(slide=str(args.slide),slide_bytes=args.slide.stat().st_size,slide_mtime=args.slide.stat().st_mtime_ns,weights_sha256=hashlib.sha256(args.weights.read_bytes()).hexdigest(),levels=args.levels,tile=args.tile,overlap=args.overlap,imgsz=args.imgsz,conf=args.conf,roi=args.roi,limit=args.limit,padding=args.padding,processed=args.processed)
    done=set();detections=[]
    if configfile.exists():
        if not args.resume:raise FileExistsError(f'{out} already contains a run. Choose a new output or --resume.')
        if json.loads(configfile.read_text(encoding='utf-8'))!=signature:raise ValueError('Resume configuration differs from the saved run')
        if checkpoint.exists():
            # Ignore only a final incomplete JSON line from an interrupted write.
            lines=checkpoint.read_text(encoding='utf-8').splitlines()
            valid_lines=[]
            for i,line in enumerate(lines):
                try:r=json.loads(line)
                except json.JSONDecodeError:
                    if i==len(lines)-1:
                        break
                    raise
                valid_lines.append(line)
                done.add(r['tile_key']);detections.extend(r['detections'])
            # Remove a torn final write and ensure a trailing newline even if
            # interruption occurred between the JSON record and its newline.
            checkpoint.write_text(''.join(s+'\n' for s in valid_lines),encoding='utf-8')
    else:configfile.write_text(json.dumps(signature,indent=2),encoding='utf-8')
    started=time.perf_counter();last=0;skipped_blank=0;initial_completed=len(done)
    acceleration=dict(device=args.device,decode_workers=args.decode_workers,batch_size=args.batch)
    print('ACCELERATION '+json.dumps(acceleration),flush=True)
    with Slide(args.slide) as slide:
        w0,h0=slide.dimensions[0]
        roi=args.roi or [0,0,w0,h0]
        if not (0<=roi[0]<roi[2]<=w0 and 0<=roi[1]<roi[3]<=h0):raise ValueError('ROI is outside L0 slide dimensions')
        tiles=[]
        for level in dict.fromkeys(args.levels):
            if level<1 or level>=len(slide.dimensions):raise ValueError(f'Invalid input level {level}; use native pyramid levels L1 and above')
            ds=slide.downsamples[level]
            # Align origins to native pyramid pixels and clamp to both level and L0 bounds.
            x0,y0=int(roi[0]//ds),int(roi[1]//ds)
            x1=min(slide.dimensions[level][0],int(math.ceil(roi[2]/ds)),int(w0//ds))
            y1=min(slide.dimensions[level][1],int(math.ceil(roi[3]/ds)),int(h0//ds))
            width,height=min(args.tile,x1-x0),min(args.tile,y1-y0)
            for y in positions(y1-y0,height,min(args.overlap,height-1)):
                for x in positions(x1-x0,width,min(args.overlap,width-1)):
                    gx,gy=int((x+x0)*ds),int((y+y0)*ds)
                    tiles.append(dict(key=f'L{level}_{gx}_{gy}_{width}_{height}',level=level,origin=[gx,gy],width=width,height=height))
        total_full=len(tiles)
        if args.limit:tiles=tiles[:args.limit]
        total=len(tiles);batch=[]
        def record(rows,fp):
            for r in rows:
                fp.write(json.dumps(r)+'\n');done.add(r['tile_key']);detections.extend(r['detections'])
            fp.flush()
        with checkpoint.open('a',encoding='utf-8') as fp:
            remaining=[t for t in tiles if t['key'] not in done]
            for tile,im in decode_tiles(remaining,slide,args.decode_workers):
                # Only skip genuinely blank white fields, not ordinary cells,
                # dust or pale clusters. All nonblank fields reach the model.
                small=im[::8,::8].astype(np.float32)
                blank=float((small.min(2)>238).mean())>.999 and float(small.std())<3
                if blank:
                    skipped_blank+=1;record([dict(tile_key=tile['key'],detections=[],blank=True)],fp)
                else:batch.append((tile,im))
                if len(batch)>=args.batch:
                    record(detect_batch(model,batch,slide,args),fp);batch=[]
                if time.perf_counter()-last>20:
                    stats=scan_progress(len(done),total,initial_completed,time.perf_counter()-started)
                    print(f'SCAN {len(done)}/{total} tiles; {len(detections)} raw predictions; elapsed {stats["session_seconds"]:.0f}s',flush=True)
                    print('PROGRESS '+json.dumps(stats),flush=True);last=time.perf_counter()
            if batch:record(detect_batch(model,batch,slide,args),fp)
        stats=scan_progress(len(done),total,initial_completed,time.perf_counter()-started)
        print('PROGRESS '+json.dumps(dict(stats,stage='merge',estimated_remaining_seconds=None)),flush=True)
        scan_seconds=time.perf_counter()-started
        print(f'MERGE {len(detections)} raw predictions',flush=True)
        clusters=recover_boundaries(model,merge_detections(detections),slide,args)
        rawdir=out/'L0_original';rawdir.mkdir(exist_ok=True)
        if args.processed:(out/'L0_processed').mkdir(exist_ok=True)
        manifest=[];skipped_exports=[]
        for i,c in enumerate(clusters,1):
            if i==1 or i%50==0:print(f'EXPORT_L0 {i}/{len(clusters)} clusters',flush=True)
            b=c['box_l0'];cw=min(w0,math.ceil(b[2]+args.padding))-max(0,math.floor(b[0]-args.padding));ch=min(h0,math.ceil(b[3]+args.padding))-max(0,math.floor(b[1]-args.padding))
            if cw*ch>60_000_000:
                skipped_exports.append(dict(id=i,box_l0=b,reason='oversized_l0_crop_over_60_megapixels'))
                continue
            image,bounds=slide.crop_l0(c['box_l0'],args.padding)
            name=f'cluster_{i:06}_x{bounds[0]}_y{bounds[1]}_{image.width}x{image.height}.png'
            path=rawdir/name;image.save(path,compress_level=3)
            if args.processed:ImageEnhance.Contrast(image).enhance(1.12).save(out/'L0_processed'/name,compress_level=3)
            recordrow=dict(id=i,file=f'L0_original/{name}',width=image.width,height=image.height,crop_box_l0=bounds,**c)
            manifest.append(recordrow)
        summary=dict(slide=str(args.slide),slide_dimensions_l0=[w0,h0],pyramid_dimensions=slide.dimensions,downsamples=slide.downsamples,scan_roi_l0=roi,levels=args.levels,tiles_completed=len(done),tiles_planned=total,tiles_full_roi=total_full,raw_predictions=len(detections),exported_clusters=len(manifest),partial_run=total<total_full,full_slide_run=total==total_full and roi==[0,0,w0,h0],saved_level=0,resized=False,lossless_png=True,elapsed_seconds=round(time.perf_counter()-started,1),model_status=args.model_label+'; detections require quality review; scores are not calibrated probabilities',blank_tiles_skipped_this_session=skipped_blank)
        summary.update(skipped_exports=skipped_exports,acceleration=acceleration,
                       scan_seconds_this_session=round(scan_seconds,2),
                       session_tiles=len(done)-initial_completed,resumed_tiles=initial_completed,
                       scan_tiles_per_second=stats['tiles_per_second'])
        (out/'detections.json').write_text(json.dumps(dict(summary=summary,clusters=manifest),ensure_ascii=False,indent=2),encoding='utf-8')
        with (out/'coordinates.csv').open('w',encoding='utf-8-sig',newline='') as fp:
            wr=csv.writer(fp);wr.writerow(['id','file','score','box_x0_L0','box_y0_L0','box_x1_L0','box_y1_L0','crop_x0_L0','crop_y0_L0','crop_x1_L0','crop_y1_L0','input_levels','observations','boundary_recovery'])
            for r in manifest:wr.writerow([r['id'],r['file'],r['score'],*r['box_l0'],*r['crop_box_l0'],','.join(map(str,r['levels'])),r['observations'],','.join(r['boundary_recovery'])])
        (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        print('COMPLETE',json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
