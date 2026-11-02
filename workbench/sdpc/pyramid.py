"""Native pyramid planning and conservative L0 proposal-to-tile mapping."""
import math


def positions(length, tile, overlap):
    if length <= tile:return [0]
    p=list(range(0,length-tile+1,tile-overlap))
    if p[-1]!=length-tile:p.append(length-tile)
    return p


def plan_tiles(slide, roi, levels, tile_size, overlap):
    w0,h0=slide.dimensions[0];tiles=[]
    for level in dict.fromkeys(levels):
        if level<1 or level>=len(slide.dimensions):raise ValueError(f'Invalid native pyramid level {level}')
        ds=slide.downsamples[level]
        x0,y0=int(roi[0]//ds),int(roi[1]//ds)
        x1=min(slide.dimensions[level][0],math.ceil(roi[2]/ds),int(w0//ds))
        y1=min(slide.dimensions[level][1],math.ceil(roi[3]/ds),int(h0//ds))
        width,height=min(tile_size,x1-x0),min(tile_size,y1-y0)
        for y in positions(y1-y0,height,min(overlap,height-1)):
            for x in positions(x1-x0,width,min(overlap,width-1)):
                gx,gy=int((x+x0)*ds),int((y+y0)*ds)
                tiles.append(dict(key=f'L{level}_{gx}_{gy}_{width}_{height}',level=level,origin=[gx,gy],width=width,height=height))
    return tiles


def select_fine_tiles(tiles, proposals, downsamples, padding):
    # Sparse spatial bins keep whole-slide selection bounded in typical cases.
    grid={};cell=8192
    for row in proposals:
        b=row['box_l0'];b=[max(0,b[0]-padding),max(0,b[1]-padding),b[2]+padding,b[3]+padding]
        for y in range(int(b[1]//cell),int(b[3]//cell)+1):
            for x in range(int(b[0]//cell),int(b[2]//cell)+1):grid.setdefault((x,y),[]).append(b)
    selected=[]
    for tile in tiles:
        x,y=tile['origin'];ds=downsamples[tile['level']]
        right,bottom=x+tile['width']*ds,y+tile['height']*ds
        if any(b[0]<right and b[2]>x and b[1]<bottom and b[3]>y
               for gy in range(int(y//cell),int(bottom//cell)+1)
               for gx in range(int(x//cell),int(right//cell)+1) for b in grid.get((gx,gy),[])):
            selected.append(tile)
    return selected
