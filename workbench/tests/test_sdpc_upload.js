'use strict';
const assert=require('node:assert/strict');
const Queue=require('../web/sdpc-upload.js');
const file=(name,size=12,path='')=>({name,size,webkitRelativePath:path,lastModified:123,slice:(start,end)=>({size:end-start})});
function harness(){
  const states=new Map(),saved=new Map(),calls={begin:0,finish:0,chunks:[]};let brokenName='',loseFinish=false;
  const storage={getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)};
  async function api(url,body){
    if(url==='/api/slides/uploads'){const id=String(++calls.begin),s={...body,id,status:'uploading',uploaded_bytes:0};states.set(id,s);return {...s};}
    const id=url.split('/')[4],s=states.get(id);
    if(!s){const e=new Error('not found');e.status=404;throw e;}
    if(url.endsWith('/finish')){calls.finish++;s.status='queued';if(loseFinish){loseFinish=false;throw new Error('lost acknowledgement');}}
    return {...s};
  }
  async function sendChunk(id,offset,blob){
    const s=states.get(id);if(s.name===brokenName)throw new Error('network down');
    assert.equal(offset,s.uploaded_bytes);assert.ok(blob.size<=8*1024*1024);s.uploaded_bytes+=blob.size;calls.chunks.push([id,offset,blob.size]);return {uploaded_bytes:s.uploaded_bytes};
  }
  return {api,sendChunk,storage,calls,states,saved,break:(name)=>brokenName=name,loseFinish:()=>loseFinish=true};
}
async function main(){
  let h=harness(),q=new Queue(h);
  q.add([file('mode.sdpc')],{scan_mode:'full'});await q.start();
  assert.equal(h.states.get(q.items[0].slideId).scan_mode,'full');
  h=harness();q=new Queue(h);
  q.add([file('a.sdpc',8*1024*1024+3),file('b.SDPC'),file('bad.png'),file('a.sdpc',8*1024*1024+3)]);await q.start();
  assert.deepEqual(q.items.map(r=>r.status),['queued','queued','invalid']);assert.equal(h.calls.begin,2);assert.equal(h.calls.chunks.length,3);

  h=harness();h.break('bad.sdpc');q=new Queue(h);q.add([file('bad.sdpc'),file('good.sdpc')]);await q.start();
  assert.deepEqual(q.items.map(r=>r.status),['failed','queued']);h.break('');await q.retry(q.items[0].key);
  assert.equal(q.items[0].status,'queued');assert.equal(h.calls.begin,2,'retry must reuse the reserved upload');

  h=harness();h.loseFinish();q=new Queue(h);q.add([file('ack.sdpc')]);await q.start();assert.equal(q.items[0].status,'failed');
  await q.retry(q.items[0].key);assert.equal(q.items[0].status,'queued');assert.equal(h.calls.begin,1);assert.equal(h.calls.chunks.length,1);

  h=harness();q=new Queue({...h,onQueued:()=>{throw new Error('refresh failed');}});q.add([file('one.sdpc')]);await q.start();assert.equal(q.items[0].status,'queued');

  h=harness();q=new Queue(h);q.add([file('same.sdpc',12,'folder1/same.sdpc'),file('same.sdpc',12,'folder2/same.sdpc')]);await q.start();assert.equal(h.calls.begin,2);

  h=harness();let release;const gate=new Promise(r=>release=r);let first=true;
  q=new Queue({...h,sendChunk:async(...args)=>{if(first){first=false;await gate;}return h.sendChunk(...args);}});
  q.add([file('first.sdpc')]);const running=q.start();await Promise.resolve();q.add([file('added-later.sdpc')]);release();await running;
  assert.deepEqual(q.items.map(r=>r.status),['queued','queued']);assert.equal(h.calls.begin,2);
  console.log('7 SDPC upload queue scenarios passed: mode, chunks, duplicates, failure isolation, resume, lost ACK and live additions');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
