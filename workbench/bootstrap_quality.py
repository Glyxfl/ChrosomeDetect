"""Train an explicitly synthetic quality preannotation bootstrap, never clinical truth."""
import argparse
import json
import random
import shutil
from pathlib import Path
from PIL import Image,ImageDraw,ImageFilter
from .config import DATA
from .pipeline import classifier

def main():
    p=argparse.ArgumentParser();p.add_argument('--epochs',type=int,default=6);p.add_argument('--output',type=Path,default=DATA/'models/quality.pt');a=p.parse_args()
    if a.output.exists():p.error('输出已存在，拒绝覆盖。请指定新 --output')
    import uuid
    folder=DATA/'bootstrap'/uuid.uuid4().hex;folder.mkdir(parents=True)
    rows=[]
    for i in range(60):
        rng=random.Random(9123+i);im=Image.new('RGB',(256,256),(242,240,238));d=ImageDraw.Draw(im)
        for _ in range(rng.randint(30,70)):
            x,y=rng.randrange(230),rng.randrange(230);w,h=rng.randint(4,13),rng.randint(8,24)
            d.ellipse((x,y,x+w,y+h),fill=(rng.randint(45,115),rng.randint(40,100),rng.randint(70,130)))
        split='train' if i<40 else 'validation' if i<50 else 'test'
        variants={'clear':im,'blurry':im.filter(ImageFilter.GaussianBlur(rng.uniform(2.5,5))),
                  'unusable':Image.blend(im.filter(ImageFilter.GaussianBlur(12)),Image.new('RGB',im.size,(244,244,244)),.97)}
        for label,img in variants.items():
            path=folder/f'{i}-{label}.png';img.save(path)
            rows.append(dict(image=str(path),label=label,split=split,group=f'synthetic-{i}',reviewed=False,label_source='procedural_synthetic'))
    config=dict(task='quality',classes=['clear','blurry','unusable'],epochs=a.epochs,batch=12,learning_rate=.001,seed=42,bootstrap=True)
    classifier(config,rows,folder);classifier(config,rows,folder,folder/'best.pt')
    a.output.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(folder/'best.pt',a.output)
    (folder/'manifest.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
    (folder/'NOTICE.txt').write_text('Synthetic-only bootstrap. Metrics on synthetic test images do not establish real-image performance. Replace using human-reviewed quality data.',encoding='utf-8')
    print('SYNTHETIC BOOTSTRAP',a.output,'report',folder/'test_report.json',flush=True)

if __name__=='__main__':main()
