"""SDPC ingestion, bounded uploads, isolated scanning and review-project linkage."""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from .config import ROOT,model_paths
from .store import now,Conflict

CHUNK_BYTES=8*1024*1024
MAX_BYTES=int(os.environ.get('MAX_SDPC_BYTES',64*1024**3))

class Slides:
    def __init__(self,service):
        self.service=service;self.store=service.store;self.lock=threading.RLock();self.region_lock=threading.Lock();self.processes={};self.stopping=False
        for slide in self.store.records('slides'):
            if slide['status'] in ('queued','running','preparing_review'):
                slide.update(status='interrupted',error='服务重启中断，可继续扫描');self.store.record('slides',slide)

    def get(self,identifier):
        slide=next((s for s in self.store.records('slides') if s['id']==identifier),None)
        if not slide:raise KeyError('切片不存在')
        return slide

    def summaries(self):
        return [{k:v for k,v in s.items() if k not in ('clusters','source_path','options','project_ids')} for s in self.store.records('slides')]

    def folder(self,identifier):
        self.get(identifier)
        return self.store.root/'slides'/identifier

    @staticmethod
    def filename(name):
        name=str(name).replace('\\','/').split('/')[-1]
        if not name.lower().endswith('.sdpc') or len(name)>200:raise ValueError('原始数据必须为 .sdpc 切片文件')
        return name

    def begin(self,body):
        name=self.filename(body.get('name',''));size=body.get('size')
        if not isinstance(size,int) or isinstance(size,bool) or not 1<=size<=MAX_BYTES:raise ValueError('SDPC 大小无效或超过配置上限')
        root=self.store.root/'slides';root.mkdir(exist_ok=True)
        if shutil.disk_usage(root).free<size+256*1024**2:raise ValueError('磁盘空间不足以接收该 SDPC')
        identifier=uuid.uuid4().hex;folder=root/identifier;folder.mkdir()
        # Readiness lives in SQLite; one file avoids a multi-GB completion copy.
        (folder/'source.sdpc').touch()
        record=dict(id=identifier,name=name,size=size,uploaded_bytes=0,status='uploading',source_mode='upload',created_at=now(),cluster_count=0)
        self.store.record('slides',record)
        return record

    def append(self,identifier,offset,stream,length):
        if not 0<length<=CHUNK_BYTES:raise ValueError('单个分块必须为 1 字节至 8 MiB')
        with self.lock:
            slide=self.get(identifier)
            if slide['status']!='uploading':raise Conflict('该切片不在上传状态')
            path=self.folder(identifier)/'source.sdpc';actual=path.stat().st_size
            if offset!=actual or offset+length>slide['size']:raise Conflict('分块偏移不一致，请读取已接收大小后继续')
            try:
                with path.open('ab') as f:
                    remaining=length
                    while remaining:
                        chunk=stream.read(min(1024*1024,remaining))
                        if not chunk:raise ValueError('上传连接中断，分块未完整接收')
                        f.write(chunk);remaining-=len(chunk)
                    f.flush()
            except Exception:
                with path.open('r+b') as f:f.truncate(actual)
                raise
            slide['uploaded_bytes']=actual+length;self.store.record('slides',slide)
            return dict(id=identifier,uploaded_bytes=slide['uploaded_bytes'],size=slide['size'])

    def upload_status(self,identifier):
        with self.lock:
            slide=self.get(identifier)
            if slide['status']=='uploading':
                slide['uploaded_bytes']=(self.folder(identifier)/'source.sdpc').stat().st_size
            return slide

    def finish(self,identifier):
        with self.lock:
            slide=self.get(identifier)
            if slide['status']!='uploading':return slide
            folder=self.folder(identifier);path=folder/'source.sdpc'
            if path.stat().st_size!=slide['size']:raise ValueError('SDPC 尚未上传完整')
            slide.update(source_path=str(path.resolve()),status='imported',options={})
            self.store.record('slides',slide)
        return self.run(identifier)

    def local(self,body):
        source=Path(str(body.get('path',''))).expanduser().resolve()
        name=self.filename(source.name)
        if not source.is_file():raise ValueError('服务端找不到该 SDPC 文件')
        # Optional ROI/limit are explicit diagnostics, never the default import scope.
        options={k:body[k] for k in ('roi','limit') if k in body}
        if 'roi' in options and (not isinstance(options['roi'],list) or len(options['roi'])!=4 or not all(isinstance(v,int) for v in options['roi'])):raise ValueError('ROI 必须为四个 L0 整数坐标')
        if 'limit' in options and (not isinstance(options['limit'],int) or not 0<=options['limit']<=100000):raise ValueError('调试图块上限无效')
        identifier=uuid.uuid4().hex;(self.store.root/'slides'/identifier).mkdir(parents=True)
        slide=dict(id=identifier,name=name,size=source.stat().st_size,source_path=str(source),source_mode='local_reference',status='imported',created_at=now(),options=options,partial=bool(options.get('roi') or options.get('limit')),cluster_count=0)
        self.store.record('slides',slide)
        return self.run(identifier)

    def invoke(self,slide,command,output,job):
        args=[sys.executable,'-u','-m','workbench.sdpc.worker',command,'--source',slide['source_path'],'--output',str(output)]
        if command=='scan':
            args.extend(['--weights',str(model_paths()['clusters'])])
            for key in ('roi','limit'):
                value=slide.get('options',{}).get(key)
                if value:args.extend(['--'+key,*map(str,value if isinstance(value,list) else [value])])
            if (output/'run_config.json').exists():args.append('--resume')
        log=self.folder(slide['id'])/'scan.log'
        with log.open('a',encoding='utf-8') as f:
            process=subprocess.Popen(args,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.processes[job['id']]=process
            try:
                for line in process.stdout:
                    f.write(line);f.flush()
                    job['progress']=line.strip()[:1000]
                    self.store.record('jobs',job)
                    current=self.get(slide['id']);current['progress']=job['progress'];self.store.record('slides',current)
                code=process.wait()
                if code:raise RuntimeError(f'SDPC {command} 失败（退出码 {code}），详见切片扫描日志')
            finally:
                if process.poll() is None:process.terminate();process.wait(timeout=15)
                self.processes.pop(job['id'],None)

    def run(self,identifier):
        with self.lock:
            slide=self.get(identifier)
            if slide['status'] in ('queued','running','preparing_review'):raise Conflict('该 SDPC 已在处理队列中')
            if slide['status']=='uploading':raise Conflict('请先完成上传')
            if slide['status'] in ('completed','completed_partial','completed_with_issues'):raise Conflict('该切片已完成提取，请在簇项目中复核')
            if not Path(slide['source_path']).is_file():raise ValueError('原始 SDPC 已移动或删除')
            job=dict(id=uuid.uuid4().hex,project_id='slide:'+identifier,slide_id=identifier,stage='sdpc_detection',status='queued',created_at=now())
            self.service.jobs[job['id']]=job;self.store.record('jobs',job)
            slide.update(status='queued',job_id=job['id'],error=None);self.store.record('slides',slide)
        queued=time.perf_counter()
        def work():
            start=time.perf_counter();job.update(status='running',started_at=now(),queue_seconds=start-queued);self.store.record('jobs',job)
            try:
                if self.stopping:raise RuntimeError('服务正在关闭，扫描未执行；可重新提交')
                slide=self.get(identifier);slide['status']='running';self.store.record('slides',slide)
                folder=self.folder(identifier);scan=folder/'detection';scan.mkdir(exist_ok=True)
                if not (folder/'info.json').exists():self.invoke(slide,'inspect',folder,job)
                info=json.loads((folder/'info.json').read_text(encoding='utf-8'))
                slide=self.get(identifier);slide.update(info);self.store.record('slides',slide)
                self.invoke(slide,'scan',scan,job)
                report=json.loads((scan/'detections.json').read_text(encoding='utf-8'))
                slide=self.get(identifier);slide.update(status='preparing_review',summary=report['summary'],cluster_count=len(report['clusters']),clusters=[],project_ids=[])
                if (scan/'run_config.json').is_file():slide['detector_config']=json.loads((scan/'run_config.json').read_text(encoding='utf-8'))
                self.store.record('slides',slide)
                failures=list(report['summary'].get('skipped_exports',[]))
                for index,row in enumerate(report['clusters'],1):
                    try:
                        project_id=uuid.uuid5(uuid.NAMESPACE_URL,identifier+':'+str(row['id'])).hex
                        try:p=self.store.get(project_id)
                        except KeyError:
                            path=(scan/row['file']).resolve()
                            if not path.is_relative_to(scan.resolve()):raise ValueError('检测结果裁图路径无效')
                            p=self.service.import_sdpc_crop(path,slide,row,project_id)
                        slide['clusters'].append(dict(id=row['id'],project_id=p['id'],bbox_l0=row['box_l0'],crop_box_l0=row['crop_box_l0'],score=row['score']))
                        slide['project_ids'].append(p['id'])
                    except Exception as exc:failures.append(dict(cluster_id=row['id'],error=str(exc)))
                    slide['progress']=f'准备复核 {index}/{len(report["clusters"])} 簇'
                    if index==1 or index%25==0:self.store.record('slides',slide)
                slide.update(status='completed_with_issues' if failures else 'completed_partial' if not report['summary']['full_slide_run'] else 'completed',
                             partial=not report['summary']['full_slide_run'],failures=failures,machine_seconds=time.perf_counter()-start,finished_at=now())
                self.store.record('slides',slide);job['status']='completed'
            except Exception as exc:
                job.update(status='failed',error=str(exc));slide=self.get(identifier);slide.update(status='failed',error=str(exc));self.store.record('slides',slide)
            finally:
                job.update(compute_seconds=time.perf_counter()-start,finished_at=now());self.store.record('jobs',job)
        self.service.pool.submit(work)
        return self.get(identifier)

    def stop(self):
        self.stopping=True
        for process in list(self.processes.values()):
            if process.poll() is None:process.terminate()

    def region(self,identifier,box,level):
        slide=self.get(identifier)
        current=Path(slide['source_path']).stat()
        if current.st_size!=slide['size'] or ('source_mtime_ns' in slide and current.st_mtime_ns!=slide['source_mtime_ns']):raise ValueError('原 SDPC 文件发生变化，请重新导入以保持定位可追溯')
        if not isinstance(level,int) or not 0<=level<len(slide.get('downsamples',[])) or len(box)!=4:raise ValueError('原生区域或层级无效')
        x,y,x2,y2=box;w,h=slide['dimensions_l0'];ds=slide['downsamples'][level]
        if not (0<=x<x2<=w and 0<=y<y2<=h) or max((x2-x)/ds,(y2-y)/ds)>4096:raise ValueError('区域越界或超过 4096 原生像素')
        cache=self.folder(identifier)/'regions';cache.mkdir(exist_ok=True)
        name=hashlib.sha256(json.dumps([box,level]).encode()).hexdigest()+'.png';destination=cache/name
        with self.region_lock:
            if not destination.exists():
                args=[sys.executable,'-m','workbench.sdpc.worker','region','--source',slide['source_path'],'--output',str(destination),'--box',*map(str,box),'--level',str(level)]
                result=subprocess.run(args,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if result.returncode:raise ValueError('SDPC 原生区域读取失败：'+result.stderr[-500:])
        return destination
