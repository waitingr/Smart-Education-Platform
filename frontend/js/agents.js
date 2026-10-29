/* ============================================================
 * 灵眸智教 · 学科智能体前端
 * 在 main.js 之后加载：复用其登录态、Toast 与资源页逻辑，
 * 负责智能体工作台、多轮对话、知识库管理、使用看板与学习画像。
 * ============================================================ */
'use strict';

const API_BASE = (location.protocol === 'file:' ? 'http://localhost:5000' : (location.origin || 'http://localhost:5000')) + '/api/v1';

const WS = {
    catalog: null,
    currentAgent: { learn: 'tutor', teach: 'lesson_plan', research: 'frontier' },
    conversationId: null,
    chatAgent: 'tutor',
    lastResult: null,
    uploading: {},
};

// ==================== 基础工具 ====================
function authHeaders(extra) {
    const headers = Object.assign({}, extra || {});
    const token = localStorage.getItem('api_token');
    if (token) headers['Authorization'] = 'Bearer ' + token;
    return headers;
}

async function apiGet(path) {
    const response = await fetch(API_BASE + path, { headers: authHeaders(), credentials: 'include' });
    const body = await safeJson(response);
    if (!response.ok || (body.code && body.code >= 400)) throw new Error(body.message || ('请求失败 ' + response.status));
    return body.data;
}

async function apiSend(path, payload, method) {
    const response = await fetch(API_BASE + path, {
        method: method || 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        credentials: 'include',
        body: JSON.stringify(payload || {}),
    });
    const body = await safeJson(response);
    if (!response.ok || (body.code && body.code >= 400)) throw new Error(body.message || ('请求失败 ' + response.status));
    return body.data;
}

async function apiUpload(path, formData) {
    const response = await fetch(API_BASE + path, { method: 'POST', headers: authHeaders(), credentials: 'include', body: formData });
    const body = await safeJson(response);
    if (!response.ok || (body.code && body.code >= 400)) throw new Error(body.message || ('上传失败 ' + response.status));
    return body.data;
}

async function safeJson(response) {
    try { return await response.json(); } catch (error) { return {}; }
}

function esc(text) {
    return String(text == null ? '' : text)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function md(text) {
    if (!text) return '';
    if (window.marked) {
        try { return marked.parse(text); } catch (error) { /* 退化为纯文本 */ }
    }
    return '<pre class="whitespace-pre-wrap text-sm leading-relaxed">' + esc(text) + '</pre>';
}

function paint(el) {
    if (!el) return;
    el.querySelectorAll('pre code').forEach(block => {
        try { if (window.hljs) hljs.highlightElement(block); } catch (error) { /* 忽略高亮失败 */ }
    });
}

function notify(message, type) {
    if (typeof showToast === 'function') showToast(message, type || 'success');
}

function stamp(ms) {
    if (ms == null) return '';
    return ms < 1000 ? Math.round(ms) + ' ms' : (ms / 1000).toFixed(1) + ' s';
}

const SOURCE_ICON = { textbook: 'ph-book-open-text', standard: 'ph-seal-check', outline: 'ph-list-dashes', paper: 'ph-file-text', case: 'ph-warning-circle', other: 'ph-file' };

// ==================== 页面切换（覆盖 main.js 默认实现） ====================
const PUBLIC_PAGES = ['ai-chat', 'analytics', 'workspace-learn', 'workspace-research', 'profile'];

function switchPage(target) {
    if (!PUBLIC_PAGES.includes(target) && AppState.isGuest) {
        notify('该功能需要登录后使用', 'warning');
        if (typeof requireLogin === 'function') requireLogin();
        return;
    }
    localStorage.setItem('active_page', target);
    document.querySelectorAll('.sidebar-item').forEach(item => item.classList.remove('active'));
    const navItem = document.querySelector('.sidebar-item[data-page="' + target + '"]');
    if (navItem) navItem.classList.add('active');
    document.querySelectorAll('.page-content').forEach(page => page.classList.add('hidden'));
    const page = document.getElementById('page-' + target);
    if (!page) return;
    page.classList.remove('hidden');
    page.scrollTop = 0;

    if (target === 'video' && typeof loadVideos === 'function') loadVideos();
    if (target === 'material' && typeof loadMaterials === 'function') loadMaterials();
    if (target === 'homework' && typeof loadHomeworks === 'function') loadHomeworks();
    if (target === 'course' && typeof loadCoursePage === 'function') loadCoursePage();
    if (target.indexOf('workspace-') === 0) renderWorkspace(target.replace('workspace-', ''));
    if (target === 'kb') renderKb();
    if (target === 'analytics') renderAnalytics();
    if (target === 'profile') renderProfile();
    if (target === 'ai-chat') refreshConversationList();
}

// ==================== 智能体工作台 ====================
async function loadCatalog() {
    if (WS.catalog) return WS.catalog;
    const [catalog, meta] = await Promise.all([apiGet('/agents'), apiGet('/meta').catch(() => null)]);
    WS.catalog = catalog;
    WS.meta = meta;
    return catalog;
}

async function renderWorkspace(scenario) {
    const root = document.getElementById('ws-' + scenario);
    if (!root) return;
    if (!root.dataset.ready) {
        root.innerHTML = '<div class="text-sm text-gray-400 py-16 text-center"><i class="ph ph-spinner-gap animate-spin mr-2"></i>正在加载智能体…</div>';
        try {
            await loadCatalog();
        } catch (error) {
            root.innerHTML = '<div class="text-sm text-red-500 py-10 text-center">加载失败：' + esc(error.message) + '</div>';
            return;
        }
        root.dataset.ready = '1';
    }
    const group = (WS.catalog.scenarios || []).find(item => item.id === scenario);
    if (!group) return;
    const agents = group.agents || [];
    if (!agents.some(item => item.id === WS.currentAgent[scenario])) {
        WS.currentAgent[scenario] = agents[0] ? agents[0].id : '';
    }
    const active = agents.find(item => item.id === WS.currentAgent[scenario]) || agents[0] || {};

    root.innerHTML = `
    <div class="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div class="flex items-center gap-3">
            <div class="w-11 h-11 rounded-xl bg-gradient-to-br from-[#2d8cf0] to-[#5aa9f7] flex items-center justify-center text-white shadow-md">
                <i class="ph ${esc(group.icon || 'ph-robot')} text-xl"></i>
            </div>
            <div>
                <h2 class="text-xl font-bold text-gray-900">${esc(group.name)}</h2>
                <p class="text-xs text-gray-400 mt-0.5">${esc(group.desc || '')}</p>
            </div>
        </div>
        <div class="flex items-center gap-2 text-[11px]">
            <span class="px-2 py-1 rounded-full bg-[#f0f7ff] text-[#2d8cf0]"><i class="ph ph-tree-structure mr-1"></i>${agents.length} 个智能体</span>
            <span class="px-2 py-1 rounded-full bg-emerald-50 text-emerald-600" id="ws-llm-badge"><i class="ph ph-cpu mr-1"></i>检测模型中…</span>
        </div>
    </div>
    <div class="grid grid-cols-1 lg:grid-cols-[260px_minmax(0,1fr)] gap-4 items-start">
        <div class="bg-white border border-gray-200 rounded-xl p-2 shadow-sm space-y-1">${agents.map(item => `
            <button class="w-full text-left px-3 py-2.5 rounded-lg transition-colors ${item.id === active.id ? 'bg-[#f0f7ff] text-[#2d8cf0]' : 'hover:bg-gray-50 text-gray-700'}" onclick="selectAgent('${scenario}','${item.id}')">
                <div class="flex items-center gap-2 text-sm font-medium"><i class="ph ${esc(item.icon || 'ph-robot')}"></i>${esc(item.name)}</div>
                <div class="text-[11px] text-gray-400 mt-1 leading-snug">${esc(item.summary || '')}</div>
            </button>`).join('')}
        </div>
        <div class="space-y-4 min-w-0">
            <div class="bg-white border border-gray-200 rounded-xl shadow-sm">
                <div class="px-5 pt-4 pb-3 border-b border-gray-100">
                    <h3 class="font-semibold text-gray-900 text-sm flex items-center gap-2"><i class="ph ${esc(active.icon || 'ph-robot')} text-[#2d8cf0]"></i>${esc(active.name || '')}</h3>
                    <p class="text-xs text-gray-500 mt-1 leading-relaxed">${esc(active.summary || '')}</p>
                    ${(active.steps || []).length ? `<div class="mt-2 flex flex-wrap gap-1.5 text-[10px] text-gray-500">${active.steps.map((step, index) => `<span class="px-2 py-0.5 rounded-full bg-gray-50 border border-gray-100">${index + 1}. ${esc(step)}</span>`).join('')}</div>` : ''}
                </div>
                <div class="p-5 space-y-3" id="ws-form">${renderFields(active)}</div>
                <div class="px-5 pb-5 flex items-center justify-between gap-3">
                    <span class="text-[11px] text-gray-400"><i class="ph ph-info mr-1"></i>输出内容由 AI 生成，请结合实际教学判断</span>
                    <button class="px-4 py-2 rounded-lg bg-[#2d8cf0] text-white text-sm font-medium hover:bg-[#2080e0] transition-colors disabled:opacity-50" id="ws-run-btn" onclick="runWorkspaceAgent('${scenario}')">
                        <i class="ph ph-play mr-1"></i>运行工作流
                    </button>
                </div>
            </div>
            <div id="ws-result"></div>
        </div>
    </div>`;
    refreshLlmBadge(scenario);
    const previous = WS.lastResult && WS.lastResult.scenario === scenario ? WS.lastResult : null;
    if (previous) {
        const box = document.getElementById('ws-result');
        if (box) box.innerHTML = previous.html;
        paint(box);
    }
}

function renderFields(agent) {
    const inputs = agent.inputs || [];
    if (!inputs.length) return '<p class="text-xs text-gray-400">该智能体无需额外输入，直接运行即可。</p>';
    return inputs.map(field => {
        const id = 'ws-field-' + field.key;
        const value = field.default != null ? field.default : '';
        const required = field.required ? '<span class="text-red-500">*</span>' : '';
        let control = '';
        if (field.type === 'select') {
            control = `<select id="${id}" class="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-[#2d8cf0] bg-white">${(field.options || []).map(option => `<option value="${esc(option)}" ${option === value ? 'selected' : ''}>${esc(option)}</option>`).join('')}</select>`;
        } else if (field.type === 'textarea') {
            control = `<textarea id="${id}" rows="3" placeholder="${esc(field.placeholder || '')}" class="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-[#2d8cf0] resize-y">${esc(value)}</textarea>`;
        } else if (field.type === 'number') {
            control = `<input id="${id}" type="number" value="${esc(value)}" class="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-[#2d8cf0]">`;
        } else if (field.type === 'file') {
            control = `<div class="flex items-center gap-2">
                <input id="${id}" type="file" onchange="handleAgentFile(this,'${esc(field.key)}')" class="block w-full text-xs text-gray-500 file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:bg-[#f0f7ff] file:text-[#2d8cf0] hover:file:bg-[#e2effd]">
                <span class="text-[11px] text-gray-400" id="${id}-name"></span></div>`;
        } else if (field.type === 'switch') {
            control = `<label class="inline-flex items-center gap-2 text-sm text-gray-600"><input id="${id}" type="checkbox" ${value ? 'checked' : ''} class="rounded border-gray-300">${esc(field.help || '启用')}</label>`;
        } else {
            control = `<input id="${id}" type="text" value="${esc(value)}" placeholder="${esc(field.placeholder || '')}" class="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-[#2d8cf0]">`;
        }
        return `<div><label class="block text-xs font-medium text-gray-600 mb-1.5">${esc(field.label || field.key)} ${required}</label>${control}
            ${field.help ? `<p class="text-[11px] text-gray-400 mt-1">${esc(field.help)}</p>` : ''}</div>`;
    }).join('');
}

function selectAgent(scenario, agentId) {
    WS.currentAgent[scenario] = agentId;
    WS.lastResult = null;
    document.getElementById('ws-' + scenario).dataset.ready = '';
    renderWorkspace(scenario);
}

async function handleAgentFile(input, key) {
    const file = input.files && input.files[0];
    if (!file) return;
    const label = document.getElementById(input.id + '-name');
    if (label) label.textContent = '上传中…';
    try {
        const formData = new FormData();
        formData.append('file', file);
        const data = await apiUpload('/attachments/upload', formData);
        WS.uploading[key] = data.filename;
        if (label) label.innerHTML = '<i class="ph ph-check-circle text-emerald-500"></i> ' + esc(data.name);
    } catch (error) {
        if (label) label.textContent = '上传失败：' + error.message;
        notify('附件上传失败：' + error.message, 'error');
    }
}

function collectInputs(agent) {
    const inputs = {};
    (agent.inputs || []).forEach(field => {
        const element = document.getElementById('ws-field-' + field.key);
        if (field.type === 'file') {
            if (WS.uploading[field.key]) inputs.attachment_name = WS.uploading[field.key];
            return;
        }
        if (!element) return;
        if (field.type === 'switch') { inputs[field.key] = element.checked; return; }
        const value = (element.value || '').trim();
        if (value) inputs[field.key] = value;
    });
    return inputs;
}

async function runWorkspaceAgent(scenario) {
    const group = (WS.catalog.scenarios || []).find(item => item.id === scenario);
    const agent = (group.agents || []).find(item => item.id === WS.currentAgent[scenario]);
    if (!agent) return;
    const button = document.getElementById('ws-run-btn');
    const box = document.getElementById('ws-result');
    if (button) { button.disabled = true; button.innerHTML = '<i class="ph ph-spinner-gap animate-spin mr-1"></i>执行中…'; }
    if (box) box.innerHTML = renderSkeleton(agent.steps || []);

    try {
        const payload = await apiSend('/agents/' + agent.id + '/run', { inputs: collectInputs(agent), conversation_id: WS.conversationId });
        WS.conversationId = payload.conversation_id || WS.conversationId;
        const html = renderResult(payload, agent, scenario);
        WS.lastResult = { scenario: scenario, html: html };
        if (box) { box.innerHTML = html; paint(box); }
        // 保持页面本身的滚动位置，让用户看到结果顶部
        box.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) {
        if (box) box.innerHTML = '<div class="bg-white border border-red-200 rounded-xl p-5 text-sm text-red-600"><i class="ph ph-warning-circle mr-1"></i>' + esc(error.message) + '</div>';
    } finally {
        if (button) { button.disabled = false; button.innerHTML = '<i class="ph ph-play mr-1"></i>运行工作流'; }
    }
}

function renderSkeleton(steps) {
    const items = (steps && steps.length ? steps : ['检索学科知识库', '调用大模型生成', '引用核验与合规检查']);
    return `<div class="bg-white border border-gray-200 rounded-xl p-5 shadow-sm">
        <div class="flex items-center gap-2 text-sm font-medium text-[#2d8cf0] mb-3"><i class="ph ph-spinner-gap animate-spin"></i>工作流执行中</div>
        <div class="space-y-2">${items.map((step, index) => `<div class="flex items-center gap-2 text-xs text-gray-500">
            <span class="w-5 h-5 rounded-full bg-[#f0f7ff] text-[#2d8cf0] flex items-center justify-center text-[10px]">${index + 1}</span>
            <span class="h-2 rounded shimmer flex-1"></span></div>`).join('')}</div>
    </div>`;
}

// ==================== 结果渲染（工作台与对话共用） ====================
function renderResult(payload, agent, scenario) {
    const citations = payload.citations || [];
    const trace = payload.trace || [];
    const facts = payload.facts || [];
    const artifacts = payload.artifacts || [];
    const providerText = payload.degraded ? '离线检索模式'
        : (payload.provider === 'ollama' ? '本机视觉大模型'
        : (payload.provider === 'local' ? '本地大模型'
        : (payload.provider === 'spark' ? '讯飞星火' : '在线大模型')));
    const providerClass = payload.degraded ? 'bg-amber-50 text-amber-600' : 'bg-emerald-50 text-emerald-600';

    return `<div class="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
        <div class="px-5 py-3 border-b border-gray-100 flex flex-wrap items-center gap-2 text-[11px]">
            <span class="px-2 py-1 rounded-full ${providerClass}"><i class="ph ph-cpu mr-1"></i>${esc(providerText)}</span>
            ${payload.latency_ms ? `<span class="px-2 py-1 rounded-full bg-gray-50 text-gray-500"><i class="ph ph-timer mr-1"></i>${stamp(payload.latency_ms)}</span>` : ''}
            <span class="px-2 py-1 rounded-full bg-gray-50 text-gray-500"><i class="ph ph-quotes mr-1"></i>${citations.length} 条引用</span>
            <span class="px-2 py-1 rounded-full bg-gray-50 text-gray-500"><i class="ph ph-tree-structure mr-1"></i>${trace.length} 步工作流</span>
            <span class="ml-auto text-gray-400">AI 生成内容 · 请核对引用</span>
        </div>
        <div class="px-5 py-4">
            <div class="prose prose-sm max-w-none ws-answer">${md(payload.answer || '')}</div>
        </div>
        ${artifacts.length ? `<div class="px-5 pb-4 space-y-3">${artifacts.map(renderArtifact).join('')}</div>` : ''}
        ${facts.length ? `<div class="px-5 pb-4">
            <details class="group rounded-lg border border-gray-100 bg-gray-50/60">
                <summary class="cursor-pointer px-3 py-2 text-xs font-medium text-gray-600 select-none"><i class="ph ph-function mr-1"></i>工作流计算结果（${facts.length}）</summary>
                <ul class="px-5 pb-3 pt-1 space-y-1 text-xs text-gray-600 list-disc">${facts.map(fact => `<li>${esc(fact)}</li>`).join('')}</ul>
            </details></div>` : ''}
        ${citations.length ? `<div class="px-5 pb-4">
            <div class="text-xs font-semibold text-gray-500 mb-2"><i class="ph ph-books mr-1"></i>引用与出处（点击可核查原文）</div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-2">${citations.map(citationCard).join('')}</div></div>` : ''}
        ${trace.length ? `<div class="px-5 pb-4">
            <details class="rounded-lg border border-gray-100">
                <summary class="cursor-pointer px-3 py-2 text-xs font-medium text-gray-600 select-none"><i class="ph ph-path mr-1"></i>工作流执行轨迹（可追溯）</summary>
                <ol class="px-4 py-2 space-y-2">${trace.map(step => `<li class="flex items-start gap-2 text-xs">
                    <span class="mt-0.5 w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0 ${step.status === 'blocked' ? 'bg-red-50 text-red-500' : (step.status === 'warn' ? 'bg-amber-50 text-amber-500' : 'bg-[#f0f7ff] text-[#2d8cf0]')}"><i class="ph ${traceIcon(step.kind)} text-[11px]"></i></span>
                    <span class="min-w-0"><span class="font-medium text-gray-700">${esc(step.title || '')}</span>
                    <span class="text-gray-500"> · ${esc(step.detail || '')}</span>
                    ${step.latency ? `<span class="text-gray-300 ml-1">${stamp(step.latency)}</span>` : ''}</span></li>`).join('')}</ol>
            </details></div>` : ''}
        <div class="px-5 py-3 border-t border-gray-100 flex flex-wrap items-center gap-2 bg-gray-50/50">
            <span class="text-[11px] text-gray-400 mr-auto">这条结果对您有帮助吗？</span>
            <button class="px-2.5 py-1.5 rounded-lg text-xs border border-gray-200 hover:border-emerald-400 hover:text-emerald-600 transition-colors" onclick="sendFeedback(1,this,'${esc(payload.agent_id || '')}','${esc(payload.conversation_id || '')}','${esc(payload.message_id || '')}')"><i class="ph ph-thumbs-up mr-1"></i>有帮助</button>
            <button class="px-2.5 py-1.5 rounded-lg text-xs border border-gray-200 hover:border-amber-400 hover:text-amber-600 transition-colors" onclick="sendFeedback(-1,this,'${esc(payload.agent_id || '')}','${esc(payload.conversation_id || '')}','${esc(payload.message_id || '')}')"><i class="ph ph-thumbs-down mr-1"></i>需改进</button>
            <button class="px-2.5 py-1.5 rounded-lg text-xs border border-gray-200 hover:border-[#2d8cf0] hover:text-[#2d8cf0] transition-colors" onclick="copyResult(this)"><i class="ph ph-copy mr-1"></i>复制</button>
            <button class="px-2.5 py-1.5 rounded-lg text-xs border border-gray-200 hover:border-[#2d8cf0] hover:text-[#2d8cf0] transition-colors" onclick="downloadResult(this)"><i class="ph ph-download-simple mr-1"></i>导出 Markdown</button>
        </div>
        <div data-answer hidden>${esc(payload.answer || '')}</div>
        <div data-citations hidden>${esc(JSON.stringify(citations))}</div>
    </div>`;
}

function traceIcon(kind) {
    return ({ retrieval: 'ph-magnifying-glass', generation: 'ph-sparkle', tool: 'ph-wrench', guard: 'ph-shield-check', branch: 'ph-git-branch', analyze: 'ph-chart-bar' })[kind] || 'ph-dot';
}

function citationCard(citation) {
    const stars = '★'.repeat(Math.max(1, Math.min(5, citation.authority || 3)));
    return `<div class="rounded-lg border border-gray-100 bg-gray-50/60 p-2.5 hover:border-[#2d8cf0] transition-colors">
        <div class="flex items-start gap-2">
            <span class="px-1.5 py-0.5 rounded bg-[#ebf4ff] text-[#2d8cf0] text-[10px] font-semibold flex-shrink-0">[${citation.index}]</span>
            <div class="min-w-0 flex-1">
                <div class="text-xs font-medium text-gray-800 truncate" title="${esc(citation.locator)}">${esc(citation.locator || citation.doc_title || '')}</div>
                <div class="text-[11px] text-gray-500 mt-1 leading-snug line-clamp-2">${esc((citation.snippet || citation.text || '').slice(0, 120))}</div>
                <div class="flex items-center gap-2 mt-1.5 text-[10px] text-gray-400">
                    <span class="inline-flex items-center gap-0.5"><i class="ph ${SOURCE_ICON[citation.source_type] || 'ph-file'}"></i>${esc(citation.source_type_label || '资料')}</span>
                    ${citation.page ? `<span>第 ${citation.page} 页</span>` : ''}
                    ${citation.year ? `<span>${citation.year}</span>` : ''}
                    <span class="text-amber-400">${stars}</span>
                    <span class="ml-auto">相关度 ${(citation.score || 0).toFixed(2)}</span>
                </div>
            </div>
        </div>
    </div>`;
}

function renderArtifact(artifact) {
    if (!artifact || !artifact.type) return '';
    if (artifact.type === 'kpi') {
        return `<div class="rounded-lg border border-gray-100 bg-gray-50/60 p-3"><div class="grid grid-cols-2 sm:grid-cols-4 gap-3">${(artifact.items || []).map(item => `
            <div class="text-center"><div class="text-lg font-bold text-[#2d8cf0]">${esc(item.value)}</div><div class="text-[11px] text-gray-500 mt-0.5">${esc(item.label)}</div></div>`).join('')}</div></div>`;
    }
    if (artifact.type === 'bar') {
        const values = artifact.values || [];
        const max = Math.max.apply(null, values.concat([1]));
        return `<div class="rounded-lg border border-gray-100 p-3">
            <div class="text-xs font-medium text-gray-600 mb-2">${esc(artifact.title || '统计图')}</div>
            <div class="flex items-end gap-3 h-28">${values.map((value, index) => {
                const height = Math.round((value / max) * 100);
                return `<div class="flex-1 flex flex-col items-center justify-end h-full">
                    <span class="text-[10px] text-gray-500 mb-1">${esc(value)}</span>
                    <div class="w-full rounded-t bg-gradient-to-t from-[#2d8cf0] to-[#7bbcf9]" style="height:${Math.max(3, height)}%"></div>
                    <span class="text-[10px] text-gray-400 mt-1 truncate max-w-full">${esc((artifact.labels || [])[index] || '')}</span></div>`;
            }).join('')}</div></div>`;
    }
    if (artifact.type === 'timeline') {
        return `<div class="rounded-lg border border-gray-100 p-3">
            <div class="text-xs font-medium text-gray-600 mb-2">${esc(artifact.title || '时间线')}</div>
            <ol class="relative border-l border-gray-200 ml-2 space-y-2.5">${(artifact.items || []).map(item => `
                <li class="ml-4"><span class="absolute -left-[5px] w-2.5 h-2.5 rounded-full bg-[#2d8cf0]"></span>
                <div class="text-xs font-medium text-gray-800">${esc(item.title || '')}</div>
                <div class="text-[11px] text-gray-500 leading-snug">${esc(item.detail || '')}</div></li>`).join('')}</ol></div>`;
    }
    if (artifact.type === 'tags') {
        return `<div class="rounded-lg border border-gray-100 p-3">
            <div class="text-xs font-medium text-gray-600 mb-2">${esc(artifact.title || '关键词')}</div>
            <div class="flex flex-wrap gap-1.5">${(artifact.items || []).map(item => `<span class="px-2 py-1 rounded-full bg-[#f0f7ff] text-[#2d8cf0] text-[11px]">${esc(item.label)}</span>`).join('')}</div></div>`;
    }
    if (artifact.type === 'table') {
        return `<div class="rounded-lg border border-gray-100 p-3 overflow-x-auto">
            <div class="text-xs font-medium text-gray-600 mb-2">${esc(artifact.title || '数据表')}</div>
            <table class="min-w-full text-[11px]"><thead><tr>${(artifact.columns || []).map(column => `<th class="px-2 py-1 text-left font-medium text-gray-500 border-b border-gray-100">${esc(column)}</th>`).join('')}</tr></thead>
            <tbody>${(artifact.rows || []).map(row => `<tr>${row.map(cell => `<td class="px-2 py-1 border-b border-gray-50 text-gray-600">${esc(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
    }
    return '';
}

async function sendFeedback(rating, button, agentId, conversationId, messageId) {
    try {
        await apiSend('/feedback', {
            rating: rating,
            usefulness: rating > 0 ? 5 : 2,
            agent_id: agentId || undefined,
            conversation_id: conversationId || undefined,
            message_id: messageId || undefined,
        });
        if (button && button.parentElement) {
            button.parentElement.querySelectorAll('button').forEach(item => { if (item.dataset.fb) item.remove(); });
            const tip = document.createElement('span');
            tip.dataset.fb = '1';
            tip.className = 'text-[11px] text-emerald-600';
            tip.innerHTML = '<i class="ph ph-check-circle mr-1"></i>已记录，感谢反馈';
            button.parentElement.appendChild(tip);
        }
        notify('感谢反馈，已记录到效果统计');
    } catch (error) {
        notify(error.message, 'error');
    }
}

function answerText(button) {
    const box = button.closest('.bg-white');
    const holder = box ? box.querySelector('[data-answer]') : null;
    return holder ? holder.textContent : '';
}

function copyResult(button) {
    const text = answerText(button);
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => notify('已复制到剪贴板')).catch(() => notify('复制失败', 'error'));
}

function downloadResult(button) {
    const text = answerText(button);
    if (!text) return;
    const header = '> 本文由灵眸智教 AI 辅助生成（学科垂类大模型），引用出处已在文末标注，请核查后使用。\n\n';
    const blob = new Blob([header + text], { type: 'text/markdown;charset=utf-8' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = '灵眸智教-生成结果-' + new Date().toISOString().slice(0, 10) + '.md';
    link.click();
    URL.revokeObjectURL(link.href);
}

async function refreshLlmBadge(scenario) {
    const badge = document.getElementById('ws-llm-badge');
    if (!badge) return;
    try {
        const health = await apiGet('/health/stack');
        const llm = health.llm || {};
        const vision = health.vision || {};
        badge.className = 'px-2 py-1 rounded-full ' + (llm.available ? 'bg-emerald-50 text-emerald-600' : 'bg-amber-50 text-amber-600');
        const label = llm.available
            ? '模型已接入：' + (llm.model || '') + (llm.vision ? '（可看图）' : '')
            : '离线检索模式';
        badge.innerHTML = '<i class="ph ph-cpu mr-1"></i>' + esc(label);
        badge.title = llm.notice || (vision.available ? '视觉模型：' + (vision.model || '') : '');
    } catch (error) { badge.textContent = '模型状态未知'; }
}

// 设置面板里的模型信息：全部来自后端 /health/stack，界面不写死模型名
async function refreshModelPanel() {
    const modelEl = document.getElementById('model-version-display');
    const visionEl = document.getElementById('vision-capability-display');
    if (!modelEl && !visionEl) return;
    try {
        const health = await apiGet('/health/stack');
        const llm = health.llm || {};
        const vision = health.vision || {};
        if (modelEl) {
            modelEl.textContent = llm.available
                ? (llm.provider || '') + ' / ' + (llm.model || '')
                : '未接入（离线检索模式）';
            modelEl.title = llm.notice || '由本机 Ollama 提供';
        }
        if (visionEl) {
            const canSee = vision.available && llm.vision !== false;
            visionEl.textContent = canSee ? '可识图（' + (vision.model || '') + '）' : '当前模型不支持识图';
            visionEl.className = 'font-medium text-xs ' + (canSee ? 'text-emerald-600' : 'text-amber-600');
        }
    } catch (error) {
        if (modelEl) modelEl.textContent = '状态读取失败';
        if (visionEl) visionEl.textContent = '状态读取失败';
    }
}

// ==================== 多轮对话 ====================
function quickAsk(text) {
    if (typeof switchPage === 'function') switchPage('ai-chat');
    const input = document.getElementById('user-input');
    if (!input) return;
    input.value = text;
    input.dispatchEvent(new Event('input'));
    sendChatMessage();
}

function appendChatMessage(role, html, options) {
    const stream = document.getElementById('chat-stream');
    if (!stream) return null;
    const welcome = document.getElementById('chat-welcome');
    if (welcome) welcome.remove();
    options = options || {};
    const wrapper = document.createElement('div');
    wrapper.className = 'flex items-start max-w-3xl mx-auto msg-enter ' + (role === 'user' ? 'justify-end' : '');
    if (role === 'user') {
        wrapper.innerHTML = `<div class="bg-[#2d8cf0] text-white rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm max-w-[85%] whitespace-pre-wrap break-words">${esc(html)}</div>`;
    } else {
        wrapper.innerHTML = `<div class="w-8 h-8 rounded-full bg-[#ebf4ff] flex items-center justify-center text-[#2d8cf0] mr-3 mt-1 flex-shrink-0"><i class="ph ph-robot text-lg"></i></div>
            <div class="min-w-0 flex-1 max-w-full">${html}</div>`;
    }
    stream.appendChild(wrapper);
    stream.scrollTop = stream.scrollHeight;
    paint(wrapper);
    return wrapper;
}

function chatLoader() {
    const wrapper = appendChatMessage('ai', `<div class="bg-white border border-gray-200 rounded-xl rounded-tl-none px-5 py-4 shadow-sm">
        <div class="flex items-center text-xs font-medium text-[#2d8cf0] mb-2"><i class="ph ph-spinner-gap animate-spin mr-2"></i>正在检索知识库并执行工作流…</div>
        <div class="space-y-2"><div class="h-2.5 w-full rounded shimmer"></div><div class="h-2.5 w-3/4 rounded shimmer"></div></div></div>`);
    return wrapper;
}

async function sendChatMessage() {
    const input = document.getElementById('user-input');
    const text = input ? input.value.trim() : '';
    if (!text || AppState.isGenerating) return;
    if (AppState.isGuest && AppState.guestQueryCount >= MAX_GUEST_QUERIES) {
        notify('游客提问次数已用完，请登录后继续', 'warning');
        if (typeof requireLogin === 'function') requireLogin();
        return;
    }
    AppState.isGenerating = true;
    if (input) { input.value = ''; input.dispatchEvent(new Event('input')); input.disabled = true; }
    if (AppState.isGuest) {
        AppState.guestQueryCount++;
        localStorage.setItem('guest_query_count', String(AppState.guestQueryCount));
    }

    appendChatMessage('user', text);
    const loader = chatLoader();
    try {
        const payload = await apiSend('/chat', {
            prompt: text,
            mode: AppState.currentMode || 'qa',
            conversation_id: WS.conversationId,
            attachment_name: (typeof currentAttachment === 'object' && currentAttachment && currentAttachment.filename) || '',
        });
        WS.conversationId = payload.conversation_id || WS.conversationId;
        const html = renderChatAnswer(payload);
        if (loader) {
            loader.innerHTML = `<div class="w-8 h-8 rounded-full bg-[#ebf4ff] flex items-center justify-center text-[#2d8cf0] mr-3 mt-1 flex-shrink-0"><i class="ph ph-robot text-lg"></i></div>
                <div class="min-w-0 flex-1 max-w-full">${html}</div>`;
            paint(loader);
            const stream = document.getElementById('chat-stream');
            if (stream) stream.scrollTop = stream.scrollHeight;
        }
        if (typeof clearAttachment === 'function') clearAttachment();
        refreshConversationList();
    } catch (error) {
        if (loader) loader.innerHTML = `<div class="w-8 h-8 rounded-full bg-red-50 flex items-center justify-center text-red-500 mr-3 mt-1 flex-shrink-0"><i class="ph ph-warning text-lg"></i></div>
            <div class="min-w-0 flex-1"><div class="bg-red-50 border border-red-100 rounded-xl px-4 py-3 text-sm text-red-600">${esc(error.message)}</div></div>`;
    } finally {
        AppState.isGenerating = false;
        if (input) { input.disabled = false; input.focus(); }
    }
}

function renderChatAnswer(payload) {
    const citations = payload.citations || [];
    const suggestions = payload.suggestions || [];
    const degraded = payload.degraded ? `<div class="mb-2 px-3 py-2 rounded-lg bg-amber-50 text-amber-700 text-[11px] flex items-start gap-1.5"><i class="ph ph-info mt-0.5"></i><span>当前为离线检索模式（未连接大模型），内容由学科知识库原文结构化编排，引用可核查。</span></div>` : '';
    return `<div class="bg-white border border-gray-200 rounded-xl rounded-tl-none px-4 py-3 shadow-sm">
        ${degraded}
        <div class="prose prose-sm max-w-none">${md(payload.content || '')}</div>
        ${citations.length ? `<details class="mt-3 rounded-lg border border-gray-100 bg-gray-50/60">
            <summary class="cursor-pointer px-3 py-2 text-[11px] font-medium text-gray-600 select-none"><i class="ph ph-books mr-1"></i>引用出处（${citations.length}）</summary>
            <div class="px-3 pb-3 grid grid-cols-1 md:grid-cols-2 gap-2">${citations.map(citationCard).join('')}</div>
        </details>` : ''}
        ${(payload.trace || []).length ? `<details class="mt-2 rounded-lg border border-gray-100">
            <summary class="cursor-pointer px-3 py-2 text-[11px] font-medium text-gray-600 select-none"><i class="ph ph-path mr-1"></i>执行轨迹（${payload.trace.length} 步）</summary>
            <ol class="px-3 pb-3 space-y-1.5">${payload.trace.map(step => `<li class="text-[11px] text-gray-500"><span class="text-gray-700 font-medium">${esc(step.title || '')}</span> · ${esc(step.detail || '')}</li>`).join('')}</ol>
        </details>` : ''}
        ${suggestions.length ? `<div class="mt-3 flex flex-wrap gap-1.5">${suggestions.map(item => `<button data-q="${esc(item)}" class="px-2.5 py-1 rounded-full border border-gray-200 text-[11px] text-gray-600 hover:border-[#2d8cf0] hover:text-[#2d8cf0] transition-colors" onclick="quickAsk(this.dataset.q)">${esc(item)}</button>`).join('')}</div>` : ''}
        <div class="mt-2.5 pt-2 border-t border-gray-100 flex items-center gap-2">
            <span class="text-[10px] text-gray-400 mr-auto"><i class="ph ph-sparkle mr-0.5"></i>AI 生成内容，请核查引用后使用</span>
            <button class="px-2 py-1 rounded text-[11px] text-gray-500 hover:text-emerald-600" onclick="sendFeedback(1,this,'','${esc(payload.conversation_id || '')}','${esc(payload.message_id || '')}')"><i class="ph ph-thumbs-up"></i></button>
            <button class="px-2 py-1 rounded text-[11px] text-gray-500 hover:text-amber-600" onclick="sendFeedback(-1,this,'','${esc(payload.conversation_id || '')}','${esc(payload.message_id || '')}')"><i class="ph ph-thumbs-down"></i></button>
            <button class="px-2 py-1 rounded text-[11px] text-gray-500 hover:text-[#2d8cf0]" onclick="copyResult(this)"><i class="ph ph-copy"></i></button>
        </div>
        <div data-answer hidden>${esc(payload.content || '')}</div>
    </div>`;
}

async function refreshConversationList() {
    const holder = document.getElementById('history-list');
    if (!holder) return;
    try {
        const data = await apiGet('/conversations?limit=8');
        const items = data.conversations || [];
        if (!items.length) {
            holder.innerHTML = '<div class="text-xs text-gray-400 py-1.5 px-1">暂无历史记录</div>';
            return;
        }
        holder.innerHTML = items.map(item => `<div class="text-xs text-gray-600 hover:text-[#2d8cf0] hover:bg-[#f0f7ff] rounded-md px-2 py-1.5 cursor-pointer truncate transition-colors" title="${esc(item.title || '')}" onclick="openConversation('${esc(item.id)}')">
            <i class="ph ph-chat-circle-dots mr-1"></i>${esc(item.title || '未命名会话')}</div>`).join('');
    } catch (error) {
        holder.innerHTML = '<div class="text-xs text-gray-400 py-1.5 px-1">历史记录需登录后查看</div>';
    }
}

async function openConversation(id) {
    switchPage('ai-chat');
    try {
        const data = await apiGet('/conversations/' + id);
        WS.conversationId = data.id || Number(id);
        const stream = document.getElementById('chat-stream');
        if (!stream) return;
        stream.innerHTML = '';
        (data.messages || []).forEach(message => {
            if (message.role === 'user') {
                appendChatMessage('user', message.content || '');
            } else {
                appendChatMessage('ai', `<div class="bg-white border border-gray-200 rounded-xl rounded-tl-none px-4 py-3 shadow-sm"><div class="prose prose-sm max-w-none">${md(message.content || '')}</div><div data-answer hidden>${esc(message.content || '')}</div></div>`);
            }
        });
        notify('已载入历史会话，可继续追问');
    } catch (error) {
        notify('会话载入失败：' + error.message, 'error');
    }
}

function newConversation() {
    WS.conversationId = null;
    const stream = document.getElementById('chat-stream');
    if (!stream) return;
    stream.innerHTML = `<div class="max-w-3xl mx-auto msg-enter">
        <div class="bg-white border border-gray-200 rounded-xl p-5 shadow-sm text-center">
            <div class="w-10 h-10 rounded-full bg-[#ebf4ff] text-[#2d8cf0] flex items-center justify-center mx-auto mb-2"><i class="ph ph-chats-circle text-xl"></i></div>
            <p class="text-sm text-gray-600">已开启新对话，请提出你的问题。</p>
            <p class="text-[11px] text-gray-400 mt-1">回答会标注知识库出处，可逐条核查</p>
        </div></div>`;
    notify('已开启新对话');
    localStorage.removeItem('active_conversation');
}

// ==================== 知识库管理 ====================
async function renderKb() {
    const root = document.getElementById('kb-root');
    if (!root) return;
    root.innerHTML = '<div class="text-sm text-gray-400 py-16 text-center"><i class="ph ph-spinner-gap animate-spin mr-2"></i>正在读取知识库…</div>';
    try {
        const [stats, docs] = await Promise.all([apiGet('/kb/stats'), apiGet('/kb/docs')]);
        const info = stats.stats || stats;
        const documents = (docs.documents || docs || []);
        root.innerHTML = `
        <div class="flex flex-wrap items-center justify-between gap-3 mb-5">
            <div>
                <h2 class="text-xl font-bold text-gray-900">学科知识库</h2>
                <p class="text-xs text-gray-400 mt-0.5">结构化元数据 + 章节级分块，所有回答均由此检索并标注出处</p>
            </div>
            <div class="flex items-center gap-2">
                <button class="px-3 py-2 rounded-lg border border-gray-200 text-xs text-gray-600 hover:border-[#2d8cf0] hover:text-[#2d8cf0]" onclick="rebuildKb()"><i class="ph ph-arrows-clockwise mr-1"></i>重建索引</button>
                <button class="px-3 py-2 rounded-lg bg-[#2d8cf0] text-white text-xs hover:bg-[#2080e0]" onclick="document.getElementById('kb-upload-panel').classList.toggle('hidden')"><i class="ph ph-upload-simple mr-1"></i>上传资料</button>
            </div>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            ${[['文档数', info.doc_count], ['知识块', info.chunk_count], ['向量后端', info.backend], ['平均块长', info.avg_chunk_chars || '—']].map(pair => `
                <div class="bg-white border border-gray-200 rounded-xl p-4"><div class="text-2xl font-bold text-[#2d8cf0]">${esc(pair[1] == null ? '—' : pair[1])}</div><div class="text-[11px] text-gray-500 mt-1">${esc(pair[0])}</div></div>`).join('')}
        </div>
        <div class="bg-white border border-gray-200 rounded-xl p-4 mb-4 hidden" id="kb-upload-panel">
            <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div><label class="block text-xs text-gray-600 mb-1">文件</label><input type="file" id="kb-file" class="block w-full text-xs text-gray-500 file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:bg-[#f0f7ff] file:text-[#2d8cf0]"></div>
                <div><label class="block text-xs text-gray-600 mb-1">资料类型</label>
                    <select id="kb-source-type" class="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm">
                        <option value="textbook">教材/讲义</option><option value="outline">课程大纲</option><option value="standard">规范标准</option>
                        <option value="paper">学术论文</option><option value="case">案例/题库</option><option value="other">其他</option></select></div>
                <div><label class="block text-xs text-gray-600 mb-1">权威等级（1-5）</label><input type="number" id="kb-authority" value="3" min="1" max="5" class="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm"></div>
            </div>
            <div class="mt-3 flex justify-end"><button class="px-4 py-2 rounded-lg bg-[#2d8cf0] text-white text-xs hover:bg-[#2080e0]" onclick="uploadKbDoc(this)"><i class="ph ph-check mr-1"></i>上传并索引</button></div>
        </div>
        <div class="bg-white border border-gray-200 rounded-xl overflow-hidden">
            <table class="min-w-full text-xs">
                <thead class="bg-gray-50 text-gray-500"><tr>
                    <th class="px-4 py-2.5 text-left font-medium">文档</th><th class="px-4 py-2.5 text-left font-medium">类型</th>
                    <th class="px-4 py-2.5 text-left font-medium">知识块</th><th class="px-4 py-2.5 text-left font-medium">权威度</th>
                    <th class="px-4 py-2.5 text-left font-medium">状态</th><th class="px-4 py-2.5 text-right font-medium">操作</th></tr></thead>
                <tbody>${documents.map(doc => `<tr class="border-t border-gray-50 hover:bg-gray-50/60">
                    <td class="px-4 py-2.5 text-gray-800">
                        <div class="font-medium">${esc(doc.title || doc.doc_title || doc.name)}</div>
                        <div class="text-[10px] text-gray-400 mt-0.5">${esc(doc.relative_path || doc.filename || '')}</div>
                    </td>
                    <td class="px-4 py-2.5 text-gray-500">${esc(doc.source_type_label || doc.source_type || '')}</td>
                    <td class="px-4 py-2.5 text-gray-500">${esc(doc.chunks != null ? doc.chunks : (doc.chunk_count == null ? '—' : doc.chunk_count))}</td>
                    <td class="px-4 py-2.5 text-amber-400">${'★'.repeat(Math.max(1, Math.min(5, doc.authority || 3)))}</td>
                    <td class="px-4 py-2.5"><span class="px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-600">已索引</span></td>
                    <td class="px-4 py-2.5 text-right"><button class="text-gray-400 hover:text-red-500" onclick="deleteKbDoc('${esc(doc.doc_id || doc.id || '')}',this)" title="移出知识库"><i class="ph ph-trash"></i></button></td>
                </tr>`).join('') || '<tr><td colspan="6" class="px-4 py-8 text-center text-gray-400">暂无文档</td></tr>'}</tbody>
            </table>
        </div>
        <div class="bg-white border border-gray-200 rounded-xl p-4 mt-4">
            <div class="text-xs font-medium text-gray-600 mb-2"><i class="ph ph-magnifying-glass mr-1"></i>检索效果自测（输入问题，查看召回与引用定位）</div>
            <div class="flex gap-2"><input id="kb-search-input" placeholder="例如：快速排序最坏时间复杂度" class="flex-1 px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-[#2d8cf0]">
                <button class="px-4 py-2 rounded-lg bg-[#2d8cf0] text-white text-sm hover:bg-[#2080e0]" onclick="kbSearch(this)">检索</button></div>
            <div id="kb-search-result" class="mt-3 space-y-2"></div>
        </div>`;
    } catch (error) {
        root.innerHTML = '<div class="bg-white border border-red-200 rounded-xl p-5 text-sm text-red-600">' + esc(error.message) + '</div>';
    }
}

async function uploadKbDoc(button) {
    const fileInput = document.getElementById('kb-file');
    if (!fileInput || !fileInput.files || !fileInput.files[0]) return notify('请先选择文件', 'warning');
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    formData.append('source_type', document.getElementById('kb-source-type').value);
    formData.append('authority', document.getElementById('kb-authority').value);
    button.disabled = true;
    try {
        await apiUpload('/kb/upload', formData);
        notify('上传成功，索引已更新');
        renderKb();
    } catch (error) {
        notify('上传失败：' + error.message, 'error');
    } finally { button.disabled = false; }
}

async function deleteKbDoc(docId, button) {
    if (!confirm('确认从知识库移除该文档？')) return;
    try {
        await apiSend('/kb/docs/' + docId, {}, 'DELETE');
        notify('已移除');
        renderKb();
    } catch (error) { notify('移除失败：' + error.message, 'error'); }
}

async function rebuildKb() {
    notify('正在重建索引…', 'warning');
    try {
        const data = await apiSend('/kb/rebuild', {});
        notify('索引已重建：' + (data.chunk_count || '') + ' 个知识块');
        renderKb();
    } catch (error) { notify('重建失败：' + error.message, 'error'); }
}

async function kbSearch(button) {
    const input = document.getElementById('kb-search-input');
    const holder = document.getElementById('kb-search-result');
    if (!input || !holder) return;
    const query = input.value.trim();
    if (!query) return;
    holder.innerHTML = '<div class="text-xs text-gray-400">检索中…</div>';
    try {
        const data = await apiSend('/kb/search', { query: query, top_k: 5 });
        const results = data.results || [];
        holder.innerHTML = results.length ? results.map(citationCard).join('') : '<div class="text-xs text-gray-400">未检索到相关内容</div>';
    } catch (error) { holder.innerHTML = '<div class="text-xs text-red-500">' + esc(error.message) + '</div>'; }
}

// ==================== 使用与评测看板 ====================
async function renderAnalytics() {
    const root = document.getElementById('analytics-root');
    if (!root) return;
    root.innerHTML = '<div class="text-sm text-gray-400 py-16 text-center"><i class="ph ph-spinner-gap animate-spin mr-2"></i>正在汇总使用数据…</div>';
    try {
        const data = await apiGet('/analytics/overview');
        const usage = data.usage || {};
        const users = data.users || {};
        const quality = data.quality || {};
        const agents = data.agents || [];
        const trend = data.trend || [];
        const scale = data.scale || {};
        const evaluation = data.latest_evaluation || null;
        const maxCount = Math.max.apply(null, trend.map(point => point.count).concat([1]));

        root.innerHTML = `
        <div class="flex flex-wrap items-center justify-between gap-3 mb-5">
            <div><h2 class="text-xl font-bold text-gray-900">使用与评测看板</h2>
                <p class="text-xs text-gray-400 mt-0.5">实时反映平台使用规模、内容质量与用户反馈，可作为效果验证依据</p></div>
            <button class="px-3 py-2 rounded-lg border border-gray-200 text-xs text-gray-600 hover:border-[#2d8cf0] hover:text-[#2d8cf0]" onclick="runEvaluation(this)"><i class="ph ph-flask mr-1"></i>运行标准测试集评测</button>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            ${[['累计用户', users.total || 0, 'ph-users'], ['学生会话', usage.conversations || 0, 'ph-chat-circle-dots'],
               ['智能体调用', usage.agent_runs || 0, 'ph-tree-structure'], ['用户满意度', (quality.positive_rate != null ? Math.round(quality.positive_rate * 100) + '%' : '—'), 'ph-smiley']]
              .map(item => `<div class="bg-white border border-gray-200 rounded-xl p-4">
                <div class="flex items-center justify-between"><span class="text-[11px] text-gray-500">${item[0]}</span><i class="ph ${item[2]} text-[#2d8cf0]"></i></div>
                <div class="text-2xl font-bold text-gray-900 mt-2">${esc(item[1])}</div></div>`).join('')}
        </div>
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
            <div class="lg:col-span-2 bg-white border border-gray-200 rounded-xl p-4">
                <div class="text-xs font-medium text-gray-600 mb-3">近 ${trend.length || 7} 天使用趋势</div>
                <div class="flex items-end gap-1.5 h-32">${trend.map(point => `<div class="flex-1 flex flex-col items-center justify-end h-full" title="${esc(point.date)}：${esc(point.count)}">
                    <span class="text-[10px] text-gray-400 mb-0.5">${point.count || ''}</span>
                    <div class="w-full rounded-t bg-gradient-to-t from-[#2d8cf0] to-[#7bbcf9]" style="height:${Math.max(2, Math.round((point.count / maxCount) * 100))}%"></div>
                    <span class="text-[9px] text-gray-400 mt-1">${esc((point.date || '').slice(5))}</span></div>`).join('')}</div>
            </div>
            <div class="bg-white border border-gray-200 rounded-xl p-4">
                <div class="text-xs font-medium text-gray-600 mb-2">规模化使用情况</div>
                <div class="space-y-2 text-xs text-gray-600">
                    <div class="flex justify-between"><span>覆盖学生数</span><span class="font-semibold text-gray-900">${esc(scale.students != null ? scale.students : 0)}</span></div>
                    <div class="flex justify-between"><span>授课教师数</span><span class="font-semibold text-gray-900">${esc(scale.teachers != null ? scale.teachers : 0)}</span></div>
                    <div class="flex justify-between"><span>作业次数</span><span class="font-semibold text-gray-900">${esc(scale.homeworks != null ? scale.homeworks : 0)}</span></div>
                    <div class="flex justify-between"><span>作业提交份数</span><span class="font-semibold text-gray-900">${esc(scale.submissions != null ? scale.submissions : 0)}</span></div>
                    <div class="flex justify-between"><span>提供反馈的用户</span><span class="font-semibold text-gray-900">${esc(scale.trial_users != null ? scale.trial_users : 0)}</span></div>
                </div>
                <p class="text-[10px] ${scale.reaches_50_users ? 'text-emerald-600' : 'text-amber-600'} mt-3 leading-relaxed">
                    <i class="ph ${scale.reaches_50_users ? 'ph-check-circle' : 'ph-info'} mr-1"></i>${esc(scale.note || '')}</p>
            </div>
        </div>
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div class="bg-white border border-gray-200 rounded-xl p-4">
                <div class="text-xs font-medium text-gray-600 mb-2">智能体调用分布</div>
                <div class="space-y-1.5">${agents.length ? agents.slice(0, 8).map(item => `
                    <div class="flex items-center gap-2 text-[11px]"><span class="w-24 truncate text-gray-600">${esc(item.name || item.id)}</span>
                    <div class="flex-1 h-2 rounded bg-gray-100 overflow-hidden"><div class="h-full bg-[#2d8cf0]" style="width:${Math.max(4, Math.round((item.count / Math.max.apply(null, agents.map(a => a.count).concat([1]))) * 100))}%"></div></div>
                    <span class="w-8 text-right text-gray-500">${esc(item.count)}</span></div>`).join('') : '<div class="text-xs text-gray-400">暂无调用记录</div>'}</div>
            </div>
            <div class="bg-white border border-gray-200 rounded-xl p-4">
                <div class="text-xs font-medium text-gray-600 mb-2">最近一次效果评测</div>
                ${evaluation ? `<div class="space-y-2 text-xs text-gray-600">
                    <div class="flex justify-between"><span>测试集</span><span class="text-gray-900">${esc(evaluation.total_cases || 0)} 题</span></div>
                    <div class="flex justify-between"><span>关键词命中（准确率）</span><span class="font-semibold text-[#2d8cf0]">${Math.round((evaluation.accuracy || 0) * 100)}%</span></div>
                    <div class="flex justify-between"><span>引用覆盖率</span><span class="font-semibold text-[#2d8cf0]">${Math.round((evaluation.citation_coverage || 0) * 100)}%</span></div>
                    <div class="flex justify-between"><span>平均响应耗时</span><span>${stamp(evaluation.avg_latency_ms || 0)}</span></div>
                    <div class="text-[10px] text-gray-400">评测时间：${esc(evaluation.created_at || '')}</div>
                </div>` : '<div class="text-xs text-gray-400 leading-relaxed">尚未运行评测。点击右上角「运行标准测试集评测」，系统将用 <strong>data/eval/testset.jsonl</strong> 中的题目自动打分并生成报告。</div>'}
            </div>
        </div>
        <div class="bg-white border border-gray-200 rounded-xl p-4 mt-4">
            <div class="text-xs font-medium text-gray-600 mb-2">用户反馈（最近）</div>
            <div class="space-y-2">${(data.feedback || []).length ? data.feedback.slice(0, 6).map(item => `
                <div class="flex items-start gap-2 text-[11px] text-gray-600 border-b border-gray-50 pb-2">
                    <i class="ph ${item.rating > 0 ? 'ph-thumbs-up text-emerald-500' : 'ph-thumbs-down text-amber-500'} mt-0.5"></i>
                    <span class="min-w-0 flex-1">${esc(item.comment || '（未填写评价）')}</span>
                    <span class="text-gray-400 flex-shrink-0">${esc(item.agent_id || '')} · ${esc((item.created_at || '').slice(5, 16))}</span></div>`).join('') : '<div class="text-xs text-gray-400">暂无用户反馈</div>'}</div>
        </div>`;
    } catch (error) {
        root.innerHTML = '<div class="bg-white border border-red-200 rounded-xl p-5 text-sm text-red-600">' + esc(error.message) + '</div>';
    }
}

async function runEvaluation(button) {
    button.disabled = true;
    const original = button.innerHTML;
    button.innerHTML = '<i class="ph ph-spinner-gap animate-spin mr-1"></i>评测中…';
    try {
        const data = await apiSend('/eval/run', { limit: 12, allow_offline: true }, 'POST');
        const metrics = data.metrics || {};
        notify('评测完成：准确率 ' + Math.round((metrics.accuracy || 0) * 100) + '%，引用覆盖率 ' + Math.round((metrics.citation_coverage || 0) * 100) + '%');
        renderAnalytics();
    } catch (error) {
        notify('评测失败：' + error.message, 'error');
    } finally { button.disabled = false; button.innerHTML = original; }
}

// ==================== 学习画像 ====================
async function renderProfile() {
    const root = document.getElementById('profile-root');
    if (!root) return;
    root.innerHTML = '<div class="text-sm text-gray-400 py-16 text-center"><i class="ph ph-spinner-gap animate-spin mr-2"></i>正在读取画像…</div>';
    try {
        const profile = await apiGet('/profile');
        const weak = profile.weak_points || [];
        root.innerHTML = `
        <div class="mb-5"><h2 class="text-xl font-bold text-gray-900">我的学习画像</h2>
            <p class="text-xs text-gray-400 mt-0.5">画像驱动讲解深度、表达风格与练习推荐，实现个性化自适应</p></div>
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div class="bg-white border border-gray-200 rounded-xl p-5 space-y-4">
                <div><label class="block text-xs font-medium text-gray-600 mb-1.5">学习阶段</label>
                    <select id="pf-level" class="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm">
                        ${[['beginner', '入门（初学，需要铺垫）'], ['intermediate', '进阶（已学基础，重在辨析）'], ['advanced', '提高（考研/竞赛，需要深度）']].map(item => `<option value="${item[0]}" ${profile.level === item[0] ? 'selected' : ''}>${item[1]}</option>`).join('')}
                    </select></div>
                <div><label class="block text-xs font-medium text-gray-600 mb-1.5">讲解深度</label>
                    <select id="pf-detail" class="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm">
                        ${[['brief', '精简（先给结论）'], ['standard', '标准（结论 + 讲解）'], ['deep', '深入（含推导与扩展）']].map(item => `<option value="${item[0]}" ${profile.detail_level === item[0] ? 'selected' : ''}>${item[1]}</option>`).join('')}
                    </select></div>
                <div><label class="block text-xs font-medium text-gray-600 mb-1.5">表达风格</label>
                    <select id="pf-style" class="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm">
                        ${[['balanced', '均衡（讲解 + 例子）'], ['concise', '简洁（要点为主）'], ['socratic', '启发（多提问引导）'], ['example', '案例驱动（多举例）']].map(item => `<option value="${item[0]}" ${profile.learning_style === item[0] ? 'selected' : ''}>${item[1]}</option>`).join('')}
                    </select></div>
                <div><label class="block text-xs font-medium text-gray-600 mb-1.5">近期学习目标</label>
                    <input id="pf-goal" value="${esc(profile.goal || '')}" placeholder="例如：三周内掌握图的最短路径" class="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm"></div>
                <button class="w-full py-2.5 rounded-lg bg-[#2d8cf0] text-white text-sm font-medium hover:bg-[#2080e0]" onclick="saveProfile(this)"><i class="ph ph-check mr-1"></i>保存画像</button>
            </div>
            <div class="bg-white border border-gray-200 rounded-xl p-5">
                <div class="text-xs font-medium text-gray-600 mb-2">系统记录的薄弱点</div>
                <div class="flex flex-wrap gap-1.5 mb-3">${weak.length ? weak.map(item => `<span class="px-2 py-1 rounded-full bg-amber-50 text-amber-600 text-[11px]">${esc(item)}</span>`).join('') : '<span class="text-xs text-gray-400">暂无记录，系统会从错题与提问中自动归纳</span>'}</div>
                <div class="text-xs font-medium text-gray-600 mb-2 mt-4">互动统计</div>
                <div class="space-y-1.5 text-xs text-gray-600">
                    <div class="flex justify-between"><span>累计互动</span><span class="font-semibold text-gray-900">${esc(profile.interactions || 0)} 次</span></div>
                    <div class="flex justify-between"><span>画像状态</span><span class="font-semibold text-emerald-600">${profile.guest ? '游客（登录后持久化）' : '已生效'}</span></div>
                </div>
                <div class="mt-4 p-3 rounded-lg bg-[#f0f7ff] text-[11px] text-[#2d8cf0] leading-relaxed">
                    <i class="ph ph-lightbulb mr-1"></i>画像会实时作用于答疑、路径规划与练习推荐：同一问题对不同阶段学生给出不同深度的讲解。
                </div>
            </div>
        </div>`;
    } catch (error) {
        root.innerHTML = '<div class="bg-white border border-red-200 rounded-xl p-5 text-sm text-red-600">' + esc(error.message) + '</div>';
    }
}

async function saveProfile(button) {
    button.disabled = true;
    try {
        await apiSend('/profile', {
            level: document.getElementById('pf-level').value,
            detail_level: document.getElementById('pf-detail').value,
            learning_style: document.getElementById('pf-style').value,
            goal: document.getElementById('pf-goal').value.trim(),
        }, 'PATCH');
        notify('画像已保存，后续回答将按此适配');
    } catch (error) {
        notify('保存失败：' + error.message, 'error');
    } finally { button.disabled = false; }
}

// ==================== 启动挂载 ====================
function pickChatMode(mode) {
    AppState.currentMode = mode;
    document.querySelectorAll('#chat-mode-bar button').forEach(button => {
        button.className = button.id === 'mode-' + mode
            ? 'px-2.5 py-1 rounded-md bg-white text-[#2d8cf0] shadow-sm font-medium border border-gray-100 transition-all'
            : 'px-2.5 py-1 rounded-md text-gray-500 hover:text-[#2d8cf0] font-medium transition-all';
    });
    if (typeof setChatMode === 'function') setChatMode(mode);
}

// 覆盖 main.js 的发送逻辑：改用支持会话续接与引用展示的实现
function handleSend() { return sendChatMessage(); }

document.addEventListener('DOMContentLoaded', function () {
    AppState.currentMode = 'qa';
    pickChatMode('qa');
    const sendBtn = document.getElementById('send-btn');
    if (sendBtn) sendBtn.addEventListener('click', event => { event.preventDefault(); sendChatMessage(); });
    const newChatBtn = document.getElementById('new-chat-btn');
    if (newChatBtn) newChatBtn.addEventListener('click', newConversation);
    // 等 main.js 的 init 完成后再刷新会话列表，避免被历史记录旧逻辑覆盖
    setTimeout(refreshConversationList, 900);
});