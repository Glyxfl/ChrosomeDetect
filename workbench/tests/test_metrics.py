import copy
import io
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from workbench.metrics import detection, evaluate, aggregate
from workbench.service import Service
from workbench.exporting import bundle,overlay
from workbench.store import upgrade

class MetricTests(unittest.TestCase):
    def test_detection_one_to_one_and_missed_targets(self):
        predicted=[dict(id='a',bbox=[0,0,20,20],confidence=.9),dict(id='duplicate',bbox=[0,0,20,20],confidence=.8),dict(id='wrong',bbox=[80,80,95,95])]
        truth=[dict(id='x',bbox=[0,0,20,20]),dict(id='missed',bbox=[40,40,60,60])]
        r=detection(predicted,truth)
        self.assertEqual((r['tp'],r['fp'],r['fn']),(1,2,1));self.assertEqual(r['precision'],1/3);self.assertEqual(r['recall'],.5)
        self.assertIsNone(detection([],[])['recall'])

    def test_quality_agreement_uses_raw_model_snapshot(self):
        p=upgrade(dict(id='test',stage='quality',results={'quality':[dict(id='image',label='blurry',reviewed=True,review_status='corrected')]},confirmed={'quality':True}))
        p['predictions']['quality']=dict(rows=[dict(id='image',label='clear')],source='model',bootstrap=True)
        self.assertEqual(evaluate(p)['quality']['agreement'],0)
        self.assertEqual(aggregate([p])['quality_model_agreement']['reviewed'],1)
        p['predictions']['quality']['stale']=True
        self.assertIsNone(evaluate(p)['quality'])
        self.assertFalse(evaluate(p)['instances']['available'])

    def test_instance_coordinates_reference_original(self):
        from workbench.inference import instances
        from PIL import ImageDraw
        image=Image.new('RGB',(300,200),'white');d=ImageDraw.Draw(image);d.rectangle([110,90,120,120],fill='black')
        rows,_=instances(image,[dict(id='parent',bbox=[100,80,180,160])])
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['cluster_id'],'parent')
        self.assertEqual(rows[0]['bbox'],[110,90,121,121])
        self.assertTrue(all(110<=x<=121 and 90<=y<=121 for x,y in rows[0]['polygon']))

    def test_batch_persistence_review_and_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);s=Service(root)
            try:
                b=io.BytesIO();Image.new('RGB',(128,128),'white').save(b,'PNG')
                with patch('workbench.inference.quality',return_value=dict(id='image',label='clear',source='model',bootstrap=True)):
                    p=s.upload(b.getvalue(),'synthetic.png')
                    batch=s.batch(dict(stage='quality',project_ids=[p['id']]))
                    deadline=time.monotonic()+10
                    while s.batch_status(batch['id'])['status']=='running' and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(s.batch_status(batch['id'])['status'],'completed')
                p=s.store.get(p['id'])
                p=s.review(p['id'],dict(stage='quality',row_id='image',decision='correct',label='blurry',reason='合成测试',reviewer='tester',revision=p['revision']))
                p=s.confirm(p['id'],dict(stage='quality',revision=p['revision']))
                self.assertEqual(evaluate(p)['quality']['agreement'],0)
                self.assertEqual(p['results']['quality'][0]['reviewer'],'tester')
                with bundle(s,[p['id']]) as f,zipfile.ZipFile(f) as z:
                    self.assertIn(p['id']+'/audit.json',z.namelist())
                    saved=json.loads(z.read(p['id']+'/result.json'))
                    self.assertEqual(saved['predictions']['quality']['rows'][0]['label'],'clear')
                s.pool.shutdown(wait=True);s2=Service(root)
                try:self.assertEqual(s2.batch_status(batch['id'])['status'],'completed')
                finally:s2.pool.shutdown(wait=True)
            finally:s.pool.shutdown(wait=True)

if __name__=='__main__':unittest.main()
