"""Lazy model adapters. Missing models fail explicitly, never fabricate labels."""
import uuid
import math
import os
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps
from .config import ROOT, model_paths

def quality(image):
    weights = model_paths()['quality']
    if weights.is_file():
        import torch
        from .ml import TinyClassifier, tensor
        checkpoint = torch.load(weights, map_location='cpu', weights_only=False)
        if checkpoint.get('architecture') != 'tiny-v1' or checkpoint.get('task') != 'quality':
            raise ValueError('质量分级权重格式无效，应使用统一 pipeline 训练的 quality 模型')
        labels = checkpoint['classes']
        if labels != ['clear','blurry','unusable']:
            raise ValueError('质量分类类别顺序必须为 clear / blurry / unusable')
        model = TinyClassifier(len(labels))
        model.load_state_dict(checkpoint['state_dict']); model.eval()
        with torch.inference_mode():
            probabilities = model(tensor(image).unsqueeze(0)).softmax(1)[0].tolist()
        index = int(np.argmax(probabilities))
        return dict(id='image', label=labels[index], confidence=probabilities[index],
                    probabilities=dict(zip(labels, probabilities)), source='model', reviewed=False,
                    note='合成数据初始化模型，真实数据效果待校准' if checkpoint.get('bootstrap') else '已配置质量分级模型',
                    bootstrap=checkpoint.get('bootstrap',False), metrics={})
    gray = np.asarray(ImageOps.grayscale(image.resize((512, 512))), dtype=np.float32)
    lap = -4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:]
    sharpness, contrast = float(lap.var()), float(gray.std())
    label = 'unusable' if min(image.size) < 64 or contrast < 3 else ('blurry' if sharpness < 60 else 'clear')
    return dict(id='image', label=label, source='heuristic', reviewed=False,
                metrics=dict(laplacian_variance=sharpness, contrast=contrast),
                note='规则初筛，阈值尚待标注数据校准')

def clusters(image):
    weights = model_paths()['clusters']
    if not weights.is_file():
        raise ValueError('染色体簇模型未就绪，请配置 CLUSTER_WEIGHTS；也可以手工绘制候选框')
    from ultralytics import YOLO
    result = YOLO(str(weights)).predict(image, imgsz=1280, conf=.25, verbose=False)[0]
    rows = []
    for box in result.boxes:
        rows.append(dict(id=uuid.uuid4().hex[:12], bbox=[round(float(v), 2) for v in box.xyxy[0].tolist()],
                         label='cluster', confidence=float(box.conf[0]), source='model', reviewed=False))
    return rows, str(weights)

def phase(images):
    import torch
    from torch import nn
    from ultralytics import YOLO
    weights = model_paths()['phase']
    if not weights.is_file():
        raise ValueError('分裂期模型未就绪，请配置 PHASE_WEIGHTS')
    # Architecture matches mi_annotation/train_mi.py; avoid its dataset side effects.
    from .ml import TinyClassifier, tensor
    checkpoint = torch.load(weights, map_location='cpu', weights_only=False)
    if checkpoint.get('architecture') == 'tiny-v1':
        if checkpoint.get('task')!='phase' or checkpoint.get('classes')!=['mi','non_mi','unreadable']:
            raise ValueError('分裂期权重任务或类别顺序不匹配')
        labels = checkpoint['classes']
        model = TinyClassifier(len(labels))
    else:
        labels = ['mi', 'non_mi', 'unreadable']
        backbone = Path(os.environ.get('MI_BACKBONE_WEIGHTS',ROOT / 'chromosome_detector/weights/yolov8n.pt'))
        if not backbone.is_file():
            raise ValueError('旧版 MI 模型需要 chromosome_detector/weights/yolov8n.pt')
        model = nn.Module()
        model.backbone = nn.Sequential(*list(YOLO(str(backbone)).model.model.children())[:10])
        model.head = nn.Sequential(nn.AdaptiveAvgPool2d(1), nn.Flatten(), nn.Dropout(.25), nn.Linear(256, 3))
        model.forward = lambda x: model.head(model.backbone(x))
    model.load_state_dict(checkpoint['state_dict'])
    model.eval()
    rows = []
    with torch.inference_mode():
        for identifier, image in images:
            probabilities = model(tensor(image).unsqueeze(0)).softmax(1)[0].tolist()
            index = int(np.argmax(probabilities))
            rows.append(dict(id=identifier, label=labels[index], confidence=probabilities[index],
                             probabilities=dict(zip(labels, probabilities)), source='model', reviewed=False))
    return rows, str(weights)

def features(image):
    a = np.asarray(ImageOps.grayscale(ImageOps.pad(image, (64, 64), color='white')), dtype=np.float32) / 255
    return np.array([a.mean(), a.std(), (a < .65).mean(), np.abs(np.diff(a, axis=0)).mean(),
                     np.abs(np.diff(a, axis=1)).mean(), image.width / max(1, image.height)], dtype=np.float64)

def outliers(images):
    if len(images) < 3:
        raise ValueError('离群分析至少需要 3 个已确认、属于目标分裂期且质量可用的染色体簇')
    x = np.stack([features(im) for _, im in images])
    reference = os.environ.get('OUTLIER_WEIGHTS')
    if reference:
        with np.load(Path(reference), allow_pickle=False) as saved:
            center, scale = saved['center'], saved['scale']
        if center.shape != (6,) or scale.shape != (6,) or not np.isfinite(center).all() or not np.isfinite(scale).all() or not (scale > 0).all():
            raise ValueError('离群特征基线权重格式无效')
    else:
        center = np.median(x, axis=0)
        scale = np.maximum(np.median(np.abs(x - center), axis=0) * 1.4826, .01)
    distances = np.sqrt(np.mean(((x - center) / scale) ** 2, axis=1))
    return [dict(id=identifier, label='unknown', score=round(float(distance), 4),
                 rank=1 + int(np.sum(distances > distance)), source='feature_baseline', reviewed=False,
                 note='图像特征离群排序，需人工确认')
            for (identifier, _), distance in zip(images, distances)]

def crop(image, box):
    return image.crop((math.floor(box[0]), math.floor(box[1]), math.ceil(box[2]), math.ceil(box[3])))

def instances(image, cluster_rows):
    """Return chromosome candidate boxes/polygons in ORIGINAL image coordinates."""
    import cv2
    configured = os.environ.get('INSTANCE_WEIGHTS')
    model = None
    if configured:
        if not Path(configured).is_file():
            raise ValueError('INSTANCE_WEIGHTS 文件不存在')
        from ultralytics import YOLO
        model = YOLO(configured)
    rows=[]
    for cluster in cluster_rows:
        box=cluster['bbox']; offset_x,offset_y=math.floor(box[0]),math.floor(box[1])
        im=crop(image,box); candidates=[]
        if model:
            result=model.predict(im,conf=.25,verbose=False)[0]
            for i,b in enumerate(result.boxes):
                polygon=result.masks.xy[i].tolist() if result.masks is not None else None
                candidates.append((b.xyxy[0].tolist(),polygon,float(b.conf[0])))
        else:
            rgb=np.asarray(im);gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
            _,mask=cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
            contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                area=cv2.contourArea(contour)
                if not max(6,im.width*im.height*.00003)<=area<=im.width*im.height*.8:continue
                x,y,w,h=cv2.boundingRect(contour)
                polygon=cv2.approxPolyDP(contour,max(.7,cv2.arcLength(contour,True)*.008),True).reshape(-1,2).tolist()
                candidates.append(([x,y,x+w,y+h],polygon,None))
        for index,(bbox,polygon,confidence) in enumerate(sorted(candidates,key=lambda c:(c[0][1],c[0][0])),1):
            identifier=cluster['id']+'-chr-'+str(index).zfill(3)
            rows.append(dict(id=identifier,cluster_id=cluster['id'],instance_id=identifier,label='chromosome',
                             bbox=[bbox[0]+offset_x,bbox[1]+offset_y,min(image.width,bbox[2]+offset_x),min(image.height,bbox[3]+offset_y)],
                             polygon=[[min(image.width,max(0,x+offset_x)),min(image.height,max(0,y+offset_y))] for x,y in polygon] if polygon else None,
                             source='model' if model else 'connected_components',confidence=confidence or 0,
                             reviewed=False,review_status='pending',note='相连区域可能含多条染色体，需人工拆分复核'))
    return rows, configured or 'connected-components-v1-unvalidated'
