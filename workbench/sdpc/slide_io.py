"""Read native SDPC pyramid regions in RGB, with coordinates always in L0 pixels."""
from . import runtime
import importlib
import importlib.util
import os
import ctypes
from pathlib import Path

import numpy as np
from PIL import Image

_DLL_DIR = None

class Slide:
    def __init__(self, path):
        global _DLL_DIR
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        # Vendor sdpc library changes cwd on import; restore it immediately.
        spec = importlib.util.find_spec('sdpc')
        if spec is None: raise RuntimeError('SDPC SDK missing: install sdpc-for-python==1.0.5')
        dll = Path(next(iter(spec.submodule_search_locations))) / 'WINDOWS/dll'
        if os.name == 'nt' and dll.exists() and _DLL_DIR is None:
            _DLL_DIR = os.add_dll_directory(str(dll))
        cwd = os.getcwd()
        try:
            mod = importlib.import_module('sdpc.Sdpc')
        finally:
            os.chdir(cwd)
        # Avoid the upstream constructor opening the same file three times.
        self._reader = mod.Sdpc.__new__(mod.Sdpc)
        self._reader.sdpcPath = str(self.path)
        # Upstream readSdpc replaces the SDK-owned filename pointer with a
        # Python bytes pointer, causing an invalid free when closing on Windows.
        # Let the SDK own its original filename allocation instead.
        mod.so.SqOpenSdpc.argtypes = [ctypes.c_char_p]
        mod.so.SqCloseSdpc.argtypes = [ctypes.POINTER(mod.SqSdpcInfo)]
        mod.so.SqCloseSdpc.restype = ctypes.c_int
        self._encoded_path = str(self.path).encode('gbk' if os.name=='nt' else 'utf-8')
        self._reader.sdpc = mod.so.SqOpenSdpc(self._encoded_path)
        if not self._reader.sdpc:
            raise ValueError(f'Cannot decode SDPC file: {self.path}')
        self._reader.level_count = self._reader.getLevelCount()
        self._reader.level_downsamples = self._reader.getLevelDownsamples()
        self._reader.level_dimensions = self._reader.getLevelDimensions()
        self.dimensions = self._reader.level_dimensions
        self.downsamples = self._reader.level_downsamples
        self._native = mod.so
        self._native.Dispose.argtypes = [ctypes.POINTER(ctypes.c_uint8)]
        self._native.Dispose.restype = None
        self.closed = False

    def read(self, x, y, level, width, height):
        """Return a native-level RGB array; requests must stay within the slide."""
        x,y,width,height = int(x),int(y),int(width),int(height)
        ds = self.downsamples[level]
        w,h = self.dimensions[0]
        if min(x,y)<0 or min(width,height)<=0 or x+width*ds>w or y+height*ds>h:
            raise ValueError(f'Region out of bounds: {x,y,level,width,height}')
        # Decode directly and release the native buffer immediately. The
        # upstream wrapper forces a full Python gc on every tile, which is
        # unnecessary here and costly during a multi-thousand-tile scan.
        buffer = ctypes.POINTER(ctypes.c_uint8)()
        self._native.SqGetRoiRgbOfSpecifyLayer(self._reader.sdpc,ctypes.byref(buffer),width,height,int(x/ds),int(y/ds),level)
        if not buffer:raise RuntimeError(f'SDPC region decode failed: {x,y,level,width,height}')
        try:
            return np.ctypeslib.as_array(buffer,(height,width,3))[:,:,::-1].copy()
        finally:self._native.Dispose(buffer)

    def crop_l0(self, box, padding=64):
        import math
        w,h = self.dimensions[0]
        x0,y0,x1,y1 = box
        bounds = [max(0,math.floor(x0-padding)),max(0,math.floor(y0-padding)),min(w,math.ceil(x1+padding)),min(h,math.ceil(y1+padding))]
        x0,y0,x1,y1 = bounds
        return Image.fromarray(self.read(x0,y0,0,x1-x0,y1-y0)), bounds

    def close(self):
        if not self.closed:
            self._reader.close()
            self.closed = True

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
