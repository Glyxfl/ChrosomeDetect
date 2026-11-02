"""Resumable low-resolution proposals; never exported as final clusters."""
import copy
import json
import time
from .native_decode import decode_tiles
from .progress import scan_progress


def coarse_proposals(model, slide, args, tiles, predict):
    path=args.output/'coarse_tiles.jsonl';done=set();proposals=[]
    if path.exists():
        lines=path.read_text(encoding='utf-8').splitlines();valid=[]
        for i,line in enumerate(lines):
            try:row=json.loads(line)
            except json.JSONDecodeError:
                if i==len(lines)-1:break
                raise
            valid.append(line);done.add(row['tile_key']);proposals.extend(row['detections'])
        path.write_text(''.join(line+'\n' for line in valid),encoding='utf-8')
    initial=len(done);started=time.perf_counter();last=0;batch=[]
    params=copy.copy(args);params.conf=args.coarse_conf
    def progress():
        return dict(scan_progress(len(done),len(tiles),initial,time.perf_counter()-started),stage='coarse',
                    estimate_scope='coarse_only_excludes_fine_scan_merge_export_review')
    with path.open('a',encoding='utf-8') as fp:
        def flush():
            for row in predict(model,batch,slide,params):
                fp.write(json.dumps(row)+'\n');done.add(row['tile_key']);proposals.extend(row['detections'])
            fp.flush();batch.clear()
        for tile,image in decode_tiles([t for t in tiles if t['key'] not in done],slide,args.decode_workers):
            batch.append((tile,image))
            if len(batch)>=args.batch:flush()
            if time.perf_counter()-last>20:
                print('PROGRESS '+json.dumps(progress()),flush=True);last=time.perf_counter()
        if batch:flush()
    stats=progress()
    print('COARSE_COMPLETE '+json.dumps(dict(stats,proposals=len(proposals))),flush=True)
    return proposals,stats
