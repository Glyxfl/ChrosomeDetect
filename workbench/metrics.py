"""Review agreement and detection evaluation; missing truth is never zero accuracy."""
def divide(a, b):
    return a / b if b else None

def iou(a, b):
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    area = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection
    return intersection / max(area, 1e-12)

def detection(predictions, truth, threshold=.5):
    # Confidence-ordered one-to-one matching, no reuse of reference objects.
    available = set(range(len(truth))); matches = []
    for prediction in sorted(predictions, key=lambda p: p.get('confidence', 0), reverse=True):
        candidates = [(iou(prediction['bbox'], truth[i]['bbox']), i) for i in available]
        overlap, index = max(candidates, default=(0, -1))
        if index >= 0 and overlap >= threshold:
            available.remove(index)
            matches.append(dict(prediction_id=prediction['id'], truth_id=truth[index]['id'], iou=overlap))
    tp = len(matches); fp = len(predictions)-tp; fn = len(available)
    return dict(tp=tp, fp=fp, fn=fn, precision=divide(tp,tp+fp), recall=divide(tp,tp+fn),
                f1=divide(2*tp,2*tp+fp+fn), iou_threshold=threshold, matches=matches)

def evaluate(project):
    report = dict(project_id=project['id'], evaluation_kind='human_review_agreement',
                  caveat='人工复核预标注后的符合率；不是盲法独立测试准确率', quality=None, phase=None)
    for stage in ('quality', 'phase'):
        snapshot = project.get('predictions', {}).get(stage)
        if not snapshot or snapshot.get('stale') or not project['confirmed'].get(stage):
            continue
        truth = {r['id']: r for r in project['results'][stage] if r.get('reviewed') and r.get('review_status') != 'rejected'}
        pairs = [(r, truth[r['id']]) for r in snapshot['rows'] if r['id'] in truth]
        matches = sum(a['label']==b['label'] for a,b in pairs)
        matrix = {}
        for a,b in pairs:
            key = a['label']+' -> '+b['label']; matrix[key] = matrix.get(key,0)+1
        report[stage] = dict(matched=matches, reviewed=len(pairs), agreement=divide(matches,len(pairs)),
                             source=snapshot['source'], model=snapshot.get('model'), confusion=matrix,
                             bootstrap=snapshot.get('bootstrap',False))
    for stage,label in (('clusters','cluster'),('instances','chromosome')):
        snapshot=project.get('predictions',{}).get(stage)
        ready = bool(snapshot and not snapshot.get('stale') and project['confirmed'].get(stage) and project.get('truth_complete',{}).get(stage))
        report[stage]=dict(available=ready, reason=None if ready else '需要本阶段预标注和人工完整真值确认')
        if ready:
            predicted=[r for r in snapshot['rows'] if r['label']==label]
            truth=[r for r in project['results'][stage] if r['label']==label]
            report[stage].update(detection(predicted,truth), source=snapshot['source'], reference_count=len(truth))
    successful=[r for r in project.get('timings',[]) if r.get('status')=='completed']
    latest={r['stage']:r for r in successful}
    machine_seconds=sum(r.get('compute_seconds',0) for r in latest.values())
    report['efficiency']=dict(machine_seconds=machine_seconds, cumulative_machine_seconds=sum(r.get('compute_seconds',0) for r in successful),
                              images_per_minute=60/machine_seconds if machine_seconds else None,
                              complete=all(project['confirmed'].get(s) for s in ('quality','clusters','phase','instances','outliers')),
                              timing_scope='最近一次成功的各阶段耗时之和，含解码/模型加载，初次导入含原图保存；不含队列、人工、空闲与结果数据库写入', stages=latest)
    return report

def aggregate(projects):
    reports=[evaluate(p) for p in projects]
    result=dict(projects=len(reports),reports=reports)
    quality=[r['quality'] for r in reports if r['quality'] and r['quality']['source']=='model']
    n=sum(r['reviewed'] for r in quality); matched=sum(r['matched'] for r in quality)
    result['quality_model_agreement']=dict(matched=matched, reviewed=n, rate=divide(matched,n),
                                          bootstrap_count=sum(r['reviewed'] for r in quality if r.get('bootstrap')))
    for stage in ('clusters','instances'):
        eligible=[r[stage] for r in reports if r[stage]['available']]
        tp=sum(r['tp'] for r in eligible); fp=sum(r['fp'] for r in eligible); fn=sum(r['fn'] for r in eligible)
        result[stage]=dict(evaluated_images=len(eligible),tp=tp,fp=fp,fn=fn,precision=divide(tp,tp+fp),recall=divide(tp,tp+fn))
    complete=[r['efficiency'] for r in reports if r['efficiency']['complete']]
    seconds=sum(r['machine_seconds'] for r in complete)
    result['efficiency']=dict(completed_images=len(complete),mean_machine_seconds=divide(seconds,len(complete)),
                              machine_images_per_minute=divide(len(complete)*60,seconds))
    return result
