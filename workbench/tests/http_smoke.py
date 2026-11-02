"""Isolated loopback HTTP integration check. All records live in a temp folder."""
import http.client
import io
import json
import tempfile
import threading
import time
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
            request('POST',base+'/confirm',dict(stage='quality',revision=0),expected=409)
            request('POST',base+'/confirm',dict(stage='quality',revision=project['revision']),{'Origin':'http://other.example'},403)
            print('HTTP integration OK: upload, confirm gates, annotations, crops, async outliers, export, conflict and origin protection')
        finally:
            client.close();server.shutdown();server.server_close();worker.join();service.pool.shutdown(wait=True)

if __name__=='__main__':main()
