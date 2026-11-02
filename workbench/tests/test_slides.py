import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from workbench.service import Service
from workbench.store import Conflict

class SlideTests(unittest.TestCase):
    def test_local_batch_isolates_failures_and_preserves_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);s=Service(root/'runtime')
            first=root/'first.sdpc';second=root/'second.sdpc';first.write_bytes(b'fake');second.write_bytes(b'fake')
            try:
                with patch.object(s.slides,'invoke',side_effect=RuntimeError('SDK fixture')):
                    batch=s.slides.local_batch(dict(paths=[str(first),str(root/'missing.sdpc'),str(second),str(first)]))
                    s.pool.shutdown(wait=True)
                self.assertEqual((batch['accepted'],batch['failed'],batch['duplicates']),(2,1,1))
                self.assertEqual(len(s.store.records('slides')),2)
                self.assertEqual(s.slides.import_batch(batch['id'])['accepted'],2)
                self.assertEqual(len([j for j in s.jobs.values() if j['stage']=='sdpc_detection']),2)
                for identifier in (r['slide_id'] for r in batch['items'] if r.get('slide_id')):
                    self.assertEqual(s.slides.get(identifier)['status'],'failed')
            finally:s.pool.shutdown(wait=True)

    def test_chunk_offsets_incomplete_and_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            s=Service(Path(folder))
            try:
                with self.assertRaises(ValueError):s.slides.begin(dict(name='ordinary.png',size=10))
                upload=s.slides.begin(dict(name='example.sdpc',size=10))
                s.slides.append(upload['id'],0,io.BytesIO(b'12345'),5)
                with self.assertRaises(Conflict):s.slides.append(upload['id'],0,io.BytesIO(b'12345'),5)
                with self.assertRaises(ValueError):s.slides.finish(upload['id'])
                with self.assertRaises(ValueError):s.slides.append(upload['id'],5,io.BytesIO(b'1'),5)
                self.assertEqual(s.slides.upload_status(upload['id'])['uploaded_bytes'],5)
            finally:s.pool.shutdown(wait=True)

    def test_finish_automatically_scans_and_links_coordinates(self):
        with tempfile.TemporaryDirectory() as folder:
            s=Service(Path(folder))
            def invoke(slide,command,output,job):
                if command=='inspect':
                    output.mkdir(exist_ok=True)
                    (output/'info.json').write_text(json.dumps(dict(dimensions_l0=[1000,800],source_sha256='test',overview_extent_l0=[0,0,1000,800])))
                    Image.new('RGB',(100,80),'white').save(output/'overview.jpg')
                else:
                    (output/'L0_original').mkdir(exist_ok=True);Image.new('RGB',(100,80),'gray').save(output/'L0_original/c.png')
                    (output/'detections.json').write_text(json.dumps(dict(summary=dict(full_slide_run=True),clusters=[dict(id=1,file='L0_original/c.png',score=.9,box_l0=[110,210,180,260],crop_box_l0=[100,200,200,280])])) )
            try:
                with patch.object(s.slides,'invoke',side_effect=invoke):
                    upload=s.slides.begin(dict(name='sample.sdpc',size=8));s.slides.append(upload['id'],0,io.BytesIO(b'SDPCtest'),8)
                    slide=s.slides.finish(upload['id']);deadline=time.monotonic()+20
                    while s.jobs[slide['job_id']]['status'] in ('queued','running') and time.monotonic()<deadline:time.sleep(.02)
                slide=s.slides.get(slide['id']);self.assertEqual(slide['status'],'completed')
                project=s.store.get(slide['project_ids'][0]);self.assertEqual(project['source_slide']['id'],slide['id'])
                row=project['results']['clusters'][0];self.assertEqual(row['bbox'],[10,10,80,60]);self.assertEqual(row['bbox_l0'],[110,210,180,260])
                project=s.edit(project['id'],dict(stage='quality',rows=[dict(id='image',label='clear')],revision=project['revision']))
                self.assertFalse(project['predictions']['clusters']['stale'])
                self.assertFalse(project['confirmed']['quality'])
            finally:s.slides.stop();s.pool.shutdown(wait=True)

    def test_vendor_failure_preserves_uploaded_source_for_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            s=Service(Path(folder))
            try:
                with patch.object(s.slides,'invoke',side_effect=RuntimeError('SDK decode failed')):
                    upload=s.slides.begin(dict(name='bad.sdpc',size=3));s.slides.append(upload['id'],0,io.BytesIO(b'bad'),3)
                    slide=s.slides.finish(upload['id']);s.pool.shutdown(wait=True)
                self.assertEqual(s.slides.get(slide['id'])['status'],'failed')
                self.assertTrue(Path(s.slides.get(slide['id'])['source_path']).is_file())
            finally:s.pool.shutdown(wait=True)

if __name__=='__main__':unittest.main()
