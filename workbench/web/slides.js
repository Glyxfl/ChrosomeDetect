'use strict';
let slideVisible=true,slideTimer=null,activeSlide=null,slideSelection=null,slidePage=0,slideFindings=[];
let overviewImage=new Image();
let slideView=null,slideDrag=null,regionImage=null,regionBox=null,regionTimer=null,regionBusy=false,regionKey='';
let uploadQueueTimer=null;
const uploadQueue=new SdpcUploadQueue({
  api,storage:localStorage,
  async sendChunk(id,offset,blob){
    const response=await fetch('/api/slides/uploads/'+id+'/chunk',{method:'POST',headers:{'X-Offset':String(offset)},body:blob});
    const data=await response.json();if(!response.ok){const e=new Error(data.error||'分块上传失败');e.status=response.status;throw e;}return data;
  },
  onChange(){if(!uploadQueueTimer)uploadQueueTimer=setTimeout(()=>{uploadQueueTimer=null;renderUploadQueue();},100);},
  async onQueued(slide){if(!activeSlide){activeSlide=slide;slideView=null;}await refreshSlides();}
});
function renderUploadQueue(){
  const rows=uploadQueue.items;const count=status=>rows.filter(r=>r.status===status).length;
  $('sdpcUploadQueue').hidden=!rows.length;
  $('uploadQueueSummary').textContent=`共 ${rows.length} 个文件 · 待上传 ${count('pending')} · 上传中 ${count('uploading')} · 已入识别队列 ${count('queued')} · 失败 ${count('failed')+count('invalid')}`;
  $('retryAllUploads').disabled=!count('failed');
  $('uploadQueueRows').innerHTML=rows.map((r,i)=>`<tr><td>${escapeHTML(r.name)}<small>${(r.size/1024**3).toFixed(2)} GiB</small></td><td><progress max="${r.size||1}" value="${r.uploaded}"></progress> ${r.size?(r.uploaded/r.size*100).toFixed(1):'0'}%</td><td>${{pending:'等待上传',uploading:'上传中',queued:'上传完成，已提交识别',failed:'上传失败，可重试',invalid:'文件无效'}[r.status]}<small>${escapeHTML(r.error||'')}</small></td><td>${r.status==='failed'?`<button data-retry-upload="${i}">重试 / 续传</button>`:''}</td></tr>`).join('');
}
const slideStates={uploading:'上传中',imported:'已导入',queued:'等待识别',running:'自动识别中',preparing_review:'准备簇图复核',completed:'全片提取完成',completed_partial:'局部提取完成',completed_with_issues:'完成，有失败簇',failed:'识别失败',interrupted:'已中断，可继续'};
function slideMessage(text,success=false){$('slideMessage').hidden=!text;$('slideMessage').textContent=text;$('slideMessage').className='message'+(success?' success':'');}
function leaveSlides(){slideVisible=false;clearTimeout(slideTimer);clearTimeout(regionTimer);}
function slideTiming(s){
  if(['completed','completed_partial','completed_with_issues','failed','interrupted'].includes(s.status)&&s.machine_seconds!==undefined)return `${s.machine_seconds.toFixed(1)} s（累计处理）`;
  if(s.status==='queued')return '排队中';
  const p=s.scan_progress;
  if(!p)return '处理中 / 待测量';
  if(p.stage==='coarse')return `高层粗筛 ${p.tiles_completed}/${p.tiles_planned} · ${p.tiles_per_second.toFixed(2)} 图块/s<small>粗筛后计算精检范围及剩余时间</small>`;
  if(p.stage!=='scan')return `扫描完成，合并 / 裁图 / 准备复核中`;
  const eta=p.estimated_remaining_seconds;
  return `本轮扫描 ${p.session_seconds.toFixed(0)} s · ${p.tiles_per_second.toFixed(2)} 图块/s<small>${eta==null?'正在估算':`扫描预计剩余 ${Math.ceil(eta/60)} 分钟`}（另需合并、裁图、准备复核）</small>`;
}
async function showSlides(id=null,projectId=null){
  slideVisible=true;batchVisible=false;clearTimeout(batchTimer);$('slidesPage').hidden=false;$('analysisPage').hidden=true;$('batchPage').hidden=true;$('trainingPage').hidden=true;
  ['analysisNav','batchNav','trainingNav'].forEach(id=>$(id).classList.remove('active'));$('slidesNav').classList.add('active');$('pageTitle').textContent='从 SDPC 切片自动提取染色体簇';
  if(id){if(activeSlide?.id!==id){slideView=null;regionImage=null;regionBox=null;regionKey='';}activeSlide=await api('/api/slides/'+id);slideSelection=projectId;slidePage=Math.max(0,Math.floor((activeSlide.clusters||[]).findIndex(c=>c.project_id===projectId)/40));}
  await refreshSlides();
  if(projectId)zoomSlideSelection();
}
async function refreshSlides(){
  clearTimeout(slideTimer);
  const slides=await api('/api/slides');
  $('slideRows').innerHTML=slides.map(s=>`<tr><td><button data-slide="${s.id}">${escapeHTML(s.name)}</button><small>${(s.size/1024**3).toFixed(2)} GiB · ${s.id.slice(0,10)}</small></td><td>${slideStates[s.status]||escapeHTML(s.status)}${s.partial?' / 局部':''}${s.scan_mode==='pyramid'?' / 高层粗筛（检出率待验证）':''}<small>${escapeHTML(s.error||s.progress||'')}</small></td><td>${s.cluster_count||0}</td><td>${slideTiming(s)}</td><td>${['failed','interrupted'].includes(s.status)?`<button data-resume="${s.id}">继续扫描</button>`:''}<a class="crop-download" href="/api/slides/${s.id}/log" target="_blank" rel="noopener">扫描日志</a></td></tr>`).join('')||'<tr><td colspan="5">请导入 .sdpc 原始切片，导入后会自动开始识别。</td></tr>';
  const byId=new Map(slides.map(s=>[s.id,s]));
  for(const button of $('slideRows').querySelectorAll('[data-slide]')){
    const source=byId.get(button.dataset.slide);if(source?.relative_path)button.textContent=source.relative_path;
  }
  if(activeSlide){
    activeSlide=await api('/api/slides/'+activeSlide.id);
    slideFindings=await api('/api/slides/'+activeSlide.id+'/findings');renderSlide();
  }
  if(slideVisible&&slides.some(s=>['queued','running','preparing_review'].includes(s.status)))slideTimer=setTimeout(()=>refreshSlides().catch(e=>slideMessage(e.message)),2000);
}
function renderSlide(){
  $('slideDetail').hidden=!activeSlide;if(!activeSlide)return;
  $('slideName').textContent=activeSlide.name;$('slideSource').href='/api/slides/'+activeSlide.id+'/source';
  const dims=activeSlide.dimensions_l0;
  $('slideInfo').textContent=`${slideStates[activeSlide.status]||activeSlide.status} · ${dims?dims.join(' × ')+' L0 像素 · ':''}${activeSlide.cluster_count||0} 个检出簇 · ${activeSlide.source_sha256?'SHA-256 '+activeSlide.source_sha256.slice(0,16):'正在读取原始切片'}`;
  $('partialWarning').hidden=!activeSlide.partial;
  const rows=activeSlide.clusters||[],start=slidePage*40,visible=rows.slice(start,start+40);
  $('slideGallery').innerHTML=visible.map(c=>`<article class="panel slide-cluster ${slideSelection===c.project_id?'selected':''}"><img loading="lazy" src="/api/projects/${c.project_id}/image?thumbnail=1" alt="L0 染色体簇 ${c.id}"><div><strong>簇 ${c.id}</strong><small>SDPC L0 [${c.crop_box_l0.join(', ')}]<br>检测分数 ${(c.score*100).toFixed(1)}%</small><button data-locate="${c.project_id}">原切片定位</button><button data-review="${c.project_id}" class="primary">复核簇图 →</button></div></article>`).join('')||'<p class="muted">尚无可复核簇图。扫描结束后自动显示；零检出不等于切片中不存在染色体簇。</p>';
  $('slidePageNumber').textContent=` ${rows.length?slidePage+1:0}/${Math.ceil(rows.length/40)} `;$('slidePrev').disabled=slidePage===0;$('slideNext').disabled=start+40>=rows.length;
  const url='/api/slides/'+activeSlide.id+'/overview';
  if(dims&&overviewImage.datasetSource!==url){overviewImage=new Image();overviewImage.datasetSource=url;overviewImage.onload=paintSlide;overviewImage.src=url;}
  paintSlide();
}
function slideTransform(){
  const rect=$('slideCanvas').getBoundingClientRect(),[w,h]=activeSlide.dimensions_l0;
  if(slideView)return {...slideView,width:rect.width,height:rect.height};
  const scale=Math.min(rect.width/w,rect.height/h)*.94;
  slideView={scale,x:(rect.width-w*scale)/2,y:(rect.height-h*scale)/2};return {...slideView,width:rect.width,height:rect.height};
}
function paintSlide(){
  const canvas=$('slideCanvas'),rect=canvas.getBoundingClientRect();if(!rect.width)return;
  const dpr=window.devicePixelRatio||1;canvas.width=rect.width*dpr;canvas.height=rect.height*dpr;const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
  if(!activeSlide?.dimensions_l0||!overviewImage.complete||!overviewImage.naturalWidth)return;
  const t=slideTransform();ctx.translate(t.x,t.y);ctx.scale(t.scale,t.scale);const extent=activeSlide.overview_extent_l0||[0,0,...activeSlide.dimensions_l0];ctx.drawImage(overviewImage,0,0,extent[2],extent[3]);
  if(regionImage?.complete&&regionImage.naturalWidth&&regionBox)ctx.drawImage(regionImage,regionBox[0],regionBox[1],regionBox[2]-regionBox[0],regionBox[3]-regionBox[1]);
  for(const c of activeSlide.clusters||[]){const b=c.crop_box_l0;ctx.strokeStyle=c.project_id===slideSelection?'#e89030':'#168e77';ctx.lineWidth=(c.project_id===slideSelection?4:2)/t.scale;ctx.strokeRect(b[0],b[1],b[2]-b[0],b[3]-b[1]);if(c.project_id===slideSelection){ctx.fillStyle='#e89030';ctx.font=`${13/t.scale}px sans-serif`;ctx.fillText('簇 '+c.id,b[0],b[1]-5/t.scale);}}
  for(const r of slideFindings){const b=r.bbox_l0;if(!b)continue;ctx.strokeStyle='#ef3f45';ctx.lineWidth=3/t.scale;ctx.strokeRect(b[0],b[1],Math.max(b[2]-b[0],4/t.scale),Math.max(b[3]-b[1],4/t.scale));}
  const selected=(activeSlide.clusters||[]).find(c=>c.project_id===slideSelection);$('slideLocation').textContent=selected?'所选簇 '+selected.id+' · L0 '+selected.crop_box_l0.join(', '):'概览用于定位；复核查看 L0 原始簇图';
}
async function uploadSdpc(files){
  files=Array.from(files||[]);if(!files.length)return;
  await showSlides();const result=uploadQueue.add(files,{scan_mode:$('sdpcScanMode').value});renderUploadQueue();
  slideMessage(`加入 ${result.added} 个文件，跳过 ${result.duplicates} 个重复项。可继续添加文件；每份上传完成后自动开始排队识别。`,true);
  $('slideUpload').value='';$('slideFolder').value='';await uploadQueue.start();renderUploadQueue();
}
$('slidesNav').onclick=()=>showSlides().catch(e=>slideMessage(e.message));
$('slideUpload').onchange=e=>uploadSdpc(e.target.files).catch(e=>slideMessage(e.message));
$('slideFolder').onchange=e=>{
  const all=Array.from(e.target.files),files=all.filter(f=>/\.sdpc$/i.test(f.name));
  if(!files.length){slideMessage('所选文件夹中没有 SDPC 文件');return;}
  uploadSdpc(files).catch(e=>slideMessage(e.message));
};
$('uploadQueueRows').onclick=e=>{if(e.target.dataset.retryUpload!==undefined)uploadQueue.retry(uploadQueue.items[Number(e.target.dataset.retryUpload)].key);};
$('retryAllUploads').onclick=()=>{for(const r of uploadQueue.items)if(r.status==='failed'){r.status='pending';r.error='';}renderUploadQueue();uploadQueue.start();};
$('refreshSlides').onclick=()=>refreshSlides().catch(e=>slideMessage(e.message));
$('localImport').onclick=async()=>{
  $('localImport').disabled=true;
  try{
    const paths=$('localSdpc').value.split(/\r?\n/).map(p=>p.trim()).filter(Boolean);
    const batch=await api('/api/slides/local-batch',{paths,scan_mode:$('sdpcScanMode').value});
    slideMessage(`批次 ${batch.id.slice(0,10)}：已接收 ${batch.accepted}，失败 ${batch.failed}，重复跳过 ${batch.duplicates}。`,!batch.failed);
    $('localBatchResults').hidden=false;$('localBatchResults').innerHTML='<div class="panel-heading"><strong>本地 SDPC 批量导入结果</strong></div><table><tbody>'+batch.items.map(r=>`<tr><td>${escapeHTML(r.path)}</td><td>${r.status==='accepted'?'已进入识别队列':r.status==='duplicate'?'重复，已跳过':'导入失败'}<small>${escapeHTML(r.error||'')}</small></td></tr>`).join('')+'</tbody></table>';
    await refreshSlides();
  }
  catch(e){slideMessage(e.message);}finally{$('localImport').disabled=false;}
};
$('slideRows').onclick=e=>{
  if(e.target.dataset.slide)showSlides(e.target.dataset.slide).catch(e=>slideMessage(e.message));
  if(e.target.dataset.resume)api('/api/slides/'+e.target.dataset.resume+'/run',{}).then(s=>{activeSlide=s;return refreshSlides();}).catch(e=>slideMessage(e.message));
};
$('slideGallery').onclick=async e=>{
  if(e.target.dataset.locate){slideSelection=e.target.dataset.locate;zoomSlideSelection();renderSlide();}
  if(e.target.dataset.review){page(false);await listProjects();await load(e.target.dataset.review);}
};
$('slideCanvas').onclick=e=>{
  if(e.detail===0||e.target.dataset.moved==='1'){e.target.dataset.moved='0';return;}
  if(!activeSlide?.dimensions_l0)return;const r=$('slideCanvas').getBoundingClientRect(),t=slideTransform(),x=(e.clientX-r.left-t.x)/t.scale,y=(e.clientY-r.top-t.y)/t.scale;
  const candidates=(activeSlide.clusters||[]).map(c=>{const b=c.crop_box_l0;return {row:c,d:Math.hypot((b[0]+b[2])/2-x,(b[1]+b[3])/2-y)};}).sort((a,b)=>a.d-b.d);
  if(candidates[0]&&candidates[0].d*t.scale<30){slideSelection=candidates[0].row.project_id;slidePage=Math.floor(activeSlide.clusters.findIndex(c=>c.project_id===slideSelection)/40);renderSlide();}
};
function zoomSlideSelection(){
  const row=(activeSlide?.clusters||[]).find(c=>c.project_id===slideSelection);if(!row)return;
  const b=row.crop_box_l0,rect=$('slideCanvas').getBoundingClientRect(),scale=Math.min(rect.width/(b[2]-b[0]),rect.height/(b[3]-b[1]))*.65;
  slideView={scale,x:rect.width/2-(b[0]+b[2])/2*scale,y:rect.height/2-(b[1]+b[3])/2*scale};paintSlide();scheduleRegion();
}
function scheduleRegion(){clearTimeout(regionTimer);if(slideVisible)regionTimer=setTimeout(loadNativeRegion,250);}
function loadNativeRegion(){
  if(regionBusy||!activeSlide?.dimensions_l0)return;
  const t=slideTransform(),[w,h]=activeSlide.dimensions_l0;
  const fit=Math.min(t.width/w,t.height/h)*.94;if(t.scale<=fit*1.2)return;
  let level=0;activeSlide.downsamples.forEach((ds,i)=>{if(ds<=1/t.scale)level=i;});const ds=activeSlide.downsamples[level];
  const x=Math.max(0,Math.floor(-t.x/t.scale/ds)*ds),y=Math.max(0,Math.floor(-t.y/t.scale/ds)*ds);
  const x2=Math.min(Math.floor(w/ds)*ds,Math.ceil((t.width-t.x)/t.scale/ds)*ds),y2=Math.min(Math.floor(h/ds)*ds,Math.ceil((t.height-t.y)/t.scale/ds)*ds);
  if(x2<=x||y2<=y)return;const box=[x,y,x2,y2].map(Math.round),key=activeSlide.id+':'+level+':'+box.join(',');if(key===regionKey)return;
  regionBusy=true;const id=activeSlide.id,img=new Image();
  img.onload=()=>{regionBusy=false;if(activeSlide?.id===id){regionImage=img;regionBox=box;regionKey=key;paintSlide();}scheduleRegion();};
  img.onerror=()=>{regionBusy=false;$('slideLocation').textContent='原生区域暂未加载，请查看切片状态或扫描日志';};
  img.src='/api/slides/'+id+'/region?level='+level+'&box='+box.join(',');
}
$('slideCanvas').onpointerdown=e=>{if(!activeSlide?.dimensions_l0)return;const t=slideTransform();slideDrag={x:e.clientX,y:e.clientY,vx:t.x,vy:t.y};e.target.setPointerCapture(e.pointerId);};
$('slideCanvas').onpointermove=e=>{if(!slideDrag)return;if(Math.hypot(e.clientX-slideDrag.x,e.clientY-slideDrag.y)>4)e.target.dataset.moved='1';slideView.x=slideDrag.vx+e.clientX-slideDrag.x;slideView.y=slideDrag.vy+e.clientY-slideDrag.y;paintSlide();};
$('slideCanvas').onpointerup=()=>{if(slideDrag){slideDrag=null;scheduleRegion();}};
$('slideCanvas').addEventListener('wheel',e=>{if(!activeSlide?.dimensions_l0)return;e.preventDefault();const r=e.target.getBoundingClientRect(),t=slideTransform(),x=(e.clientX-r.left-t.x)/t.scale,y=(e.clientY-r.top-t.y)/t.scale;const scale=Math.max(.000001,Math.min(10,t.scale*(e.deltaY<0?1.3:1/1.3)));slideView={scale,x:e.clientX-r.left-x*scale,y:e.clientY-r.top-y*scale};paintSlide();scheduleRegion();},{passive:false});
const resetSlideButton=document.createElement('button');resetSlideButton.textContent='重置全片视图';resetSlideButton.onclick=()=>{slideView=null;regionImage=null;regionBox=null;regionKey='';paintSlide();};$('slideLocation').parentNode.append(resetSlideButton);
$('slidePrev').onclick=()=>{slidePage--;renderSlide();};$('slideNext').onclick=()=>{slidePage++;renderSlide();};
$('reviewSlidePage').onclick=async()=>{batchSelected=new Set((activeSlide.clusters||[]).slice(slidePage*40,slidePage*40+40).map(c=>c.project_id));await showBatch();};
window.addEventListener('resize',()=>{slideView=null;paintSlide();});
refreshSlides().catch(e=>slideMessage(e.message));
