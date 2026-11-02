/* Shared, independently testable upload queue. File bytes are read one chunk at a time. */
(function(root,factory){
  if(typeof module==='object'&&module.exports)module.exports=factory();
  else root.SdpcUploadQueue=factory();
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  class SdpcUploadQueue{
    constructor({api,sendChunk,storage,onChange=()=>{},onQueued=()=>{}}){
      Object.assign(this,{api,sendChunk,storage,onChange,onQueued});this.items=[];this.running=null;
    }
    add(files,options={}){
      let added=0,duplicates=0;
      for(const file of files){
        const relative=file.webkitRelativePath||file.name;
        const key='sdpc-upload:'+JSON.stringify([file.name,file.size,file.lastModified,relative]);
        const old=this.items.find(r=>r.key===key);
        if(old){if(old.status==='failed'){old.status='pending';old.error='';added++;}else duplicates++;continue;}
        const valid=/\.sdpc$/i.test(file.name)&&file.size>0;
        this.items.push({key,file,name:relative,size:file.size,options:{...options},uploaded:0,status:valid?'pending':'invalid',error:valid?'':'必须为非空 SDPC 文件'});added++;
      }
      this.onChange(this.items);return {added,duplicates};
    }
    retry(key){const row=this.items.find(r=>r.key===key);if(row?.status==='failed'){row.status='pending';row.error='';this.onChange(this.items);}return this.start();}
    start(){
      if(this.running)return this.running;
      this.running=this.drain().finally(()=>{this.running=null;this.onChange(this.items);});return this.running;
    }
    read(key){try{return this.storage?.getItem(key);}catch{return null;}}
    save(key,value){try{this.storage?.setItem(key,value);}catch{/* Live retries also retain uploadId in memory. */}}
    remove(key){try{this.storage?.removeItem(key);}catch{/* Upload has already been accepted. */}}
    async drain(){
      for(let row;(row=this.items.find(r=>r.status==='pending'));){
        row.status='uploading';this.onChange(this.items);
        try{
          const slide=await this.upload(row);row.slideId=slide.id;row.status='queued';row.uploaded=row.size;row.error='';this.onChange(this.items);
          try{await this.onQueued(slide);}catch{/* UI refresh failure must not resubmit an accepted slide. */}
        }catch(e){row.status='failed';row.error=e.message||String(e);this.onChange(this.items);}
      }
    }
    async upload(row){
      let state=null;
      const legacy='sdpc-upload:'+JSON.stringify([row.file.name,row.size,row.file.lastModified]);
      const saved=row.uploadId||this.read(row.key)||(!row.file.webkitRelativePath?this.read(legacy):null);
      if(saved){
        try{state=await this.api('/api/slides/uploads/'+saved);}
        catch(e){if(e.status!==404)throw e;this.remove(row.key);this.remove(legacy);}
      }
      if(state&&(state.size!==row.size||state.name!==row.file.name))throw new Error('保存的上传记录与当前文件不一致');
      if(!state)state=await this.api('/api/slides/uploads',{name:row.file.name,size:row.size,relative_path:row.name,...row.options});
      row.uploadId=state.id;this.save(row.key,state.id);
      if(state.status==='uploading'){
        let offset=state.uploaded_bytes||0,retries=0;row.uploaded=offset;this.onChange(this.items);
        while(offset<row.size){
          try{
            const end=Math.min(row.size,offset+8*1024*1024);
            const result=await this.sendChunk(state.id,offset,row.file.slice(offset,end));
            if(!Number.isSafeInteger(result.uploaded_bytes)||result.uploaded_bytes<=offset||result.uploaded_bytes>row.size)throw new Error('服务器返回无效的上传偏移');
            offset=result.uploaded_bytes;retries=0;
          }catch(e){
            if(++retries>3)throw e;
            const resumed=await this.api('/api/slides/uploads/'+state.id);
            if(resumed.status!=='uploading'){state=resumed;break;}
            offset=resumed.uploaded_bytes;
          }
          row.uploaded=offset;this.onChange(this.items);
        }
      }
      const slide=await this.api('/api/slides/uploads/'+state.id+'/finish',{});
      if(!slide.id)throw new Error('未收到切片任务编号');
      this.remove(row.key);this.remove(legacy);return slide;
    }
  }
  return SdpcUploadQueue;
});
