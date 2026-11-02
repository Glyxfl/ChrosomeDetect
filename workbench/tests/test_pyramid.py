import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from workbench.sdpc.pyramid import plan_tiles,select_fine_tiles


class PyramidTests(unittest.TestCase):
    def setUp(self):
        self.slide=SimpleNamespace(dimensions=[(10000,8000),(5000,4000),(2500,2000),(1250,1000)],downsamples=[1,2,4,8])

    def test_plan_covers_native_edges_without_exceeding_source(self):
        tiles=plan_tiles(self.slide,[0,0,10000,8000],[1,2],1536,512)
        for t in tiles:
            ds=self.slide.downsamples[t['level']];x,y=t['origin']
            self.assertLessEqual(x+t['width']*ds,10000)
            self.assertLessEqual(y+t['height']*ds,8000)
        self.assertTrue(any(t['origin'][0]+t['width']*2==10000 for t in tiles if t['level']==1))

    def test_cross_boundary_proposal_keeps_all_intersecting_tiles(self):
        tiles=plan_tiles(self.slide,[0,0,10000,8000],[1],1536,512)
        proposals=[dict(box_l0=[3000,2000,3200,2200])]
        selected=select_fine_tiles(tiles,proposals,self.slide.downsamples,256)
        expected=[t for t in tiles if t['origin'][0]<3456 and t['origin'][0]+3072>2744 and t['origin'][1]<2456 and t['origin'][1]+3072>1744]
        self.assertEqual(selected,expected)
        self.assertGreater(len(selected),1)
        self.assertEqual(len({t['key'] for t in selected}),len(selected))

    def test_empty_coarse_results_never_fabricate_fine_results(self):
        tiles=plan_tiles(self.slide,[0,0,10000,8000],[1,2],1536,512)
        self.assertEqual(select_fine_tiles(tiles,[],self.slide.downsamples,2048),[])
        self.assertLess(len(plan_tiles(self.slide,[0,0,10000,8000],[3],1536,512)),len(tiles))

    def test_invalid_mode_rejected_before_ingestion(self):
        from workbench.slides import Slides
        with self.assertRaises(ValueError):Slides.scan_mode(dict(scan_mode='unknown'))

    def test_batch_and_upload_persist_selected_mode(self):
        from workbench.service import Service
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'input.sdpc';source.write_bytes(b'fixture')
            service=Service(root/'runtime')
            try:
                with patch.object(service.slides,'run',side_effect=lambda id:service.slides.get(id)):
                    batch=service.slides.local_batch(dict(paths=[str(source)],scan_mode='full'))
                slide=service.slides.get(batch['items'][0]['slide_id'])
                self.assertEqual(slide['options']['scan_mode'],'full')
                upload=service.slides.begin(dict(name='upload.sdpc',size=1,scan_mode='pyramid'))
                self.assertEqual(upload['options']['scan_mode'],'pyramid')
            finally:service.pool.shutdown(wait=True)
