(function () {
    'use strict';
    const $ = id => document.getElementById(id);
    const read = (id, source=document) => {try{return JSON.parse(source.getElementById(id)?.textContent||'[]');}catch(_){return [];}};
    const limits=read('chat_upload_limits');
    let store,toast,items=[],owner='',generation=0,running=false,queue=[],pending=null,initialized=false;
    const sessionAttachments=new Map();
    const key=()=>document.querySelector('[name=deepseek_key]').value.trim();
    const headers=()=>({'X-DeepSeek-CSRF':$('deepseek_csrf').value});
    async function api(path,data) {
        const response=await fetch('/deepseek/'+path,{method:'POST',headers:{...headers(),'Content-Type':'application/json'},body:JSON.stringify(data)});
        let result;try{result=await response.json();}catch(_){throw Error('本地服务返回异常，请重试。');}
        if(!response.ok||!result.ok)throw Error(result.message||'请求失败，请重试。');
        return result;
    }
    function sync() {
        $('chat_file_ids').value=JSON.stringify(items.filter(item=>item.status==='ready').map(item=>item.id));
        const list=$('chat_attachment_list');list.replaceChildren();
        items.forEach(item=>{
            const row=document.createElement('div');row.className='chat-attachment';
            const text=document.createElement('span');
            text.textContent=item.filename+' · '+(item.bytes/1024/1024).toFixed(2)+' MiB · '+
                ({queued:'等待上传',uploading:item.progress<100?'传至本地服务 '+item.progress+'%':'正在上传至 DeepSeek…',ready:'已上传，可随 Chat 发送',error:item.error}[item.status]||'');
            row.append(text);
            if(item.status==='uploading'){const progress=document.createElement('progress');progress.max=100;progress.value=item.progress;row.append(progress);}
            const remove=document.createElement('button');remove.type='button';remove.textContent='移除';remove.setAttribute('aria-label','移除附件 '+item.filename);
            remove.addEventListener('click',()=>{
                item.xhr?.abort();items=items.filter(value=>value!==item);sync();
                if(item.id)api('files/detach',{deepseek_key:item.key,session_id:owner,file_id:item.id}).catch(error=>toast(error.message));
                toast('已移除对话引用；已上传的云端文件仍保留，可在 Chat 中申请删除。');
            });
            row.append(remove);list.append(row);
        });
    }
    function contextChanged() {
        if(!initialized)return;
        const current=store.current().id;
        if(current!==owner){generation++;items.forEach(item=>{if(['queued','uploading'].includes(item.status)){item.status='error';item.error='上传已因会话切换停止，请移除后重新上传。';}item.xhr?.abort();});sessionAttachments.set(owner,items);owner=current;items=sessionAttachments.get(owner)||[];running=false;sync();}
    }
    function upload(item,epoch,sid) {
        return new Promise(resolve=>{
            const xhr=new XMLHttpRequest();item.xhr=xhr;item.status='uploading';item.progress=0;sync();
            xhr.open('POST','/deepseek/files/upload');xhr.setRequestHeader('X-DeepSeek-CSRF',$('deepseek_csrf').value);xhr.timeout=650000;
            const data=new FormData();data.set('file',item.file);data.set('deepseek_key',item.key);data.set('session_id',sid);data.set('reference_ids',JSON.stringify(items.filter(value=>value.status==='ready').map(value=>value.id)));
            xhr.upload.onprogress=event=>{if(epoch===generation&&event.lengthComputable){item.progress=Math.round(event.loaded/event.total*100);sync();}};
            xhr.onload=()=>{
                if(epoch===generation&&items.includes(item)){
                    try {const result=JSON.parse(xhr.responseText);if(xhr.status<200||xhr.status>=300||!result.ok)throw Error(result.message||'上传失败');Object.assign(item,result.file,{status:'ready'});delete item.file;}
                    catch(error){item.status='error';item.error=error.message;}
                    sync();
                }
                resolve();
            };
            xhr.onerror=xhr.ontimeout=()=>{if(epoch===generation&&items.includes(item)){item.status='error';item.error='上传网络异常或超时；可通过云端列表核对文件状态。';sync();}resolve();};
            xhr.onabort=()=>resolve();xhr.send(data);
        });
    }
    async function processUploads() {
        if(running)return;running=true;const epoch=generation,sid=owner;
        try {let item;while(epoch===generation&&(item=items.find(value=>value.status==='queued')))await upload(item,epoch,sid);}
        finally {if(epoch===generation)running=false;}
    }
    async function pickFiles(event) {
        contextChanged();const files=[...event.target.files];event.target.value='';
        if(!key()){toast('请先在设置中填写 DeepSeek API Key。');return;}
        const allowed=['image/jpeg','image/png','image/gif','image/webp'];
        for(const file of files){
            if(!allowed.includes(file.type)){toast('Chat Files API 仅支持 JPEG/PNG/GIF/WebP，不支持 PDF、DOCX 等文档。');continue;}
            if(!file.size||file.size>limits.fileBytes){toast(file.name+' 超出单文件 64 MiB 限制或为空。');continue;}
            if(items.length>=limits.count||items.reduce((sum,item)=>sum+item.bytes,0)+file.size>limits.totalBytes){toast('每次对话最多 600 张图片、合计 200 MiB。');break;}
            if(new TextEncoder().encode(file.name).length>512){toast('文件名最多 512 字节。');continue;}
            items.push({localId:window.LingsiUid(),filename:file.name,bytes:file.size,file,key:key(),status:'queued',progress:0});
        }
        sync();processUploads();
    }
    function canSubmit(action) {
        contextChanged();
        if(items.some(item=>['queued','uploading'].includes(item.status))){toast('图片正在上传，请等待完成或移除附件后再发送。');return false;}
        if(action==='chat'){
            if(items.some(item=>item.status==='error')){toast('请移除上传失败的附件后再发送。');return false;}
            if(items.some(item=>item.key!==key())){toast('DeepSeek Key 已改变，请移除旧附件后重新上传。');return false;}
            if(items.length&&!$('chat_query').value.trim())$('chat_query').value='请分析附件图片。';
        }
        return true;
    }
    function summary() {return items.length?'\n图片附件：'+items.filter(item=>item.status==='ready').map(item=>item.filename).join('、'):'';}
    function nextDelete() {
        if(pending||!queue.length)return;
        pending=queue.shift();
        const info=pending.file;
        $('cloud_delete_details').textContent='文件名：'+info.filename+'\nfile_id：'+info.id+'\n大小：'+info.bytes+' 字节\n创建时间：'+info.created_time+'\n有效期至：'+new Date(pending.expires_at*1000).toLocaleString();
        $('cloud_delete_status').textContent='尚未删除；确认仅对当前会话和此文件有效。';
        $('cloud_delete_confirm').disabled=false;$('cloud_delete_cancel').disabled=false;$('cloud_delete_cancel').textContent='取消';
        if($('settings_sidebar').open)$('settings_sidebar').close();
        $('cloud_delete_dialog').showModal();
    }
    async function decide(decision) {
        if(!pending)return;
        const current=pending,confirm=$('cloud_delete_confirm'),cancel=$('cloud_delete_cancel');
        if(current.failed&&decision==='cancel'){$('cloud_delete_dialog').close();pending=null;nextDelete();return;}
        if(decision==='confirm'&&current.session_id!==store.current().id){toast('当前会话已改变，不能确认此删除。');return;}
        if(decision==='confirm'&&Date.now()>=current.expires_at*1000){confirm.disabled=true;$('cloud_delete_status').textContent='确认已过期，请取消后在 Chat 中重新申请。';return;}
        confirm.disabled=true;cancel.disabled=true;$('cloud_delete_status').textContent=decision==='confirm'?'正在确认删除…':'正在取消…';
        try {
            const result=await api('files/delete',{deepseek_key:key(),session_id:current.session_id,file_id:current.file.id,token:current.token,decision});
            store.append(current.session_id,{id:window.LingsiUid(),role:'ai',text:result.message+'：'+current.file.filename+'（'+current.file.id+'）',ts:Date.now()});
            if(result.deleted){items=items.filter(item=>item.id!==current.file.id);sync();}
            $('cloud_delete_dialog').close();pending=null;window.LingsiUI.renderChat();nextDelete();
        }catch(error){
            $('cloud_delete_status').textContent=error.message+'；若已确认但结果不明，请先查询云端文件状态，不要重复提交。';
            current.failed=true;cancel.disabled=false;cancel.textContent='关闭';
        }
    }
    function handleResponse(source=document) {
        const values=read('chat_attachment_data',source);
        if(Array.isArray(values)){items=values.map(value=>({...value,key:key(),status:'ready'}));sync();}
        const incoming=read('cloud_pending_data',source);
        if(Array.isArray(incoming)){queue.push(...incoming.filter(value=>value&&typeof value.token==='string'));nextDelete();}
    }
    function init(options) {
        store=options.store;toast=options.toast;owner=store.current().id;initialized=true;
        $('attachment_btn').addEventListener('click',()=>{if(key())$('chat_attachment_input').click();else{toast('请先在设置中填写 DeepSeek API Key。');$('settings_open').click();}});
        $('chat_attachment_input').addEventListener('change',pickFiles);
        const balanceButton=$('deepseek_balance_btn'),balanceStatus=$('deepseek_balance_status');let balanceSequence=0;
        balanceButton.addEventListener('click',async()=>{
            if(!key()){balanceStatus.textContent='请先填写 DeepSeek API Key';return;}
            const seq=++balanceSequence,currentKey=key();balanceButton.disabled=true;balanceStatus.textContent='查询中…';
            try{const result=await api('balance',{deepseek_key:currentKey});if(seq===balanceSequence&&key()===currentKey)balanceStatus.textContent=result.balance+'￥';}
            catch(error){if(seq===balanceSequence)balanceStatus.textContent=error.message;}
            finally{if(seq===balanceSequence)balanceButton.disabled=false;}
        });
        document.querySelector('[name=deepseek_key]').addEventListener('input',()=>{balanceSequence++;balanceButton.disabled=false;balanceStatus.textContent='尚未查询';});
        $('cloud_delete_confirm').addEventListener('click',()=>decide('confirm'));
        $('cloud_delete_cancel').addEventListener('click',()=>decide('cancel'));
        $('cloud_delete_dialog').addEventListener('cancel',event=>{event.preventDefault();if(!$('cloud_delete_cancel').disabled)decide('cancel');});
        window.addEventListener('pagehide',()=>{generation++;items.forEach(item=>item.xhr?.abort());});
        handleResponse();
    }
    window.DeepSeekUI={init,canSubmit,summary,contextChanged,handleResponse};
})();
