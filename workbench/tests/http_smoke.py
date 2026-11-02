"""Isolated loopback HTTP integration check. All records live in a temp folder."""
import http.client
import io
import json
import tempfile
import threading
import time
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from pathlib import Path
from PIL import Image
from workbench.server import handler
from workbench.service import Service

def main():
    with tempfile.TemporaryDirectory() as temporary:
        service = Service(Path(temporary))
        server = ThreadingHTTPServer(('127.0.0.1',0),handler(service))
        worker = threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        client = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=15)
        def request(method,path,body=None,headers=None,expected=200):
            if isinstance(body,dict):body=json.dumps(body).encode()
            client.request(method,path,body,headers or {})
            response=client.getresponse(); data=response.read()
            assert response.status==expected,(response.status,data)
            return json.loads(data) if response.getheader('Content-Type','').startswith('application/json') else data
        try:
            assert b'CHROMOSOME' in request('GET','/')
            assert request('GET','/api/health')['status']=='ok'
            image=Image.new('RGB',(600,200),'white')
            for x in range(600):
                for y in range(200):image.putpixel((x,y),(x%2*180,y%3*70,80))
            buffer=io.BytesIO();image.save(buffer,'PNG')
            project=request('POST','/api/projects',buffer.getvalue(),{'X-Filename':'http-synthetic-test.png'},201)
            base='/api/projects/'+project['id']
            def mutate(action,stage,**extra):
                nonlocal project
                project=request('POST',base+'/'+action,dict(stage=stage,revision=project['revision'],**extra))
            mutate('annotations','quality',rows=[dict(id='image',label='clear')])
            mutate('confirm','quality')
            mutate('annotations','clusters',rows=[dict(id=str(i),bbox=[i*190+10,20,i*190+130,160],label='cluster') for i in range(3)])
            assert request('GET',base+'/crops/0').startswith(b'\x89PNG')
            mutate('confirm','clusters')
            mutate('annotations','phase',rows=[dict(id=str(i),label='mi') for i in range(3)])
            mutate('confirm','phase')
            mutate('annotations','instances',rows=[dict(id='chr'+str(i),cluster_id=str(i),bbox=[i*190+20,30,i*190+100,150],label='chromosome') for i in range(3)])
            mutate('confirm','instances',truth_complete=True)
            job=request('POST',base+'/run',dict(stage='outliers',revision=project['revision']),expected=202)
            for _ in range(100):
                job=request('GET','/api/jobs/'+job['id'])
                if job['status'] not in ('queued','running'):break
                time.sleep(.02)
            assert job['status']=='completed',job
            project=request('GET',base)
            assert len(project['results']['outliers'])==3
            mutate('annotations','outliers',rows=[dict(r,label='normal') for r in project['results']['outliers']])
            mutate('confirm','outliers')
            exported=request('GET',base+'/export')
            assert all(exported['confirmed'].values())
            assert request('GET',base+'/overlay').startswith(b'\x89PNG')
            import zipfile
            with zipfile.ZipFile(io.BytesIO(request('GET',base+'/bundle'))) as archive:
                assert 'aggregate_metrics.json' in archive.namelist()
                assert project['id']+'/annotated.png' in archive.namelist()
                assert project['id']+'/audit.json' in archive.namelist()
            assert request('GET','/api/metrics')['projects']==1
            assert request('GET','/api/metrics?source=sdpc')['projects']==0
            # Native decoding is covered separately by the real-SDPC ROI smoke.
            # Here exercise streaming HTTP ingestion and automatic queue wiring.
            def fake_native(slide,command,output,job):
                if command=='inspect':
                    (output/'info.json').write_text(json.dumps(dict(dimensions_l0=[1000,800],source_sha256='http-fixture',overview_extent_l0=[0,0,1000,800])))
                    Image.new('RGB',(100,80),'white').save(output/'overview.jpg')
                else:
                    (output/'L0_original').mkdir(exist_ok=True)
                    Image.new('RGB',(100,80),'gray').save(output/'L0_original/c.png')
                    (output/'detections.json').write_text(json.dumps(dict(summary=dict(full_slide_run=True),clusters=[dict(id=1,file='L0_original/c.png',score=.9,box_l0=[110,210,180,260],crop_box_l0=[100,200,200,280])])))
            with patch.object(service.slides,'invoke',side_effect=fake_native):
                upload=request('POST','/api/slides/uploads',dict(name='http-fixture.sdpc',size=11),expected=201)
                up='/api/slides/uploads/'+upload['id']
                request('POST',up+'/chunk',b'SDPCp',{'X-Offset':'0'})
                request('POST',up+'/finish',{},expected=400)
                request('POST',up+'/chunk',b'ayload',{'X-Offset':'5'})
                source=request('POST',up+'/finish',{},expected=202)
                for _ in range(300):
                    state=request('GET','/api/jobs/'+source['job_id'])
                    if state['status'] not in ('queued','running'):break
                    time.sleep(.02)
                assert state['status']=='completed',state
            source=request('GET','/api/slides/'+source['id'])
            assert source['cluster_count']==1 and source['status']=='completed'
            assert request('GET','/api/metrics?source=sdpc')['projects']==1
            assert len(request('GET','/api/projects?source=sdpc'))==1
            assert request('GET','/api/slides/'+source['id']+'/source',headers={'Range':'bytes=0-3'},expected=206)==b'SDPC'
            child=request('GET','/api/projects/'+source['project_ids'][0])
            assert child['results']['clusters'][0]['bbox_l0']==[110,210,180,260]
            with zipfile.ZipFile(io.BytesIO(request('GET','/api/projects/'+child['id']+'/bundle'))) as archive:
                assert child['id']+'/source_sdpc.json' in archive.namelist()
                assert child['id']+'/slide_overview.jpg' in archive.namelist()
            request('POST',base+'/confirm',dict(stage='quality',revision=0),expected=409)
            request('POST',base+'/confirm',dict(stage='quality',revision=project['revision']),{'Origin':'http://other.example'},403)
            print('HTTP integration OK: SDPC chunks, automatic scanning, L0 linkage, source Range, five review stages, metrics and traceable ZIP export')
        finally:
            client.close();server.shutdown();server.server_close();worker.join();service.pool.shutdown(wait=True)

if __name__=='__main__':main()
