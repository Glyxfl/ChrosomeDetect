"""Explicit train/validation/test contracts. Does not consume existing annotations."""
import argparse
import hashlib
import json
import random
from pathlib import Path
from .config import ROOT

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def validate(path, classes=None, detection=False):
    path = Path(path).resolve()
    rows = read(path)
    if not isinstance(rows, list) or not rows:
        raise ValueError('数据清单必须是非空数组；请先准备人工确认数据')
    groups, hashes, files = {}, {}, set()
    for row in rows:
        if not {'image','label','split','group','reviewed'} <= row.keys():
            raise ValueError('数据项缺少 image/label/split/group/reviewed')
        if row['split'] not in ('train','validation','test') or row['reviewed'] is not True or not row['group']:
            raise ValueError('只允许人工确认数据，且必须指定有效 split 和 group')
        image = (path.parent / row['image']).resolve()
        if not image.is_file() or image in files:
            raise ValueError(f'图片不存在或重复: {image}')
        files.add(image)
        row['image'] = str(image)
        group = str(row['group'])
        if group in groups and groups[group] != row['split']:
            raise ValueError('同一切片/患者组跨数据划分: ' + group)
        groups[group] = row['split']
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        if digest in hashes and hashes[digest] != row['split']:
            raise ValueError('同一图片内容跨数据划分')
        hashes[digest] = row['split']
        if detection:
            label = (path.parent / row['label']).resolve()
            if not label.is_file():
                raise ValueError('YOLO 标签文件不存在: ' + str(label))
            for line in label.read_text(encoding='utf-8').splitlines():
                values = list(map(float, line.split()))
                if len(values) != 5 or int(values[0]) != values[0] or not 0 <= int(values[0]) < len(classes) or not all(0 <= v <= 1 for v in values[1:]) or min(values[3:]) <= 0:
                    raise ValueError('无效 YOLO 标签: ' + str(label))
            row['label'] = str(label)
        elif classes and row['label'] not in classes:
            raise ValueError('未知分类标签: ' + str(row['label']))
    if {r['split'] for r in rows} != {'train','validation','test'}:
        raise ValueError('必须包含 train、validation、test 三个独立划分')
    return rows

def classification_report(y, predictions, classes):
    matrix = [[0] * len(classes) for _ in classes]
    for actual, predicted in zip(y, predictions):
        matrix[actual][predicted] += 1
    scores = []
    for i, label in enumerate(classes):
        tp = matrix[i][i]
        precision = tp / max(1, sum(row[i] for row in matrix))
        recall = tp / max(1, sum(matrix[i]))
        scores.append(dict(label=label, precision=precision, recall=recall, f1=2*precision*recall/max(1e-12,precision+recall), support=sum(matrix[i])))
    return dict(count=len(y), accuracy=sum(matrix[i][i] for i in range(len(classes)))/max(1,len(y)),
                macro_f1=sum(s['f1'] for s in scores)/len(classes), confusion_matrix=matrix, classes=scores)

def classifier(config, rows, output, weights=None):
    import torch
    from PIL import Image
    from .ml import TinyClassifier, tensor
    torch.manual_seed(config.get('seed',42)); random.seed(config.get('seed',42))
    torch.set_num_threads(4)
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    classes = config['classes']
    model = TinyClassifier(len(classes)).to(device)
    def batch(selected):
        images = []
        for row in selected:
            with Image.open(row['image']) as im:
                images.append(tensor(im))
        return torch.stack(images).to(device), torch.tensor([classes.index(r['label']) for r in selected],device=device)
    def evaluate(split):
        model.eval(); y=[]; pred=[]
        selected=[r for r in rows if r['split']==split]
        with torch.inference_mode():
            for start in range(0,len(selected),config.get('batch',16)):
                x,t=batch(selected[start:start+config.get('batch',16)])
                y.extend(t.tolist());pred.extend(model(x).argmax(1).tolist())
        return classification_report(y,pred,classes)
    if weights:
        ckpt=torch.load(weights,map_location=device,weights_only=False)
        if ckpt.get('architecture') != 'tiny-v1' or ckpt['classes'] != classes or ckpt['task'] != config['task']:
            raise ValueError('权重架构、任务或类别顺序与配置不一致')
        model.load_state_dict(ckpt['state_dict'])
        write(output/'test_report.json',evaluate('test'))
        return
    training=[r for r in rows if r['split']=='train']
    for split in ('train','validation'):
        if {r['label'] for r in rows if r['split']==split} != set(classes):
            raise ValueError('训练及验证集必须覆盖所有类别: '+split)
    optimizer=torch.optim.AdamW(model.parameters(),lr=config.get('learning_rate',.001))
    criterion=torch.nn.CrossEntropyLoss(); best=-1;history=[]
    for epoch in range(config.get('epochs',30)):
        random.shuffle(training);model.train(); losses=[]
        for start in range(0,len(training),config.get('batch',16)):
            x,y=batch(training[start:start+config.get('batch',16)])
            optimizer.zero_grad();loss=criterion(model(x),y);loss.backward();optimizer.step();losses.append(loss.item())
        report=evaluate('validation');score=report['macro_f1']
        history.append(dict(epoch=epoch+1,loss=sum(losses)/len(losses),validation=report))
        if score>best:
            best=score
            torch.save(dict(architecture='tiny-v1',task=config['task'],classes=classes,state_dict=model.cpu().state_dict(),bootstrap=config.get('bootstrap',False)),output/'best.pt')
            model.to(device)
        write(output/'history.json',history)
        print(f'epoch={epoch+1} validation_macro_f1={score:.4f}',flush=True)

def detector(config, rows, output, weights=None):
    import shutil
    import yaml
    from ultralytics import YOLO
    # Materialize a unique run-local dataset, preserving the source files.
    for split in ('train','validation','test'):
        for kind in ('images','labels'):
            (output/'dataset'/kind/split).mkdir(parents=True)
    for i,row in enumerate(rows):
        image=Path(row['image']); name=f'{i:08d}'
        shutil.copy2(image,output/'dataset/images'/row['split']/(name+image.suffix))
        shutil.copy2(row['label'],output/'dataset/labels'/row['split']/(name+'.txt'))
    data=dict(path=str((output/'dataset').resolve()),train='images/train',val='images/validation',test='images/test',names=config['classes'])
    datafile=output/'dataset.yaml'; datafile.write_text(yaml.safe_dump(data),encoding='utf-8')
    initial=Path(weights or config['initial_weights'])
    if not initial.is_file():
        raise ValueError('模型权重不存在: '+str(initial))
    model=YOLO(str(initial))
    if weights:
        report=model.val(data=str(datafile),split='test',project=str(output),name='evaluation',workers=0)
        write(output/'test_report.json',report.results_dict)
    else:
        model.train(data=str(datafile),epochs=config.get('epochs',80),batch=config.get('batch',4),
                    imgsz=config.get('imgsz',768),project=str(output),name='training',workers=0,
                    seed=config.get('seed',42),plots=False)

def outlier(config,rows,output,weights=None):
    import numpy as np
    from PIL import Image
    from .inference import features
    if weights:
        saved=np.load(weights,allow_pickle=False);center=saved['center'];scale=saved['scale']
        predictions=[]
        for row in rows:
            if row['split']!='test':continue
            with Image.open(row['image']) as im:
                score=float(np.sqrt(np.mean(((features(im)-center)/scale)**2)))
            predictions.append(dict(image=row['image'],label=row['label'],score=score))
        write(output/'test_report.json',dict(method='robust-image-features-v1',predictions=predictions))
    else:
        x=[]
        for row in rows:
            if row['split']=='train' and row['label']=='normal':
                with Image.open(row['image']) as im:x.append(features(im))
        if len(x)<3:raise ValueError('训练至少需要 3 个正常对照')
        x=np.stack(x);center=np.median(x,axis=0);scale=np.maximum(np.median(np.abs(x-center),axis=0)*1.4826,.01)
        np.savez(output/'baseline.npz',center=center,scale=scale)
        write(output/'training_report.json',dict(method='robust-image-features-v1',normal_count=len(x)))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['validate','train','test'])
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--config',type=Path)
    parser.add_argument('--weights',type=Path)
    args=parser.parse_args()
    try:
        config=read(args.config) if args.config else {}
        manifest=args.manifest or config.get('manifest')
        if not manifest:raise ValueError('请指定 --manifest 或配置文件的 manifest')
        if args.command!='validate' and config.get('task') not in ('clusters','phase','quality','outliers'):
            raise ValueError('配置必须指定有效 task')
        rows=validate(manifest,config.get('classes'),config.get('task')=='clusters')
        if args.command=='validate':
            print(json.dumps({'valid':True,'counts':{s:sum(r['split']==s for r in rows) for s in ['train','validation','test']}}));return
        if args.command=='test' and (not args.weights or not args.weights.is_file()):
            raise ValueError('测试必须提供存在的 --weights')
        output=Path(config.get('output','workbench/data/runs'))
        from datetime import datetime
        import uuid
        output=output/(config['task']+'_'+args.command+'_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
        output.mkdir(parents=True,exist_ok=False)
        write(output/'config.json',config);write(output/'split_manifest.json',rows)
        runner={'clusters':detector,'phase':classifier,'quality':classifier,'outliers':outlier}[config['task']]
        runner(config,rows,output,args.weights if args.command=='test' else None)
        print('COMPLETE',output,flush=True)
    except (ValueError,FileNotFoundError) as exc:
        parser.exit(2,'配置或数据错误: '+str(exc)+'\n')

if __name__=='__main__':main()
