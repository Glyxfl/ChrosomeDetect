import copy
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from workbench.service import Service
from workbench.store import Conflict
from workbench.pipeline import validate, classifier

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Service(Path(self.temp.name))
        im = Image.new('RGB', (320, 180), 'white')
        for x in range(20, 300):
            for y in range(20, 160):
                im.putpixel((x, y), (x % 2 * 200, y % 2 * 200, 90))
        b = io.BytesIO(); im.save(b, 'PNG')
        self.project = self.service.upload(b.getvalue(), 'synthetic.png')

    def tearDown(self):
        self.service.pool.shutdown(wait=True)
        self.temp.cleanup()

    def edit(self, stage, rows):
        self.project = self.service.edit(self.project['id'], dict(stage=stage, rows=copy.deepcopy(rows), revision=self.project['revision']))

    def confirm(self, stage):
        self.project = self.service.confirm(self.project['id'], dict(stage=stage, revision=self.project['revision']))

    def prepare_clusters(self):
        self.edit('quality', [dict(id='image', label='clear')]); self.confirm('quality')
        self.edit('clusters', [dict(id=str(i), label='cluster', bbox=[i*100, 20, i*100+80, 120]) for i in range(3)])
        self.confirm('clusters')

    def wait(self, job):
        deadline = time.monotonic() + 10
        while self.service.jobs[job['id']]['status'] in ('queued', 'running') and time.monotonic() < deadline:
            time.sleep(.01)
        job=self.service.jobs[job['id']]
        self.assertEqual(job['status'], 'completed', job)
        self.project = self.service.store.get(self.project['id'])

    def test_gates_bounds_and_revision_conflicts(self):
        with self.assertRaises(Conflict): self.confirm('clusters')
        self.edit('quality', [dict(id='image', label='unusable')])
        self.confirm('quality')
        with self.assertRaises(Conflict): self.confirm('clusters')
        self.edit('quality', [dict(id='image', label='clear')]); self.confirm('quality')
        with self.assertRaises(ValueError): self.edit('clusters', [dict(id='bad', label='cluster', bbox=[-1, 0, 40, 50])])
        with self.assertRaises(Conflict):
            self.service.confirm(self.project['id'],dict(stage='quality',revision=0))

    def test_full_workflow_and_upstream_invalidation(self):
        self.prepare_clusters()
        with self.assertRaises(ValueError): self.confirm('phase')
        self.edit('phase', [dict(id=str(i), label='mi') for i in range(3)])
        self.confirm('phase')
        self.edit('instances',[dict(id='chr'+str(i),cluster_id=str(i),label='chromosome',bbox=[i*100+5,25,i*100+65,100]) for i in range(3)])
        self.confirm('instances')
        self.wait(self.service.run(self.project['id'],dict(stage='outliers',revision=self.project['revision'])))
        self.assertEqual(len(self.project['results']['outliers']), 3)
        self.confirm('outliers')
        self.assertTrue(all(self.project['confirmed'].values()))
        self.edit('clusters', self.project['results']['clusters'][:2])
        self.assertFalse(self.project['confirmed']['phase'])
        self.assertFalse(self.project['confirmed']['outliers'])
        self.assertEqual(self.project['results']['phase'], [])
        self.assertEqual(self.project['results']['outliers'], [])

    def test_prediction_preserves_manual_corrections(self):
        self.prepare_clusters()
        self.edit('phase', [dict(id='0',label='non_mi')])
        rows=[dict(id=str(i),label='mi',source='model') for i in range(3)]
        with patch('workbench.inference.phase', return_value=(rows, 'test-weights')):
            self.wait(self.service.run(self.project['id'],dict(stage='phase',revision=self.project['revision'])))
        self.assertEqual(self.project['results']['phase'][0]['label'], 'non_mi')
        rows=copy.deepcopy(self.project['results']['phase']); rows[1]['label']='non_mi'
        self.edit('phase',rows)
        self.assertEqual(self.project['results']['phase'][2]['source'],'model')

    def test_prediction_cannot_overwrite_concurrent_edit(self):
        self.prepare_clusters()
        entered, resume = threading.Event(), threading.Event()
        def slow(images):
            entered.set(); resume.wait(5)
            return [dict(id=str(i), label='mi', source='model') for i in range(3)], 'test'
        with patch('workbench.inference.phase', side_effect=slow):
            job=self.service.run(self.project['id'],dict(stage='phase',revision=self.project['revision']))
            self.assertTrue(entered.wait(5))
            self.edit('phase',[dict(id='0',label='non_mi')]);resume.set()
            self.service.pool.shutdown(wait=True)
        self.assertEqual(self.service.jobs[job['id']]['status'],'failed')
        current=self.service.store.get(self.project['id'])
        self.assertEqual(current['results']['phase'][0]['label'],'non_mi')

class DatasetTests(unittest.TestCase):
    def test_group_and_duplicate_leakage_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp); rows=[]
            for i,split in enumerate(('train','validation','test')):
                Image.new('RGB',(10,10),(i*50,40,30)).save(folder/f'{i}.png')
                rows.append(dict(image=f'{i}.png',label='mi',split=split,group=str(i),reviewed=True))
            path=folder/'manifest.json'
            path.write_text(json.dumps(rows)); self.assertEqual(len(validate(path,['mi'])),3)
            rows[1]['group']='0';path.write_text(json.dumps(rows))
            with self.assertRaisesRegex(ValueError,'跨数据划分'): validate(path,['mi'])
            rows[1]['group']='1';(folder/'1.png').write_bytes((folder/'0.png').read_bytes());path.write_text(json.dumps(rows))
            with self.assertRaisesRegex(ValueError,'图片内容跨'): validate(path,['mi'])

    def test_classifier_train_and_test_entrypoints(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp); rows=[]
            for i,split in enumerate(('train','validation','test')):
                for j,label in enumerate(('clear','blurry','unusable')):
                    path=folder/f'{i}-{j}.png';Image.new('RGB',(80,80),(40*i+1,60*j+1,30)).save(path)
                    rows.append(dict(image=str(path),label=label,split=split,group=str(i),reviewed=True))
            config=dict(task='quality',classes=['clear','blurry','unusable'],epochs=1,batch=3)
            classifier(config, rows, folder)
            self.assertTrue((folder/'best.pt').is_file())
            classifier(config, rows, folder, folder/'best.pt')
            report=json.loads((folder/'test_report.json').read_text())
            self.assertEqual(report['count'],3)
            self.assertEqual(len(report['confusion_matrix']),3)

if __name__=='__main__': unittest.main()
