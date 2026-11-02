"""Explicit synthetic-only end-to-end demonstration against a running local server."""
import argparse
import io
import json
import time
import urllib.request
from PIL import Image,ImageDraw

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8767');a=parser.parse_args()
    if not a.url.startswith('http://127.0.0.1:'):parser.error('只允许本机测试服务')
    def api(path,body=None,headers=None):
        if isinstance(body,dict):body=json.dumps(body).encode()
        request=urllib.request.Request(a.url+path,data=body,headers=headers or {})
        with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)
    im=Image.new('RGB',(900,620),'#f5f3f0');draw=ImageDraw.Draw(im)
    origins=[(80,90),(350,230),(650,400)]
    for x,y in origins:
        for i in range(20):draw.ellipse((x+i%5*15,y+i//5*22,x+i%5*15+7,y+i//5*22+15),fill='#65516c')
    buffer=io.BytesIO();im.save(buffer,'PNG')
    p=api('/api/projects',buffer.getvalue(),{'X-Filename':'QA-SYNTHETIC-ONLY.png'})
    base='/api/projects/'+p['id']
    def mutate(action,stage,**extra):
        nonlocal p
        p=api(base+'/'+action,dict(stage=stage,revision=p['revision'],reviewer='QA_SYNTHETIC',reason='仅合成流程演示，不作为真实图像效果',**extra))
    def run(stage):
        nonlocal p
        job=api(base+'/run',dict(stage=stage,revision=p['revision']))
        for _ in range(240):
            job=api('/api/jobs/'+job['id'])
            if job['status'] not in ('queued','running'):break
            time.sleep(.25)
        if job['status']!='completed':raise RuntimeError(job)
        p=api(base)
    mutate('annotations','quality',rows=[dict(p['results']['quality'][0],label='clear')]);mutate('confirm','quality')
    run('clusters')
    mutate('annotations','clusters',rows=[dict(id='demo-cluster-'+str(i+1),label='cluster',bbox=[x-15,y-15,x+95,y+115],note='程序生成的合成区域') for i,(x,y) in enumerate(origins)])
    mutate('confirm','clusters',truth_complete=True)
    run('phase')
    mutate('annotations','phase',rows=[dict(r,label='mi',note='合成演示占位标签，不是生物学真值') for r in p['results']['phase']]);mutate('confirm','phase')
    run('instances');mutate('confirm','instances',truth_complete=True)
    run('outliers')
    rows=p['results']['outliers']
    if rows:
        rows=[dict(r,label='outlier' if i==0 else 'normal',note='合成演示人工标记，不是临床判断') for i,r in enumerate(rows)]
        mutate('annotations','outliers',rows=rows)
    mutate('confirm','outliers')
    metrics=api(base+'/metrics')
    print(json.dumps(dict(project_id=p['id'],synthetic_only=True,instances=len(p['results']['instances']),
                          quality_agreement=metrics['quality'],machine_seconds=metrics['efficiency']['machine_seconds']),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
