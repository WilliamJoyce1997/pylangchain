(function () {
    'use strict';
    const $ = id => document.getElementById(id);
    const form = $('main_form');
    const history = $('history_list');
    const messages = $('chat_messages');
    const dialog = $('settings_sidebar');
    let submitting = false;
    let composing = false;
    let settingsSnapshot = [];
    let toastTimer;
    function toast(text) {
        $('ui_toast').textContent = text;
        $('ui_toast').classList.remove('hidden');
        clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('ui_toast').classList.add('hidden'),5000);
    }
    function warn(text) {
        $('storage_warning').textContent=text;
        $('storage_warning').classList.remove('hidden');
    }
    let storage;
    try { storage=window.localStorage; }
    catch (_) {
        storage={getItem:()=>null,setItem:()=>{throw Error('storage')},removeItem:()=>{throw Error('storage')}};
    }
    const store = new window.LingsiStore(storage,warn);
    function icon(name) {
        const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
        svg.setAttribute('class','icon');svg.setAttribute('aria-hidden','true');
        const use=document.createElementNS('http://www.w3.org/2000/svg','use');
        use.setAttribute('href','#icon-'+name);svg.append(use);return svg;
    }
    function redact(text) {
        let value=String(text||'');
        document.querySelectorAll('[data-secret]').forEach(input=>{
            if(input.value)value=value.split(input.value).join('[密钥已隐藏]');
        });
        return value;
    }
    function closeDrawers() {
        document.body.classList.remove('history-open','tools-open');
        $('sidebar_btn').setAttribute('aria-expanded','false');
    }
    function toggleHistory() {
        document.body.classList.remove('tools-open');
        const active=document.body.classList.toggle('history-open');
        $('sidebar_btn').setAttribute('aria-expanded',String(active));
    }
    function toggleTools(force) {
        if(window.innerWidth<1200) {
            document.body.classList.remove('history-open');
            document.body.classList.toggle('tools-open',force ?? !document.body.classList.contains('tools-open'));
            $('tools_toggle').setAttribute('aria-expanded',String(document.body.classList.contains('tools-open')));
        } else {
            document.body.classList.toggle('tools-collapsed',force === undefined ? !document.body.classList.contains('tools-collapsed') : !force);
            $('tools_toggle').setAttribute('aria-expanded',String(!document.body.classList.contains('tools-collapsed')));
        }
    }
    function updateToolStates() {
        [['tb_network','enable_network'],['tb_memory','use_memory'],['tb_persona','use_persona'],['tb_tts','enable_tts']].forEach(([button,input])=>{
            $(button).classList.toggle('enabled',$(input).checked);
        });
        const base=form.elements.namedItem('base_dir').value.trim()||'C:\\Script&File';
        $('file_root_summary').textContent=base;
    }
    function showTool(panel,button) {
        const wasOpen=panel && !panel.classList.contains('hidden');
        document.querySelectorAll('#tool_sections > .section').forEach(p=>p.classList.add('hidden'));
        document.querySelectorAll('.tool-btn').forEach(b=>{b.classList.remove('active');b.setAttribute('aria-expanded','false');});
        if(panel&&!wasOpen) {
            panel.classList.remove('hidden');
            button?.classList.add('active');button?.setAttribute('aria-expanded','true');
            toggleTools(true);
            if(window.innerWidth<1200)setTimeout(()=>panel.scrollIntoView({block:'nearest'}),30);
        }
    }
    function selectSession(id) {
        if(submitting)return;
        stopRecognition();store.select(id);$('chat_query').value='';renderChat();closeDrawers();
    }
    function renameSession(id) {
        const session=store.find(id);if(!session||submitting)return;
        const title=window.prompt('为对话设置标题',session.title);
        if(title?.trim()){store.rename(id,redact(title));renderChat();}
    }
    function renderHistory() {
        history.replaceChildren();
        const search=$('history_search').value.trim().toLocaleLowerCase();
        const sessions=[...store.data.sessions].sort((a,b)=>b.updatedAt-a.updatedAt).filter(s=>s.title.toLocaleLowerCase().includes(search));
        const now=new Date();const today=new Date(now.getFullYear(),now.getMonth(),now.getDate()).getTime();
        let previous='';
        sessions.forEach(session=>{
            const group=session.updatedAt>=today?'今天':session.updatedAt>=today-86400000?'昨天':'更早';
            if(group!==previous){const title=document.createElement('div');title.className='history-group';title.textContent=group;history.append(title);previous=group;}
            const row=document.createElement('div');row.className='history-row'+(session.id===store.data.activeId?' selected':'');
            const entry=document.createElement('button');entry.type='button';entry.className='history-entry';
            entry.setAttribute('aria-current',session.id===store.data.activeId?'true':'false');
            entry.append(icon('chat'));const copy=document.createElement('div');const title=document.createElement('strong');
            title.textContent=redact(session.title);title.title=title.textContent;const preview=document.createElement('small');
            preview.textContent=redact(session.messages.at(-1)?.text||'开始一个新的想法');copy.append(title,preview);entry.append(copy);
            entry.addEventListener('click',()=>selectSession(session.id));
            const edit=document.createElement('button');edit.type='button';edit.className='history-edit';edit.setAttribute('aria-label','重命名 '+session.title);edit.append(icon('edit'));
            edit.addEventListener('click',()=>renameSession(session.id));
            const remove=document.createElement('button');remove.type='button';remove.className='history-delete';remove.setAttribute('aria-label','删除 '+session.title);remove.append(icon('trash'));
            remove.addEventListener('click',()=>{
                if(submitting)return;
                if(window.confirm('删除“'+session.title+'”？仅删除浏览器中的此会话，不会删除后端本地记忆。')){if(session.id===store.current().id)stopRecognition();store.remove(session.id);renderChat();}
            });
            row.append(entry,edit,remove);history.append(row);
        });
        if(!sessions.length){const p=document.createElement('p');p.className='small';p.textContent='没有找到匹配的对话';history.append(p);}
    }
    function appendText(container,text) {
        const parts=redact(text).split(String.fromCharCode(96).repeat(3));
        parts.forEach((part,index)=>{
            const node=document.createElement(index%2?'pre':'div');
            node.className='message-text';
            if(index%2) { const line=part.indexOf('\n');part=line>=0?part.slice(line+1):part; }
            node.textContent=part;container.append(node);
        });
    }
    function appendMedia(container,url,type) {
        const safe=window.LingsiSafeMedia(url);if(!safe)return;
        const media=document.createElement(type);
        if(type==='img'){media.alt='生成的图片';media.loading='lazy';}
        else {media.controls=true;media.preload='metadata';}
        media.src=safe;container.append(media);
        const download=document.createElement('a');download.href=safe;download.download='';
        download.target='_blank';download.rel='noopener noreferrer';download.className='media-download';download.append(icon('download'));
        download.append(document.createTextNode('下载'+({img:'图片',video:'视频',audio:'音频'}[type])));
        container.append(download);
        media.addEventListener('error',()=>{
            if(container.querySelector('[data-failed-url="'+type+'"]'))return;
            const hint=document.createElement('div');hint.className='media-error';hint.dataset.failedUrl=type;
            hint.textContent='此'+({img:'图片',video:'视频',audio:'音频'}[type])+'暂时无法访问，可能已移动、过期或服务已重启。';container.append(hint);
        },{once:true});
    }
    function renderChat() {
        const session=store.current();
        $('current_session_title').textContent=redact(session.title);
        $('session_meta').textContent=session.messages.length ? '共 '+session.messages.length+' 条消息 · '+new Date(session.updatedAt).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '你的本地 AI 工作台';
        $('ui_session_id').value=session.id;
        messages.replaceChildren();
        if(!session.messages.length) {
            const welcome=document.createElement('div');welcome.className='welcome';
            const mark=document.createElement('span');mark.className='brand-mark';mark.append(icon('star'));
            const heading=document.createElement('h2');heading.textContent='让灵感，在这里发生';
            const intro=document.createElement('p');intro.textContent='从一个问题开始。整理信息、探索想法，或使用右侧工具创作图片、视频与语音。';
            const chips=document.createElement('div');chips.className='welcome-chips';
            ['思考与写作','多模态创作','本地任务'].forEach(text=>{const span=document.createElement('span');span.textContent=text;chips.append(span);});
            welcome.append(mark,heading,intro,chips);messages.append(welcome);
        }
        session.messages.forEach(message=>{
            const row=document.createElement('article');row.className='message-row '+(message.role==='user'?'user':'ai');
            const avatar=document.createElement('div');avatar.className='avatar '+(message.role==='user'?'user':'ai');avatar.append(icon(message.role==='user'?'user':'star'));
            const content=document.createElement('div');content.className='message-content';
            const bubble=document.createElement('div');bubble.className='message-bubble';appendText(bubble,message.text);
            message.images.forEach(url=>appendMedia(bubble,url,'img'));
            message.videos.forEach(url=>appendMedia(bubble,url,'video'));
            if(message.audioUrl)appendMedia(bubble,message.audioUrl,'audio');
            const time=document.createElement('div');time.className='message-time';
            time.textContent=new Date(message.ts).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});
            content.append(bubble,time);row.append(avatar,content);messages.append(row);
        });
        renderHistory();
        requestAnimationFrame(()=>{messages.scrollTop=messages.scrollHeight;});
    }
    function ingestResponse() {
        const requestId=$('result_request_id').value;
        const sessionId=$('result_session_id').value;
        const action=$('result_action').value;
        if(!requestId||store.isHandled(requestId))return;
        // Never resurrect deleted conversations or deliver a result to another session.
        const session=store.find(sessionId);
        if(!session){store.markHandled(requestId);return;}
        const text=redact($('deepseek_raw').value.trim()||$('answer_raw').value.trim());
        const parse=id=>{try {const data=JSON.parse($(id).value);return Array.isArray(data)?data:[];}catch(_){return [];}};
        if(action==='chat' && $('user_query_raw').value.trim()) {
            store.append(sessionId,{id:requestId+'-user',role:'user',text:redact($('user_query_raw').value.trim()),ts:Date.now()});
        }
        if(text||parse('media_images_raw').length||parse('media_videos_raw').length||$('tts_audio_raw').value) {
            store.append(sessionId,{id:requestId+'-ai',role:'ai',text,ts:Date.now(),images:parse('media_images_raw'),
                videos:parse('media_videos_raw'),audioUrl:$('tts_audio_raw').value.trim()});
        }
        store.markHandled(requestId);
        store.select(sessionId);
    }
    const localNames=['base_dir','memory_path','system_prompt'];
    function restoreSafeSettings() {
        // Server POST refill takes precedence over browser preferences.
        if($('result_request_id').value)return;
        try {
            const saved=JSON.parse(storage.getItem('lingsi_preferences_v1')||'{}');
            localNames.forEach(name=>{if(typeof saved[name]==='string')form.elements.namedItem(name).value=saved[name];});
        }catch(_){toast('本地偏好无法读取，已使用后端默认值。');}
    }
    function settingsTab(name) {
        document.querySelectorAll('[data-settings-tab]').forEach(button=>{
            const active=button.dataset.settingsTab===name;button.classList.toggle('active',active);button.setAttribute('aria-selected',String(active));
        });
        document.querySelectorAll('[data-settings-page]').forEach(page=>page.classList.toggle('hidden',page.dataset.settingsPage!==name));
    }
    function openSettings(tab='api') {
        if(submitting)return;
        settingsSnapshot=[...dialog.querySelectorAll('input,textarea,select')].map(input=>({input,value:input.value,checked:input.checked,files:input.type==='file'?input.files:null}));
        settingsTab(tab);dialog.showModal();
    }
    function cancelSettings() {
        settingsSnapshot.forEach(({input,value,checked,files})=>{
            if(input.type==='file') {if(files)input.files=files;}
            else input.value=value;
            if(input.type==='checkbox')input.checked=checked;
            if(input.hasAttribute('data-secret'))input.type='password';
        });
        updatePersonaDiv();updateToolStates();dialog.close();
    }
    async function saveSettings() {
        const button=$('settings_save');if(button.disabled)return;
        button.disabled=true;$('settings_cancel').disabled=true;$('settings_close').disabled=true;
        const data=new FormData(form);data.set('save_settings','1');
        try {
            const response=await fetch('/',{method:'POST',body:data});
            const result=await response.json();if(!response.ok||!result.ok)throw Error('save');
            const saved={};localNames.forEach(name=>saved[name]=form.elements.namedItem(name).value);
            try {storage.setItem('lingsi_preferences_v1',JSON.stringify(saved));}catch(_){warn('设置已发送到后端，但无法保存非密钥偏好。当前页面仍可使用。');}
            updateToolStates();dialog.close();toast(result.message);
        }catch(_){toast('设置未能保存，请检查本地服务并重试。');}
        finally {button.disabled=false;$('settings_cancel').disabled=false;$('settings_close').disabled=false;}
    }
    function validateSubmission(button) {
        const action=button?.name||'chat';
        const required=action==='send_image' ? ['ark_key','image_prompt'] :
            action==='send_video' ? ['pollo_key'] :
            ['voice_upload','check_voice_status'].includes(action) ? ['doubao_token','doubao_appid','voice_speaker_id'] :
            action==='clear_memory' ? [] : ['deepseek_key','query'];
        for(const name of required) {
            const input=form.elements.namedItem(name);
            if(!input?.value.trim()) {
                toast('请先填写 '+(name==='query'?'消息内容':input.closest('label')?.textContent.trim().split('\n')[0]||name));
                if(dialog.contains(input))openSettings('api');
                else {const panel=input.closest('.section');if(panel){panel.classList.remove('hidden');toggleTools(true);}}
                input?.focus();return false;
            }
        }
        if(action==='send_video') {
            const invalid=[...$('section_video').querySelectorAll('input,textarea,select')].find(input=>!input.disabled&&!input.checkValidity());
            if(invalid){invalid.reportValidity();return false;}
        }
        return true;
    }
    async function submitTemporarily(button, requestId, sessionId) {
        const data = new FormData(form);
        if (button.name) data.set(button.name, button.value || "1");
        try {
            const response = await fetch("/", {method:"POST", body:data});
            if (!response.ok) throw Error("request");
            const source = new DOMParser().parseFromString(await response.text(), "text/html");
            const value = id => source.getElementById(id)?.value || "";
            if (value("result_request_id") !== requestId || value("result_session_id") !== sessionId) throw Error("ownership");
            const list = id => { try { const data = JSON.parse(value(id)); return Array.isArray(data) ? data : []; } catch (_) { return []; } };
            const text = redact(value("deepseek_raw").trim() || value("answer_raw").trim());
            store.append(sessionId, {id:requestId+"-ai",role:"ai",text,ts:Date.now(),
                images:list("media_images_raw"),videos:list("media_videos_raw"),audioUrl:value("tts_audio_raw")});
            store.markHandled(requestId);
            $("diagnostics_raw").value = redact(value("answer_raw"));
            $("raw_output_details").hidden = !value("answer_raw");
            if (dialog.open) dialog.close();
        } catch (_) {
            toast("请求未能完成，当前消息仍保留在本页。请检查服务并导出未保存会话。");
        } finally {
            submitting = false;form.removeAttribute("aria-busy");
            document.querySelectorAll("button[type=submit]").forEach(b=>b.disabled=false);
            $("submit_status").textContent = "准备就绪";
            renderChat();
        }
    }
    form.addEventListener('submit',event=>{
        if(submitting){event.preventDefault();return;}
        const button=event.submitter||$('chat_send_btn');
        if(!validateSubmission(button)){event.preventDefault();return;}
        if(button.name==='clear_memory'&&!window.confirm('清空 '+form.elements.namedItem('memory_path').value+' 下的后端 memory.txt？此操作不影响浏览器会话，但会删除共享本地记忆。')){event.preventDefault();return;}
        const requestId=window.LingsiUid();$('ui_request_id').value=requestId;$('ui_session_id').value=store.current().id;
        if(!button.name)store.append(store.current().id,{id:requestId+'-user',role:'user',text:redact($('chat_query').value.trim()),ts:Date.now()});
        if(['send_image','send_video'].includes(button.name)) {
            const prompt=form.elements.namedItem(button.name==='send_image'?'image_prompt':'video_prompt').value.trim();
            store.append(store.current().id,{id:requestId+'-user',role:'user',text:(button.name==='send_image'?'图片创作':'视频创作')+(prompt?'：'+redact(prompt):'（参考图片）'),ts:Date.now()});
        }
        const canPersist = store.persist();
        submitting=true;stopRecognition();$('submit_status').textContent='正在处理，请稍候…';form.setAttribute('aria-busy','true');
        renderChat();
        if (!canPersist) {
            // Keep RAM-only sessions through the POST; no page navigation can discard them.
            event.preventDefault();
            submitTemporarily(button, requestId, store.current().id);
        }
        // Do not disable successful controls: the clicked submitter must retain its name.
        setTimeout(()=>document.querySelectorAll('button[type=submit]').forEach(btn=>btn.disabled=true),0);
    });
    $('chat_query').addEventListener('compositionstart',()=>composing=true);
    $('chat_query').addEventListener('compositionend',()=>composing=false);
    $('chat_query').addEventListener('keydown',event=>{
        if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&!composing&&event.keyCode!==229) {
            event.preventDefault();form.requestSubmit($('chat_send_btn'));
        }
    });
    $('new_chat_btn').addEventListener('click',()=>{if(!submitting){stopRecognition();store.create();$('chat_query').value='';renderChat();closeDrawers();$('chat_query').focus();}});
    $('history_search').addEventListener('input',renderHistory);
    $('rename_chat_btn').addEventListener('click',()=>renameSession(store.current().id));
    $('clear_chat_cache_btn').addEventListener('click',()=>{
        if(submitting)return;
        if(window.confirm('清空这台浏览器的全部会话历史？后端本地记忆和媒体文件不受影响。') && (stopRecognition(), store.clear())) {
            $('storage_warning').classList.add('hidden');renderChat();
        }
    });
    $('export_history_btn').addEventListener('click',()=>{
        const blob=new Blob([store.export()],{type:'application/json'});const link=document.createElement('a');
        link.href=URL.createObjectURL(blob);link.download='灵思AI-会话备份.json';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);
    });
    $('sidebar_btn').addEventListener('click',toggleHistory);$('history_close').addEventListener('click',closeDrawers);
    $('drawer_backdrop').addEventListener('click',closeDrawers);
    $('tools_toggle').addEventListener('click',()=>toggleTools());$('composer_tools').addEventListener('click',()=>toggleTools(true));
    $('tools_close').addEventListener('click',()=>window.innerWidth<1200?closeDrawers():toggleTools(false));
    $('tb_persona').addEventListener('click',event=>showTool($('section_persona'),event.currentTarget));
    $('tb_files').addEventListener('click',event=>showTool($('section_files'),event.currentTarget));
    $('persona_settings').addEventListener('click',()=>openSettings('local'));
    $('files_settings').addEventListener('click',()=>openSettings('local'));
    $('attachment_btn').addEventListener('click',()=>{showTool($('section_image'),$('tb_image'));$('img2img_file').click();});
    $('settings_open').addEventListener('click',()=>openSettings());
    $('settings_close').addEventListener('click',cancelSettings);$('settings_cancel').addEventListener('click',cancelSettings);
    $('settings_save').addEventListener('click',saveSettings);
    dialog.addEventListener('cancel',event=>{event.preventDefault();if(!$('settings_save').disabled)cancelSettings();});
    document.querySelectorAll('[data-settings-tab]').forEach(button=>button.addEventListener('click',()=>settingsTab(button.dataset.settingsTab)));
    document.querySelectorAll('[data-password-toggle]').forEach(button=>button.addEventListener('click',()=>{
        const input=button.parentElement.querySelector('input');const visible=input.type==='password';input.type=visible?'text':'password';
        button.setAttribute('aria-label',(visible?'隐藏':'显示')+input.name);
    }));
    ['enable_network','use_memory','use_persona','enable_tts'].forEach(id=>$(id).addEventListener('change',updateToolStates));
    form.elements.namedItem('base_dir').addEventListener('input',updateToolStates);
    function applyTheme(theme) {
        document.documentElement.dataset.theme=theme;
        $('theme_toggle').setAttribute('aria-pressed',String(theme==='dark'));
        $('theme_toggle').setAttribute('aria-label',theme==='dark'?'切换浅色主题':'切换深色主题');
    }
    try {applyTheme(storage.getItem('lingsi_theme')==='dark'?'dark':'light');}catch(_){applyTheme('light');}
    $('theme_toggle').addEventListener('click',()=>{
        const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';applyTheme(theme);
        try {storage.setItem('lingsi_theme',theme);}catch(_){toast('主题已切换，但浏览器不允许保存偏好。');}
    });
    window.addEventListener('pageshow',()=>{submitting=false;document.querySelectorAll('button[type=submit]').forEach(b=>b.disabled=false);form.removeAttribute('aria-busy');$('submit_status').textContent='准备就绪';});
    window.addEventListener('resize',()=>{if(window.innerWidth>=1200)closeDrawers();});
    window.LingsiUI={renderChat,showTool,store};
    restoreSafeSettings();ingestResponse();
    // Raw diagnostics are escaped by Jinja and additionally redact active secrets.
    $('diagnostics_raw').value=redact($('diagnostics_raw').value);
    updateToolStates();renderChat();
})();
