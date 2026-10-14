(function (global) {
    'use strict';
    const KEY = 'lingsi_sessions_v2';
    function uid() { return global.crypto?.randomUUID?.() || Date.now().toString(36) + '-' + Math.random().toString(36).slice(2); }
    function safeMedia(value) {
        if (typeof value !== 'string') return '';
        if (/^\/(?:media\?|tts_audio\/)/.test(value)) return value;
        try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? value : ''; }
        catch (_) { return ''; }
    }
    function message(value) {
        if (!value || !['user', 'ai', 'assistant'].includes(value.role) || typeof value.text !== 'string') return null;
        return {id: typeof value.id === 'string' ? value.id : uid(), role: value.role === 'assistant' ? 'ai' : value.role,
            text: value.text, ts: Number.isFinite(value.ts) ? value.ts : Date.now(),
            images: Array.isArray(value.images) ? value.images.map(safeMedia).filter(Boolean) : [],
            videos: Array.isArray(value.videos) ? value.videos.map(safeMedia).filter(Boolean) : [],
            audioUrl: safeMedia(value.audioUrl)};
    }
    class LingsiStore {
        constructor(storage, warn) {
            this.storage = storage; this.warn = warn; this.blocked = false; this.revision = ""; this.lastPersisted = false;
            this.data = {version: 2, activeId: '', sessions: [], handledRequestIds: []};
            let raw = null;
            try {
                raw = storage.getItem(KEY);
                if (raw) {
                    const parsed = JSON.parse(raw);
                    if (parsed.version !== 2 || !Array.isArray(parsed.sessions)) throw Error('structure');
                    const ids = new Set();
                    for (const item of parsed.sessions) {
                        if (!item || typeof item.id !== 'string' || typeof item.title !== 'string' ||
                            !Array.isArray(item.messages) || ids.has(item.id)) throw Error('session');
                        const messages = item.messages.map(message);
                        if (messages.some(m => !m)) throw Error('message');
                        ids.add(item.id);
                        this.data.sessions.push({id:item.id,title:item.title,createdAt:Number(item.createdAt)||Date.now(),
                            updatedAt:Number(item.updatedAt)||Date.now(),messages});
                    }
                    this.revision = typeof parsed.revision === "string" ? parsed.revision : "";
                    this.data.revision = this.revision;
                    this.data.activeId = parsed.activeId;
                    this.data.handledRequestIds = Array.isArray(parsed.handledRequestIds) ? parsed.handledRequestIds.filter(x=>typeof x==='string') : [];
                } else {
                    const legacy = storage.getItem('chat_history_v1');
                    if (legacy) {
                        const values = JSON.parse(legacy);
                        if (!Array.isArray(values) || values.some(v => !message(v))) throw Error('legacy');
                        if (values.length) {
                            const messages = values.map(message);
                            const first = messages.find(m=>m.role==='user');
                            const session = {id:uid(),title:first?.text.slice(0,30)||'迁移的历史对话',
                                createdAt:messages[0].ts,updatedAt:messages.at(-1).ts,messages};
                            this.data.sessions.push(session); this.data.activeId=session.id;
                        }
                    }
                }
            } catch (_) {
                this.blocked = true; this.data.sessions = [];
                warn('浏览器历史无法读取。原始数据已保留，当前会话仅临时保存在内存中。可导出当前会话，或确认清空浏览器历史后恢复保存。');
            }
            if (!this.data.sessions.length) this.create(false);
            if (!this.find(this.data.activeId)) this.data.activeId=this.data.sessions[0].id;
            if (!this.blocked && !raw) this.persist();
        }
        persist() {
            this.lastPersisted = false;
            if (this.blocked) return false;
            try {
                const raw = this.storage.getItem(KEY);
                const remote = raw ? JSON.parse(raw) : null;
                const revision = typeof remote?.revision === "string" ? remote.revision : "";
                if (revision !== this.revision) {
                    this.blocked = true;
                    this.warn("其他页面已更新会话历史。为避免覆盖，当前更改仅临时保存在本页；请导出本页会话备份后刷新，或继续在本页临时对话。");
                    return false;
                }
                const nextRevision = uid();this.data.revision = nextRevision;
                this.storage.setItem(KEY,JSON.stringify(this.data));
                this.revision = nextRevision;this.lastPersisted = true;
                return true;
            }
            catch (_) { this.warn('浏览器存储已满或不可用，当前更改仅保存在内存中。请导出会话备份，再清理旧对话；关闭页面可能丢失未保存更改。'); return false; }
        }
        find(id) { return this.data.sessions.find(s=>s.id===id); }
        current() { return this.find(this.data.activeId); }
        create(save=true) {
            const session={id:uid(),title:'新对话',createdAt:Date.now(),updatedAt:Date.now(),messages:[]};
            this.data.sessions.unshift(session);this.data.activeId=session.id;if(save)this.persist();return session;
        }
        select(id) { if(this.find(id)){this.data.activeId=id;this.persist();} }
        rename(id,title) { const s=this.find(id);if(s && title.trim()){s.title=title.trim().slice(0,80);s.updatedAt=Date.now();this.persist();} }
        remove(id) {
            this.data.sessions=this.data.sessions.filter(s=>s.id!==id);
            if(!this.data.sessions.length)this.create(false);
            if(!this.find(this.data.activeId))this.data.activeId=this.data.sessions[0].id;
            this.persist();
        }
        append(id, value) {
            const s=this.find(id);const m=message(value);if(!s||!m)return false;
            if(s.messages.some(item=>item.id===m.id))return false;
            if(m.role==='user' && !s.messages.some(item=>item.role==='user') && s.title==='新对话')s.title=m.text.replace(/\s+/g,' ').slice(0,30)||'新对话';
            s.messages.push(m);s.updatedAt=m.ts;this.persist();return true;
        }
        markHandled(id) { if(id&&!this.isHandled(id)){this.data.handledRequestIds.push(id);this.data.handledRequestIds=this.data.handledRequestIds.slice(-300);this.persist();} }
        isHandled(id) { return this.data.handledRequestIds.includes(id); }
        clear() {
            try { this.storage.removeItem(KEY);this.storage.removeItem('chat_history_v1'); }
            catch (_) { this.warn('无法清除浏览器存储，请检查浏览器权限。');return false; }
            this.blocked=false;this.revision="";this.data={version:2,activeId:'',sessions:[],handledRequestIds:[]};this.create();return true;
        }
        export() {
            const data = {...this.data};
            if (this.blocked) {
                try { data.recovery = {sessionsRaw:this.storage.getItem(KEY),legacyRaw:this.storage.getItem("chat_history_v1")}; }
                catch (_) {}
            }
            return JSON.stringify(data,null,2);
        }
    }
    global.LingsiStore = LingsiStore;
    global.LingsiSafeMedia = safeMedia;
    global.LingsiUid = uid;
})(typeof window !== 'undefined' ? window : globalThis);
