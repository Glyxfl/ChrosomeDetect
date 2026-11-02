"""Bounded, lossless native decoding in independent SDK worker processes."""
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from multiprocessing.util import Finalize
from .slide_io import Slide

_worker_slide=None

def _init(path):
    global _worker_slide
    _worker_slide=Slide(path)
    Finalize(None,_worker_slide.close,exitpriority=10)

def _read(tile):
    x,y=tile['origin']
    return _worker_slide.read(x,y,tile['level'],tile['width'],tile['height'])

def decode_tiles(tiles,slide,workers=0):
    if not workers:
        for tile in tiles:
            x,y=tile['origin'];yield tile,slide.read(x,y,tile['level'],tile['width'],tile['height'])
        return
    iterator=iter(tiles)
    with ProcessPoolExecutor(max_workers=workers,initializer=_init,initargs=(str(slide.path),)) as pool:
        pending=deque()
        for _ in range(workers*2):
            tile=next(iterator,None)
            if tile is None:break
            pending.append((tile,pool.submit(_read,tile)))
        while pending:
            tile,future=pending.popleft();image=future.result(timeout=180)
            next_tile=next(iterator,None)
            if next_tile is not None:pending.append((next_tile,pool.submit(_read,next_tile)))
            yield tile,image
