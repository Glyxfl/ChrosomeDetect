"""Explicit native-SDPC ROI integration check; never silently scan a whole slide."""
import argparse
import json
import time
from pathlib import Path
from PIL import Image
import numpy as np
from workbench.service import Service

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--roi',type=int,nargs=4,required=True)
    p.add_argument('--scan-mode',choices=['full','pyramid'],default='full');a=p.parse_args()
    service=Service(a.output)
    try:
        slide=service.slides.local(dict(path=str(a.source),roi=a.roi,scan_mode=a.scan_mode))
        deadline=time.monotonic()+300
        while service.jobs[slide['job_id']]['status'] in ('queued','running') and time.monotonic()<deadline:time.sleep(.2)
        slide=service.slides.get(slide['id'])
        assert slide['status']=='completed_partial',slide
        assert slide['partial'] is True and slide['summary']['full_slide_run'] is False
        assert slide['project_ids'],'Expected known ROI to contain detections'
        from workbench.sdpc.slide_io import Slide
        with Slide(a.source) as reader:
            for identifier in slide['project_ids']:
                project=service.store.get(identifier);b=project['source_slide']['crop_box_l0']
                raw=reader.read(b[0],b[1],0,b[2]-b[0],b[3]-b[1])
                assert np.array_equal(raw,np.asarray(service.image(project['id']))),'L0 crop pixels changed'
        for identifier in slide['project_ids']:
            project=service.store.get(identifier);offset=project['source_slide']['crop_box_l0']
            row=project['results']['clusters'][0]
            assert row['bbox_l0']==[row['bbox'][0]+offset[0],row['bbox'][1]+offset[1],row['bbox'][2]+offset[0],row['bbox'][3]+offset[1]]
            assert project['predictions']['clusters']['stale'] is False
        print(json.dumps(dict(slide_id=slide['id'],status=slide['status'],l0_dimensions=slide['dimensions_l0'],clusters=slide['cluster_count'],machine_seconds=slide['machine_seconds'],lossless_l0_verified=True,output=str(a.output.resolve())),ensure_ascii=False,indent=2))
    finally:
        service.slides.stop();service.pool.shutdown(wait=True)

if __name__=='__main__':main()
