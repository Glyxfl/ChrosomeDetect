'use strict';
const $ = id => document.getElementById(id);
const stages = ['quality', 'clusters', 'phase', 'instances', 'outliers'];
const titles = ['图像质量分级', '染色体簇提取', '分裂期识别', '染色体候选定位', '离群点提取'];
const descriptions = ['复核模型预分级；不可用图片可确认归档，但不能进入分析。', '提取候选簇并定位。完整复核误检与漏检后勾选真值确认，可计算识别指标。', '当前识别 MI / 非 MI / 不可判读；确认目标 MI 后进入单条染色体候选提取。', '分割或检测簇内染色体候选，保留原图坐标、所属簇和唯一编号。相连区域需人工修正。', '按具体染色体候选计算相对离群分数，红色高亮疑似离群；需人工确认或驳回。'];
const labels = {clear:'清晰',blurry:'模糊',unusable:'不可用',cluster:'染色体簇',chromosome:'染色体候选',exclude:'排除',mi:'MI（目标分裂期）',non_mi:'非 MI',unreadable:'不可判读',unknown:'待确认',outlier:'离群点',normal:'正常 / 保留'};
const options = {quality:['clear','blurry','unusable'],clusters:['cluster','exclude'],phase:['unknown','mi','non_mi','unreadable'],instances:['chromosome','exclude'],outliers:['unknown','outlier','normal']};
const decisions={pending:'待人工复核',confirmed:'已确认',corrected:'已修正',rejected:'已驳回'};
let project = null, stage = 'quality', selected = null, image = new Image(), busy = false, drawing = false;
let view = {scale:1,x:0,y:0}, drag = null, draft = null;
let batchSelected=new Set(),batchVisible=false,batchTimer=null;
const escapeHTML = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(url, body, extra={}) {
  const response = await fetch(url, body === undefined ? extra : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),...extra});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}
function message(text, success=false) { $('message').hidden = !text; $('message').textContent=text; $('message').className='message'+(success?' success':''); }
async function guarded(fn) {
  if(busy) return;
  busy=true; renderActions();
  try { await fn(); } catch(e) { message(e.message); if(project) render(); }
  finally { busy=false; renderActions(); }
}
async function listProjects() {
  const rows=await api('/api/projects');
  $('projects').innerHTML='<option value="">选择已有项目</option>'+rows.map(p=>`<option value="${p.id}">${escapeHTML(p.name)}</option>`).join('');
  if(project) $('projects').value=project.id;
}
async function load(id) {
  project=await api('/api/projects/'+id); stage=project.stage; selected=null;
  image=new Image(); image.onload=()=>{resetView();paint();}; image.src=`/api/projects/${id}/image`;
  render(); $('projects').value=id;
}
async function upload(files) {
  files=Array.from(files||[]);if(!files.length) return;
  await guarded(async()=>{
    const uploaded=[],errors=[];
    for(const [i,file] of files.entries()){
      message(`正在导入 ${i+1}/${files.length}：${file.name}，生成质量预标注…`);
      try{
        if(file.size>64*1024*1024)throw new Error('超过 64 MB');
        const response=await fetch('/api/projects',{method:'POST',headers:{'X-Filename':encodeURIComponent(file.name)},body:file});
        const data=await response.json();if(!response.ok)throw new Error(data.error);uploaded.push(data.id);
      }catch(e){errors.push(file.name+'：'+e.message);}
    }
    await listProjects();
    if(uploaded.length)await load(uploaded[0]);
    message(`导入成功 ${uploaded.length}/${files.length}。${errors.join('；')}`,!errors.length);
    if(files.length>1){batchSelected=new Set(uploaded);await showBatch();$('batchStatus').textContent=`导入成功 ${uploaded.length}/${files.length}。${errors.join('；')}`;}
  });
  $('upload').value=''; $('emptyUpload').value='';
}
function renderActions() {
  $('predict').disabled=busy||!project||!canAccess(stage);
  $('confirm').disabled=busy||!project||!canAccess(stage);
  $('upload').disabled=busy; $('emptyUpload').disabled=busy; $('projects').disabled=busy;
  $('draw').disabled=busy||!['clusters','instances'].includes(stage);
  document.querySelectorAll('#review input,#review select,#review button').forEach(e=>e.disabled=busy||!canAccess(stage));
  $('predict').textContent=busy?'处理中…':'更新预标注';
}
function canAccess(s) { return project && stages.slice(0,stages.indexOf(s)).every(k=>project.confirmed[k]); }
function render() {
  $('steps').innerHTML=stages.map((s,i)=>`<button class="step ${s===stage?'active':''}" data-stage="${s}" ${!project||!canAccess(s)?'disabled':''}><span class="number">${project?.confirmed[s]?'✓':String(i+1).padStart(2,'0')}</span><span><strong>${titles[i]}</strong><small>${project?.confirmed[s]?'已确认':s===stage?'当前阶段':'等待复核'}</small></span></button>`).join('');
  $('empty').hidden=!!project; $('workspace').hidden=!project; $('export').hidden=!project;
  if(!project) return;
  $('export').href=`/api/projects/${project.id}/export`;
  $('bundle').hidden=false;$('bundle').href=`/api/projects/${project.id}/bundle`;
  $('stageTitle').textContent=String(stages.indexOf(stage)+1).padStart(2,'0')+' / '+titles[stages.indexOf(stage)];
  $('stageDescription').textContent=descriptions[stages.indexOf(stage)];
  $('reviewTitle').textContent=stage==='quality'?'质量复核':'候选结果 / 人工复核';
  $('imageMeta').textContent=`${project.name} · ${project.width} × ${project.height} px · 版本 ${project.revision} · 坐标基于上传原图`;
  $('confirm').textContent=stage==='outliers'?'确认本次结果 ✓':'确认并进入下一步 →';
  $('draw').hidden=!['clusters','instances'].includes(stage);
  $('truthLabel').hidden=!['clusters','instances'].includes(stage);$('truthComplete').checked=!!project.truth_complete?.[stage];
  $('parentLabel').hidden=stage!=='instances';
  $('parentCluster').innerHTML=project.results.clusters.filter(c=>c.label==='cluster'&&project.results.phase.some(p=>p.id===c.id&&p.label==='mi')).map(c=>`<option value="${escapeHTML(c.id)}">${escapeHTML(c.id)}</option>`).join('');
  if(!['clusters','instances'].includes(stage)) drawing=false;
  renderReview(); renderActions(); paint();
  refreshMetrics().catch(e=>message(e.message));
}
function decisionHTML(row){return `<small class="review-status ${row.review_status==='rejected'?'rejected':''}">${decisions[row.review_status]||'待人工复核'}${row.reviewer?' · '+escapeHTML(row.reviewer):''}</small><div class="decision-actions"><button data-decision="confirm" data-row="${escapeHTML(row.id)}">确认</button><button data-decision="correct" data-row="${escapeHTML(row.id)}">记录修正</button><button data-decision="reject" data-row="${escapeHTML(row.id)}">驳回</button></div>`;}
function selectHTML(row) { return `<select data-label="${escapeHTML(row.id)}" aria-label="修改标签">${options[stage].map(k=>`<option value="${k}" ${k===row.label?'selected':''}>${labels[k]}</option>`).join('')}</select>`; }
function renderReview() {
  let rows=project.results[stage]; $('count').textContent=rows.length+' 项';
  if(stage==='quality') {
    const row=rows[0];
    const predicted=project.predictions?.quality?.rows?.[0];
    $('review').innerHTML=`<div class="quality-result"><h3>图像质量：${labels[row.label]}</h3><p>原始预标注：${labels[predicted?.label]||'未记录'} · ${predicted?.source==='model'?'模型':'规则'}${predicted?.bootstrap?' / 合成数据初始化':''}</p>${selectHTML(row)}<p class="muted">${row.metrics?.laplacian_variance!==undefined?'清晰度指标 '+Number(row.metrics.laplacian_variance).toFixed(1)+' · 对比度 '+Number(row.metrics.contrast).toFixed(1):''}</p><p>${escapeHTML(row.note||'请复核质量等级后确认。')}</p>${decisionHTML(row)}</div>`;
    return;
  }
  if(!rows.length) {
    $('review').innerHTML='<p>暂无结果。点击“更新预标注”生成候选。</p><p class="muted">模型未配置时可手工标注。染色体簇阶段在原图绘制框；分裂期阶段可为各候选簇直接选择标签。</p>';
    if(stage==='phase') $('review').innerHTML+='<button id="manualPhase">创建待标注条目</button>';
    return;
  }
  if(stage==='outliers') rows=[...rows].sort((a,b)=>a.rank-b.rank);
  $('review').innerHTML=rows.map((r,i)=>{
    const cluster=geometryRows().find(c=>c.id===r.id);
    const coords=['clusters','instances'].includes(stage)?`<div class="coords">${r.bbox.map((v,k)=>`<input type="number" min="0" data-box="${r.id}" data-axis="${k}" value="${Math.round(v)}" aria-label="${['左','上','右','下'][k]}坐标">`).join('')}</div><button class="remove" data-remove="${r.id}">删除候选框</button>`:'';
    const predicted=project.predictions?.[stage]?.rows?.find(p=>p.id===r.id);
    return `<article class="review-card ${selected===r.id?'selected':''}" data-select="${r.id}"><img src="/api/projects/${project.id}/crops/${r.id}?v=${project.revision}" alt="候选 ${i+1}" loading="lazy"><div><strong>${stage==='outliers'?'排序 '+r.rank:'候选 '+String(i+1).padStart(3,'0')}</strong><small>${escapeHTML(r.id)}${r.cluster_id?'<br>所属簇 '+escapeHTML(r.cluster_id):''}<br>${r.source==='human'?'人工修改':r.source==='feature_baseline'?'图像特征基线':r.source==='connected_components'?'连通区域候选（需复核）':'模型预标注'}${r.confidence!==undefined?' · '+(r.confidence*100).toFixed(1)+'%':''}${r.score!==undefined?' · 离群分数 '+(r.score??'样本不足'):''}<br>${cluster?'原图 ['+cluster.bbox.map(Math.round).join(', ')+']':''}${predicted?'<br>原始预标注：'+labels[predicted.label]:''}</small>${selectHTML(r)}${coords}${decisionHTML(r)}<a class="crop-download" href="/api/projects/${project.id}/crops/${r.id}?full=1" download="candidate-${r.id}.png">下载原分辨率裁剪 ↗</a></div></article>`;
  }).join('');
}
async function saveRows(rows) {
  project=await api(`/api/projects/${project.id}/annotations`,{stage,rows,revision:project.revision,reviewer:$('reviewer').value,reason:$('reviewReason').value||'人工修改标签或边界'});
  message('修改已保存。下游结果需重新生成与确认。',true); render();
}
async function run() {
  await guarded(async()=>{
    message('正在更新预标注…');
    const job=await api(`/api/projects/${project.id}/run`,{stage,revision:project.revision});
    let state=job;
    while(state.status==='queued'||state.status==='running') {
      await new Promise(resolve=>setTimeout(resolve,700)); state=await api('/api/jobs/'+job.id);
    }
    if(state.status==='failed') throw new Error(state.error);
    project=await api('/api/projects/'+project.id); render(); message('预标注已更新，请复核后确认。',true);
  });
}
function resetView() {
  const rect=$('canvas').getBoundingClientRect();
  if(!project||!rect.width) return;
  const scale=Math.min(rect.width/project.width,rect.height/project.height)*.9;
  view={scale,x:(rect.width-project.width*scale)/2,y:(rect.height-project.height*scale)/2}; paint();
}
function point(e) { const r=$('canvas').getBoundingClientRect(); return {x:(e.clientX-r.left-view.x)/view.scale,y:(e.clientY-r.top-view.y)/view.scale}; }
function geometryRows(){return [...project.results.clusters,...(project.results.instances||[])];}
function paint() {
  const canvas=$('canvas'), rect=canvas.getBoundingClientRect(); if(!rect.width) return;
  const dpr=window.devicePixelRatio||1; canvas.width=rect.width*dpr; canvas.height=rect.height*dpr;
  const ctx=canvas.getContext('2d'); ctx.scale(dpr,dpr); ctx.clearRect(0,0,rect.width,rect.height);
  if(!project||!image.complete||!image.naturalWidth) return;
  ctx.save();ctx.translate(view.x,view.y);ctx.scale(view.scale,view.scale);ctx.drawImage(image,0,0);
  const abnormal=new Set(project.results.outliers.filter(r=>r.label==='outlier'&&r.review_status!=='rejected').map(r=>r.instance_id));
  geometryRows().forEach((r,i)=>{
    const [x,y,x2,y2]=r.bbox;ctx.strokeStyle=r.label==='exclude'?'#aeaaa1':abnormal.has(r.id)?'#ef3f45':r.id===selected?'#e98d35':r.instance_id?'#368cd2':'#168f7a';ctx.lineWidth=(r.id===selected||abnormal.has(r.id)?3:1.5)/view.scale;
    if(r.polygon?.length){ctx.beginPath();r.polygon.forEach(([px,py],k)=>k?ctx.lineTo(px,py):ctx.moveTo(px,py));ctx.closePath();ctx.fillStyle=abnormal.has(r.id)?'#ef3f4544':'#368cd222';ctx.fill();ctx.stroke();}
    ctx.strokeRect(x,y,x2-x,y2-y);ctx.fillStyle=ctx.strokeStyle;ctx.font=`${12/view.scale}px sans-serif`;
    if(!r.instance_id||r.id===selected||abnormal.has(r.id)||(x2-x)*view.scale>=24)ctx.fillText(r.instance_id?r.id.split('-chr-').pop():String(i+1),x,y-4/view.scale);
  });
  if(draft){ctx.strokeStyle='#e98d35';ctx.lineWidth=2/view.scale;ctx.strokeRect(draft.x1,draft.y1,draft.x2-draft.x1,draft.y2-draft.y1);}
  ctx.restore();
}
function locate(id) {
  selected=id; const r=geometryRows().find(c=>c.id===id);if(!r) return;
  const rect=$('canvas').getBoundingClientRect(),[x,y,x2,y2]=r.bbox;
  view.scale=Math.min(rect.width/(x2-x),rect.height/(y2-y))*.65;
  view.x=rect.width/2-(x+x2)/2*view.scale;view.y=rect.height/2-(y+y2)/2*view.scale;renderReview();renderActions();paint();
}
$('review').addEventListener('change',e=>guarded(async()=>{
  const rows=structuredClone(project.results[stage]);
  if(e.target.dataset.label) rows.find(r=>r.id===e.target.dataset.label).label=e.target.value;
  else if(e.target.dataset.box) rows.find(r=>r.id===e.target.dataset.box).bbox[Number(e.target.dataset.axis)]=Number(e.target.value);
  else return;
  // The backend compares rows to preserve provenance for unchanged predictions.
  await saveRows(rows);
}));
$('review').addEventListener('click',e=>{
  if(e.target.dataset.decision)return guarded(async()=>{
    const row=project.results[stage].find(r=>r.id===e.target.dataset.row);
    project=await api(`/api/projects/${project.id}/review`,{stage,row_id:row.id,decision:e.target.dataset.decision,label:row.label,revision:project.revision,reviewer:$('reviewer').value,reason:$('reviewReason').value});render();message('复核决定已保存。',true);
  });
  if(e.target.closest('[data-remove]')) return guarded(async()=>saveRows(project.results[stage].filter(r=>r.id!==e.target.dataset.remove)));
  if(e.target.id==='manualPhase') return guarded(async()=>saveRows(project.results.clusters.filter(r=>r.label==='cluster').map(r=>({id:r.id,label:'unknown',source:'human',reviewed:false}))));
  if(e.target.closest('select,input,button,a')) return;
  const card=e.target.closest('[data-select]');if(card) locate(card.dataset.select);
});
$('canvas').addEventListener('pointerdown',e=>{
  if(!project||busy) return; const p=point(e);drag={...p,clientX:e.clientX,clientY:e.clientY,x:view.x,y:view.y};
  $('canvas').setPointerCapture(e.pointerId);
  if(drawing) draft={x1:p.x,y1:p.y,x2:p.x,y2:p.y};
});
$('canvas').addEventListener('pointermove',e=>{
  if(!drag) return;
  if(drawing){const p=point(e);draft.x2=p.x;draft.y2=p.y;}else{view.x=drag.x+e.clientX-drag.clientX;view.y=drag.y+e.clientY-drag.clientY;}paint();
});
$('canvas').addEventListener('pointerup',e=>{
  if(!drag) return;
  const start=drag;drag=null;
  if(drawing&&draft){
    const d=draft;draft=null;drawing=false;$('draw').textContent='＋ 绘制候选框';
    const clamp=(v,max)=>Math.max(0,Math.min(max,v));
    const box=[clamp(Math.min(d.x1,d.x2),project.width),clamp(Math.min(d.y1,d.y2),project.height),clamp(Math.max(d.x1,d.x2),project.width),clamp(Math.max(d.y1,d.y2),project.height)];
    if(box[2]-box[0]>3&&box[3]-box[1]>3) guarded(async()=>saveRows([...project.results[stage],{id:crypto.randomUUID().replaceAll('-','').slice(0,12),label:stage==='instances'?'chromosome':'cluster',bbox:box,cluster_id:stage==='instances'?$('parentCluster').value:undefined,source:'human',reviewed:true}]));
    else paint();
  }else if(Math.hypot(e.clientX-start.clientX,e.clientY-start.clientY)<4){
    const p=point(e);const hit=geometryRows().reverse().find(r=>p.x>=r.bbox[0]&&p.x<=r.bbox[2]&&p.y>=r.bbox[1]&&p.y<=r.bbox[3]);
    if(hit){selected=hit.id;renderReview();paint();}
  }
});
$('canvas').addEventListener('wheel',e=>{
  if(!project) return;e.preventDefault();const p=point(e),r=$('canvas').getBoundingClientRect();view.scale=Math.max(.0001,Math.min(30,view.scale*(e.deltaY<0?1.15:1/1.15)));view.x=e.clientX-r.left-p.x*view.scale;view.y=e.clientY-r.top-p.y*view.scale;paint();
},{passive:false});
$('draw').onclick=()=>{drawing=!drawing;$('draw').textContent=drawing?'在原图拖动绘制（点击取消）':'＋ 绘制候选框';};
$('resetView').onclick=resetView;
$('steps').onclick=e=>{const b=e.target.closest('[data-stage]');if(!b||busy)return;stage=b.dataset.stage;render();};
$('confirm').onclick=()=>guarded(async()=>{
  project=await api(`/api/projects/${project.id}/confirm`,{stage,revision:project.revision,target_phase:'mi',truth_complete:$('truthComplete').checked,reviewer:$('reviewer').value});
  stage=project.stage;render();message(project.confirmed.outliers?'本次结果已确认，可导出 JSON。':'已确认。请生成或填写下一步预标注。',true);
});
$('predict').onclick=run;
$('upload').onchange=e=>upload(e.target.files);$('emptyUpload').onchange=e=>upload(e.target.files);
$('projects').onchange=e=>{if(e.target.value)guarded(()=>load(e.target.value));};
function page(training){batchVisible=false;clearTimeout(batchTimer);$('batchPage').hidden=true;$('batchNav').classList.remove('active');$('analysisPage').hidden=training;$('trainingPage').hidden=!training;$('analysisNav').classList.toggle('active',!training);$('trainingNav').classList.toggle('active',training);$('pageTitle').textContent=training?'为下一轮模型迭代做好准备':'从原始图像到离群点';if(!training)requestAnimationFrame(resetView);}
$('analysisNav').onclick=()=>page(false);$('trainingNav').onclick=()=>page(true);
window.addEventListener('resize',paint);
const percent=v=>v===null||v===undefined?'待复核':(v*100).toFixed(1)+'%';
function metricCard(title,value,note){return `<article class="metric"><small>${escapeHTML(title)}</small><strong>${escapeHTML(value)}</strong><small>${escapeHTML(note)}</small></article>`;}
async function refreshMetrics(){
  if(!project)return;const id=project.id,revision=project.revision;
  const r=await api(`/api/projects/${id}/metrics`);if(!project||project.id!==id||project.revision!==revision)return;
  $('metrics').innerHTML=metricCard('质量分级一致率',percent(r.quality?.agreement),r.quality?`${r.quality.matched}/${r.quality.reviewed} · ${r.quality.source==='model'?'模型':'规则'}${r.quality.bootstrap?' / 合成初始化':''}`:'需人工确认质量真值')+
    metricCard('染色体识别准确率',percent(r.instances.precision),r.instances.available?`TP ${r.instances.tp} / FP ${r.instances.fp} · ${r.instances.source==='model'?'模型':'候选算法'}`:'需候选预标注及完整真值')+
    metricCard('染色体检出率',percent(r.instances.recall),r.instances.available?`TP ${r.instances.tp} / FN ${r.instances.fn} · IoU ≥ 0.5`:'请检查漏检并补框')+
    metricCard('单张机器耗时',r.efficiency.machine_seconds.toFixed(2)+' s',r.efficiency.complete?'完整流程，不含人工等待':'流程尚未完成，当前累计');
}
async function showBatch(){
  batchVisible=true;$('analysisPage').hidden=true;$('trainingPage').hidden=true;$('batchPage').hidden=false;
  $('analysisNav').classList.remove('active');$('trainingNav').classList.remove('active');$('batchNav').classList.add('active');$('pageTitle').textContent='批量处理、人工复核与指标';
  await refreshBatch();
}
function updateBatchLink(){
  $('batchExport').href='/api/bundle?'+[...batchSelected].map(id=>'id='+encodeURIComponent(id)).join('&');
  $('batchExport').setAttribute('aria-disabled',batchSelected.size?'false':'true');
}
async function refreshBatch(){
  clearTimeout(batchTimer);
  const [projects,report,batches]=await Promise.all([api('/api/projects'),api('/api/metrics'),api('/api/batches')]);
  const q=report.quality_model_agreement,d=report.instances,e=report.efficiency;
  $('batchMetrics').innerHTML=metricCard('模型质量分级一致率',percent(q.rate),`已复核 ${q.reviewed} 张 · 合成初始化 ${q.bootstrap_count} 张`)+
    metricCard('染色体识别准确率',percent(d.precision),`完整复核 ${d.evaluated_images} 张 · TP ${d.tp} / FP ${d.fp}`)+
    metricCard('染色体检出率',percent(d.recall),`TP ${d.tp} / FN ${d.fn} · IoU ≥ 0.5`)+
    metricCard('单张平均机器耗时',e.mean_machine_seconds===null?'待完成':e.mean_machine_seconds.toFixed(2)+' s',`完成 ${e.completed_images} 张，排除人工及队列等待`);
  $('batchRows').innerHTML=projects.map(p=>{
    const metrics=report.reports.find(r=>r.project_id===p.id);
    return `<tr><td><input type="checkbox" data-project="${p.id}" ${batchSelected.has(p.id)?'checked':''} aria-label="选择 ${escapeHTML(p.name)}"></td><td><button data-open="${p.id}">${escapeHTML(p.name)}</button><small>${p.id.slice(0,12)}</small></td><td>${titles[stages.indexOf(p.stage)]}</td><td>${metrics.efficiency.machine_seconds.toFixed(2)} s</td><td>${p.confirmed.outliers?'完成':p.confirmed.quality&&p.results.quality[0].label==='unusable'?'不可用 / 已归档':'需人工复核'}</td></tr>`;
  }).join('')||'<tr><td colspan="5">暂无图像，请到图像分析页批量导入。</td></tr>';
  $('batchJobs').innerHTML='<h3>批次记录</h3>'+batches.slice(0,12).map(b=>`<div class="batch-entry">${escapeHTML(b.id.slice(0,10))} · ${titles[stages.indexOf(b.stage)]} · ${escapeHTML(b.status)}<small>${b.items.map(i=>escapeHTML((projects.find(p=>p.id===i.project_id)?.name||i.project_id)+'：'+i.status+(i.error?' / '+i.error:''))).join('<br>')}</small></div>`).join('');
  updateBatchLink();if(batchVisible&&batches.some(b=>b.status==='running'))batchTimer=setTimeout(()=>refreshBatch().catch(e=>$('batchStatus').textContent=e.message),1500);
}
$('batchNav').onclick=()=>showBatch().catch(e=>message(e.message));
$('batchRefresh').onclick=()=>refreshBatch().catch(e=>$('batchStatus').textContent=e.message);
$('batchRows').onchange=e=>{const id=e.target.dataset.project;if(!id)return;e.target.checked?batchSelected.add(id):batchSelected.delete(id);updateBatchLink();};
$('batchRows').onclick=e=>{const id=e.target.dataset.open;if(!id)return;page(false);guarded(()=>load(id));};
$('selectAll').onchange=e=>{document.querySelectorAll('[data-project]').forEach(c=>{c.checked=e.target.checked;c.checked?batchSelected.add(c.dataset.project):batchSelected.delete(c.dataset.project);});updateBatchLink();};
$('batchRun').onclick=async()=>{
  if(!batchSelected.size){$('batchStatus').textContent='请勾选图像';return;}
  $('batchRun').disabled=true;
  try{await api('/api/batches',{project_ids:[...batchSelected],stage:$('batchStage').value});$('batchStatus').textContent='已提交；未满足人工确认关卡的项目会单独提示。';await refreshBatch();}
  catch(e){$('batchStatus').textContent=e.message;}finally{$('batchRun').disabled=false;}
};
async function init(){
  render();await listProjects();const health=await api('/api/health');
  $('models').innerHTML=Object.entries(health.models).map(([k,v])=>`<article class="model-card"><span class="pill">${v.available?'权重已发现':'基线 / 待训练'}</span><h3>${{clusters:'染色体簇检测',phase:'MI 分裂期分类',quality:'图像质量分级'}[k]}</h3><p>${k==='quality'&&!v.available?'当前推理使用规则初筛；可训练质量分类模型。':'权重在首次运行时加载并验证。'}</p><code>${escapeHTML(v.path)}</code></article>`).join('');
}
init().catch(e=>message(e.message));
