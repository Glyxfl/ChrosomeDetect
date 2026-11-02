"""Run vendor SDK operations in a child process, never in HTTP worker threads."""
import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from PIL import Image
from .slide_io import Slide

def inspect(source, output):
    output.mkdir(parents=True,exist_ok=True)
    before=source.stat()
    with Slide(source) as slide:
        level=next((i for i,(w,h) in enumerate(slide.dimensions) if max(w,h)<=2048),len(slide.dimensions)-1)
        ds=float(slide.downsamples[level]);w0,h0=map(int,slide.dimensions[0])
        w=min(int(slide.dimensions[level][0]),int(w0//ds));h=min(int(slide.dimensions[level][1]),int(h0//ds))
        if max(w,h)>8192:raise ValueError('最小金字塔层仍过大，无法生成安全预览')
        image=Image.fromarray(slide.read(0,0,level,w,h));image.thumbnail((2048,2048));image.save(output/'overview.jpg',quality=88)
        sha=hashlib.sha256()
        with source.open('rb') as f:
            for block in iter(lambda:f.read(4*1024*1024),b''):sha.update(block)
        if source.stat().st_mtime_ns!=before.st_mtime_ns or source.stat().st_size!=before.st_size:raise ValueError('SDPC 在导入过程中发生变化，请重新导入')
        info=dict(dimensions_l0=[w0,h0],pyramid_dimensions=[[int(w),int(h)] for w,h in slide.dimensions],
                  downsamples=[float(v) for v in slide.downsamples],overview_level=level,
                  overview_extent_l0=[0,0,w*ds,h*ds],source_sha256=sha.hexdigest(),source_mtime_ns=before.st_mtime_ns)
        (output/'info.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')

def region(source, output, box, level):
    with Slide(source) as slide:
        if not 0<=level<len(slide.dimensions):raise ValueError('金字塔层无效')
        x,y,x2,y2=box;w0,h0=slide.dimensions[0];ds=slide.downsamples[level]
        if not (0<=x<x2<=w0 and 0<=y<y2<=h0):raise ValueError('区域超出切片')
        width=math.ceil((x2-x)/ds);height=math.ceil((y2-y)/ds)
        if max(width,height)>4096:raise ValueError('原生区域单边不得超过 4096 像素')
        width=min(width,int((w0-x)//ds));height=min(height,int((h0-y)//ds))
        Image.fromarray(slide.read(x,y,level,width,height)).save(output)

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['inspect','scan','region']);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--weights',type=Path);p.add_argument('--roi',nargs=4,type=int);p.add_argument('--limit',type=int,default=0)
    p.add_argument('--resume',action='store_true');p.add_argument('--box',nargs=4,type=int);p.add_argument('--level',type=int,default=0)
    a=p.parse_args()
    if a.command=='inspect':inspect(a.source,a.output)
    elif a.command=='region':region(a.source,a.output,a.box,a.level)
    else:
        import torch
        from .detect_slide import main as detect
        # Separate SDK handles in bounded worker processes overlap decode and GPU
        # inference. Keep batch 2 on 4 GiB cards; do not reduce scan coverage.
        workers=int(os.environ.get('SDPC_DECODE_WORKERS',min(2,max(0,(os.cpu_count() or 1)-1))))
        batch=int(os.environ.get('SDPC_BATCH_SIZE','2'))
        sys.argv=['sdpc-detect',str(a.source),'--output',str(a.output),'--weights',str(a.weights),'--device','0' if torch.cuda.is_available() else 'cpu',
                  '--decode-workers',str(workers),'--batch',str(batch)]
        if a.roi:sys.argv.extend(['--roi',*map(str,a.roi)])
        if a.limit:sys.argv.extend(['--limit',str(a.limit)])
        if a.resume:sys.argv.append('--resume')
        detect()

if __name__=='__main__':main()
