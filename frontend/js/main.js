let speechRecognition = null;
let isSpeechRecognizing = false;
let speechBaseText = '';
let speechFinalText = '';
/**
 * 灵眸智教 - 前端核心脚本 (含师生双端逻辑分离)
 */

const API_CONFIG = {
    // 同源部署（由 Flask 托管前端）时自动使用当前地址；直接用 file:// 打开时回退到本地 5000 端口
    BASE_URL: (location.protocol === 'file:' ? 'http://localhost:5000' : (location.origin || 'http://localhost:5000')) + '/api/v1',
    TIMEOUT: 360000,
    ENDPOINTS: {
        CHAT: '/chat', CHAT_STOP: '/chat/stop', CODE_EVALUATE: '/code/evaluate', DIAGNOSIS: '/diagnosis',
        VIDEOS: '/videos', MATERIALS: '/materials', HOMEWORKS: '/homeworks', HEALTH: '/health',
        VIDEO_UPLOAD: '/videos/upload', MATERIAL_UPLOAD: '/materials/upload',
        AUTH_LOGIN: '/auth/login', AUTH_REGISTER: '/auth/register', AUTH_LOGOUT: '/auth/logout', AUTH_USER: '/auth/user',
    },
};

class ApiClient {
    constructor(config) {
        this.baseURL = config.BASE_URL; this.timeout = config.TIMEOUT;
        this.sessionId = this._generateSessionId(); this.abortController = null; this.isAborted = false;
    }
    _generateSessionId() { return 'sess_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9); }
    _url(endpoint) { return this.baseURL + endpoint; }
    _headers() {
        const h = { 'Content-Type': 'application/json', 'X-Session-Id': this.sessionId };
        const token = localStorage.getItem('api_token');
        if (token) h.Authorization = `Bearer ${token}`;
        return h; // Session authentication handled by cookies/Flask
    }
    _uploadHeaders() {
        const h = { 'X-Session-Id': this.sessionId };
        const token = localStorage.getItem('api_token');
        if (token) h.Authorization = `Bearer ${token}`;
        return h;
    }
    _createAbortController() { this.abortController = new AbortController(); this.isAborted = false; return this.abortController; }
    abort() { if (this.abortController) { this.abortController.abort(); this.isAborted = true; this.abortController = null; } }
    stopGeneration() {
        return fetch(this._url(API_CONFIG.ENDPOINTS.CHAT_STOP), {
            method: 'POST', headers: this._headers(), credentials: 'include', keepalive: true
        }).catch(() => {});
    }

    async post(endpoint, body) {
        const controller = this._createAbortController();
        const timeoutId = setTimeout(() => { controller.abort(); this.isAborted = true; }, this.timeout);
        try {
            const resp = await fetch(this._url(endpoint), { method: 'POST', headers: this._headers(), body: JSON.stringify(body), signal: controller.signal, credentials: 'include' });
            clearTimeout(timeoutId);
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).message || `请求失败`);
            return await resp.json();
        } catch (err) {
            clearTimeout(timeoutId);
            if (err.name === 'AbortError') throw new Error('请求已取消');
            throw err;
        }
    }
    
    async get(endpoint) {
        const controller = this._createAbortController();
        const timeoutId = setTimeout(() => { controller.abort(); this.isAborted = true; }, this.timeout);
        try {
            const resp = await fetch(this._url(endpoint), { method: 'GET', headers: this._headers(), signal: controller.signal, credentials: 'include' });
            clearTimeout(timeoutId);
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).message || `请求失败`);
            return await resp.json();
        } catch (err) {
            clearTimeout(timeoutId);
            if (err.name === 'AbortError') throw new Error('请求已取消');
            throw err;
        }
    }

    async patch(endpoint, body) {
        const resp = await fetch(this._url(endpoint), { method: 'PATCH', headers: this._headers(), body: JSON.stringify(body), credentials: 'include' });
        const result = await resp.json();
        if (!resp.ok) throw new Error(result.message || '请求失败');
        return result;
    }

    async healthCheck() {
        try { const resp = await fetch(this._url(API_CONFIG.ENDPOINTS.HEALTH), { method: 'GET', signal: AbortSignal.timeout(5000) }); return resp.ok; } catch { return false; }
    }
}
const apiClient = new ApiClient(API_CONFIG);

const MockAI = {
    getVideos: () => [
        { id: 1, title: '第1章：绪论与算法复杂度', duration: '38:20', progress: 100, thumbnail: '📘' },
        { id: 2, title: '第2章：线性表', duration: '42:15', progress: 90, thumbnail: '📗' }
    ],
    getMaterials: () => [
        { id: 1, name: '第1章 绪论', filename: 'demo1.pdf', ext: 'pdf', type: 'pdf', size: '2.4MB', icon: 'ph-file-pdf', color: 'text-red-500' },
        { id: 2, name: '第3章 栈与队列', filename: 'demo2.pdf', ext: 'pdf', type: 'pdf', size: '3.1MB', icon: 'ph-file-pdf', color: 'text-red-500' }
    ],
    getHomeworks: () => [
        { id: 1, title: '实验1：线性表实现', deadline: '2026-07-10 23:59', status: 'graded', score: 92 },
        { id: 2, title: '实验2：二叉树遍历', deadline: '2026-07-18 23:59', status: 'submitted', score: null }
    ]
};

const MAX_GUEST_QUERIES = 5;
const AppState = { currentMode: 'code', isGenerating: false, isLoggedIn: false, isGuest: true, guestQueryCount: Number(localStorage.getItem('guest_query_count') || 0), currentUser: null };
let currentAttachment = null;

const DOM = {
    sidebar: document.getElementById('sidebar'), sidebarToggleBtn: document.getElementById('sidebar-toggle-btn'),
    chatStream: document.getElementById('chat-stream'), userInput: document.getElementById('user-input'),
    sendBtn: document.getElementById('send-btn'), stopBtn: document.getElementById('stop-btn'),
    charCount: document.getElementById('char-count'),
    modeBtns: { code: document.getElementById('mode-code'), qa: document.getElementById('mode-qa'), diagnosis: document.getElementById('mode-diagnosis') },
    sidebarItems: document.querySelectorAll('.sidebar-item'), pages: document.querySelectorAll('.page-content'),
    toastContainer: document.getElementById('toast-container'), settingsModal: document.getElementById('settings-modal'),
    videoGrid: document.getElementById('video-grid'), materialList: document.getElementById('material-list'), homeworkList: document.getElementById('homework-list'),
    authModal: document.getElementById('auth-modal'), authLoginForm: document.getElementById('auth-login-form'), authRegisterForm: document.getElementById('auth-register-form'),
    userName: document.getElementById('user-name'), userAvatar: document.getElementById('user-avatar'),
    voiceInputBtn: document.getElementById('voice-input-btn'), attachmentBtn: document.getElementById('attachment-btn'), attachmentInput: document.getElementById('attachment-input'), attachmentPreview: document.getElementById('attachment-preview'),
    logoutIcon: document.getElementById('logout-icon'), guestLoginBar: document.getElementById('guest-login-bar'),
};

// ==================== 身份权限切换 ====================
let currentAuthRole = 'student';

function switchAuthRole(role) {
    currentAuthRole = role;
    const tabStudent = document.getElementById('tab-student');
    const tabTeacher = document.getElementById('tab-teacher');
    const studentNumberField = document.getElementById('reg-student-number-field');
    const employeeNumberField = document.getElementById('reg-employee-number-field');
    const loginIdentifierLabel = document.getElementById('login-identifier-label');
    const isStudent = role === 'student';

    tabStudent.classList.toggle('active', isStudent);
    tabTeacher.classList.toggle('active', !isStudent);
    tabStudent.setAttribute('aria-selected', String(isStudent));
    tabTeacher.setAttribute('aria-selected', String(!isStudent));
    document.getElementById('login-title').textContent = isStudent ? '学生登录' : '教师登录';
    document.getElementById('reg-title').textContent = isStudent ? '注册学生账号' : '注册教师账号';

    document.getElementById('reg-username').placeholder = isStudent ? '请输入学生姓名' : '请输入教师姓名';
    if (loginIdentifierLabel) loginIdentifierLabel.textContent = isStudent ? '学号' : '教职工号';
    document.getElementById('login-username').placeholder = isStudent ? '请输入学号' : '请输入教职工号';
    studentNumberField?.classList.toggle('hidden', !isStudent);
    employeeNumberField?.classList.toggle('hidden', isStudent);
    document.getElementById('reg-role').value = role;
}

function openAuthModal() {
    DOM.authModal.classList.add('show');
    switchAuthForm('login');
    setTimeout(() => document.getElementById('login-username')?.focus(), 120);
}
function closeAuthModal() { DOM.authModal.classList.remove('show'); }
function switchAuthForm(form) {
    if (form === 'login') { DOM.authLoginForm.classList.remove('hidden'); DOM.authRegisterForm.classList.add('hidden'); }
    else { DOM.authLoginForm.classList.add('hidden'); DOM.authRegisterForm.classList.remove('hidden'); }
}
function togglePassword(inputId, btn) {
    const input = document.getElementById(inputId);
    if (!input) return;
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    btn.innerHTML = `<i class="ph ${show ? 'ph-eye-slash' : 'ph-eye'}"></i>`;
    btn.setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
}

async function handleLogin() {
    const username = document.getElementById('login-username').value.trim();
    const password = document.getElementById('login-password').value.trim();
    if (!username || !password) return showToast('请填写完整的登录信息', 'warning');

    try {
        const response = await apiClient.post(API_CONFIG.ENDPOINTS.AUTH_LOGIN, { username, password, role: currentAuthRole });
        if (response.code === 200) handleLoginSuccess(response.data);
        else showToast(response.message || '登录失败', 'error');
    } catch (err) { showToast(`登录失败：${err.message}`, 'error'); }
}

async function handleRegister() {
    const username = document.getElementById('reg-username').value.trim();
    const studentNumber = document.getElementById('reg-student-number').value.trim();
    const employeeNumber = document.getElementById('reg-employee-number').value.trim();
    const email = document.getElementById('reg-email').value.trim();
    const password = document.getElementById('reg-password').value.trim();
    const role = document.getElementById('reg-role').value;

    if (!username || !email || !password || (currentAuthRole === 'student' && !studentNumber) || (currentAuthRole === 'teacher' && !employeeNumber)) return showToast('请填写完整的注册信息', 'warning');
    if (password.length < 6) return showToast('密码长度至少为6位', 'warning');

    try {
        const response = await apiClient.post(API_CONFIG.ENDPOINTS.AUTH_REGISTER, { username, student_number: studentNumber, employee_number: employeeNumber, email, password, role });
        if (response.code === 200) { handleLoginSuccess(response.data); showToast('注册成功', 'success'); }
        else showToast(response.message || '注册失败', 'error');
    } catch (err) { showToast(`注册失败：${err.message}`, 'error'); }
}

function handleLoginSuccess(userData) {
    if (userData.api_token) localStorage.setItem('api_token', userData.api_token);
    AppState.isLoggedIn = true; AppState.isGuest = false;
    AppState.currentUser = { id: userData.id, name: userData.username, email: userData.email || '', role: userData.role };
    updateUserUI(); loadHistoryList(); closeAuthModal(); showToast(`欢迎回来，${userData.username}！`, 'success');
}

async function logout() {
    try { await apiClient.post(API_CONFIG.ENDPOINTS.AUTH_LOGOUT, {}); } catch(e){}
    localStorage.removeItem('api_token');
    AppState.isLoggedIn = false; AppState.isGuest = true; AppState.currentUser = null;
    updateUserUI(); loadHistoryList(); showToast('已退出登录', 'info');
}

function updateUserUI() {
    if (AppState.isLoggedIn && AppState.currentUser) {
        DOM.userName.textContent = AppState.currentUser.name;
        DOM.userAvatar.src = `https://api.dicebear.com/7.x/notionists/svg?seed=${encodeURIComponent(AppState.currentUser.name)}&backgroundColor=e0e7ff`;
        DOM.guestLoginBar.classList.add('hidden');
        if (DOM.logoutIcon) DOM.logoutIcon.style.display = 'block';
    } else {
        DOM.userName.textContent = '游客模式';
        DOM.userAvatar.src = 'https://api.dicebear.com/7.x/notionists/svg?seed=Guest&backgroundColor=f0f0f0';
        DOM.guestLoginBar.classList.remove('hidden');
        if (DOM.logoutIcon) DOM.logoutIcon.style.display = 'none';
    }

    const isTeacher = AppState.isLoggedIn && (AppState.currentUser.role === 'teacher' || AppState.currentUser.role === 'admin');
    
    document.querySelectorAll('.teacher-only').forEach(el => {
        if (isTeacher) el.classList.remove('hidden'); else el.classList.add('hidden');
    });

    const hwTitle = document.getElementById('homework-title-text');
    if (hwTitle) hwTitle.textContent = isTeacher ? '学生作业批阅' : '我的在线作业';
    if(document.querySelector('[data-page="homework"]')?.classList.contains('active')) loadHomeworks();
}

async function restoreSession() {
    if (!localStorage.getItem('api_token')) return;
    try {
        const response = await apiClient.get(API_CONFIG.ENDPOINTS.AUTH_USER);
        if (response.code === 200 && response.data) handleLoginSuccess(response.data);
    } catch {
        localStorage.removeItem('api_token');
    }
}

async function loadHistoryList() {
    const list = document.getElementById('history-list');
    if (!list) return;
    try {
        const response = await apiClient.get('/history');
        const records = response.data || [];
        list.innerHTML = records.length ? records.map(item => `<div class="text-xs text-gray-500 py-1.5 px-1 cursor-pointer hover:text-[#2d8cf0] hover:bg-[#f8faff] rounded transition-colors flex items-center gap-1" onclick="loadHistory(${item.id})" title="${escapeAttribute(item.title)}"><i class="ph ph-clock-counter-clockwise text-[10px] flex-shrink-0"></i><span class="truncate flex-1">${escapeAttribute(item.title)}</span><button type="button" class="text-gray-300 hover:text-red-500 flex-shrink-0" title="删除历史记录" onclick="event.stopPropagation(); deleteHistory(${item.id})"><i class="ph ph-trash"></i></button></div>`).join('') : '<div class="text-xs text-gray-400 py-1.5 px-1">暂无历史记录</div>';
    } catch {
        list.innerHTML = '<div class="text-xs text-gray-400 py-1.5 px-1">历史记录加载失败</div>';
    }
}

async function deleteHistory(historyId) {
    if (AppState.isGuest) return requireLogin();
    return openConfirmModal('确定删除这条历史记录吗？', () => deleteHistoryRecord(historyId));
}

async function deleteHistoryRecord(historyId) {
    try {
        const response = await fetch(apiClient._url(`/history/${historyId}`), { method: 'DELETE', headers: apiClient._headers(), credentials: 'include' });
        const result = await response.json();
        if (!response.ok || result.code !== 200) throw new Error(result.message || '删除失败');
        showToast('历史记录已删除', 'success');
        await loadHistoryList();
    } catch (error) {
        showToast(`删除失败：${error.message}`, 'error');
    }
}

let pendingConfirmAction = null;

function openConfirmModal(message, action) {
    pendingConfirmAction = action;
    document.getElementById('confirm-message').textContent = message;
    document.getElementById('confirm-modal').classList.add('show');
}

function closeConfirmModal() {
    pendingConfirmAction = null;
    document.getElementById('confirm-modal').classList.remove('show');
}

function confirmPendingAction() {
    const action = pendingConfirmAction;
    closeConfirmModal();
    if (action) action();
}

async function loadHistory(historyId) {
    if (AppState.isGuest) return requireLogin();
    try {
        const response = await apiClient.get(`/history/${historyId}`);
        const item = response.data;
        DOM.chatStream.innerHTML = `<div class="flex items-start max-w-3xl mx-auto flex-row-reverse msg-enter"><div class="bg-[#2d8cf0] text-white rounded-xl rounded-tr-none px-4 py-3 text-sm max-w-[80%] whitespace-pre-wrap shadow-sm">${escapeAttribute(item.prompt)}</div></div><div class="flex items-start max-w-3xl mx-auto msg-enter"><div class="bg-white border rounded-xl rounded-tl-none px-5 py-4 text-sm w-full shadow-sm"><div class="prose max-w-none prose-content">${marked.parse(item.response)}</div></div></div>`;
        setChatMode(item.mode);
        switchPage('ai-chat');
        scrollToBottom();
    } catch (error) { showToast(`历史记录加载失败：${error.message}`, 'error'); }
}

function showToast(msg, type = 'info') {
    const container = DOM.toastContainer;
    const toast = document.createElement('div');
    const colors = { info: 'border-l-[#2d8cf0]', success: 'border-l-[#10b981]', error: 'border-l-[#e74c3c]', warning: 'border-l-[#f59e0b]' };
    const icons = { info: 'ph-info text-[#2d8cf0]', success: 'ph-check-circle text-[#10b981]', error: 'ph-x-circle text-[#e74c3c]', warning: 'ph-warning text-[#f59e0b]' };
    toast.className = `bg-white px-4 py-3 rounded-lg shadow-lg border-l-4 ${colors[type]} text-sm font-medium transform transition-all duration-300 translate-y-[-20px] opacity-0 pointer-events-auto flex items-center gap-2 max-w-sm`;
    toast.innerHTML = `<i class="ph ${icons[type]} flex-shrink-0"></i><span>${msg}</span>`;
    container.appendChild(toast);
    requestAnimationFrame(() => { toast.classList.remove('translate-y-[-20px]', 'opacity-0'); toast.classList.add('translate-y-0', 'opacity-100'); });
    setTimeout(() => { toast.classList.add('opacity-0', 'translate-y-[-20px]'); setTimeout(() => toast.remove(), 300); }, 3000);
}

function openSettings() {
    if (AppState.isGuest) return requireLogin();
    document.getElementById('settings-modal')?.classList.add('show');
    document.getElementById('settings-username').value = AppState.currentUser.name;
    document.getElementById('settings-email').value = AppState.currentUser.email || '';
    // 模型信息一律从后端实时读取，不在 HTML 里写死，避免界面与实际模型不一致
    if (typeof refreshModelPanel === 'function') refreshModelPanel();
}

function closeSettings() { document.getElementById('settings-modal')?.classList.remove('show'); }

async function testAPIConnection() {
    const healthy = await apiClient.healthCheck();
    showToast(healthy ? 'API 连接正常' : 'API 连接失败', healthy ? 'success' : 'error');
}

async function updateProfile() {
    const payload = { username: document.getElementById('settings-username').value.trim(), email: document.getElementById('settings-email').value.trim(), current_password: document.getElementById('settings-current-password').value, new_password: document.getElementById('settings-new-password').value };
    try {
        const response = await apiClient.patch('/auth/profile', payload);
        if (response.code !== 200) throw new Error(response.message);
        handleLoginSuccess(response.data);
        document.getElementById('settings-current-password').value = '';
        document.getElementById('settings-new-password').value = '';
        closeSettings();
        showToast(response.message, 'success');
    } catch (error) { showToast(`修改失败：${error.message}`, 'error'); }
}

// ==================== 资源渲染 ====================
function renderHomeworks(homeworks) {
    if (!DOM.homeworkList) return;
    if (!homeworks?.length) { DOM.homeworkList.innerHTML = '<div class="text-center py-12 text-gray-400">暂无数据</div>'; return; }
    
    const isTeacher = AppState.isLoggedIn && (AppState.currentUser.role === 'teacher' || AppState.currentUser.role === 'admin');

    DOM.homeworkList.innerHTML = homeworks.map(hw => {
        if (isTeacher) {
            return `
            <div class="bg-white border rounded-xl p-5 flex justify-between items-center shadow-sm border-l-4 border-l-[#2d8cf0]">
                <div>
                    <div class="font-bold text-gray-800">${hw.title}</div>
                    ${hw.description ? `<div class="text-sm text-gray-500 mt-1">${escapeAttribute(hw.description)}</div>` : ''}
                    <div class="text-xs text-gray-500 mt-1 flex items-center gap-2">
                        <span>截止时间：${hw.deadline}</span>
                        <span class="bg-blue-50 text-blue-500 px-2 py-0.5 rounded-full">已完成: ${hw.completed_count || 0}/${hw.total_students || 0}人</span>
                    </div>
                </div>
                <div class="flex items-center gap-3 ml-4 shrink-0"><button class="bg-[#2d8cf0] hover:bg-[#2080e0] text-white px-4 py-2 rounded-lg text-sm font-medium transition-all shadow-sm flex items-center gap-1" onclick="openGradeModal(${hw.id}, '${escapeAttribute(hw.title)}')"><i class="ph ph-pencil-simple"></i> 去批改</button><button class="border border-red-200 text-red-500 hover:bg-red-50 px-3 py-2 rounded-lg text-sm" title="删除作业" onclick="deleteHomework(${hw.id})"><i class="ph ph-trash"></i></button></div>
            </div>`;
        } else {
            const statusMap = {
                graded: { text: '✅ 已批改', btnText: '查看批改', btnClass: 'border border-[#2d8cf0] text-[#2d8cf0]' },
                submitted: { text: '📤 已提交', btnText: '查看详情', btnClass: 'border border-gray-300 text-gray-600' },
                pending: { text: '⏳ 待提交', btnText: '去提交', btnClass: 'bg-[#2d8cf0] text-white' },
            };
            const s = statusMap[hw.status] || statusMap.pending;
            const scoreHTML = hw.score != null ? `<span class="font-bold text-[#2d8cf0] ml-2">${hw.score}分</span>` : '';
            return `
            <div class="bg-white border rounded-xl p-5 flex justify-between items-center shadow-sm">
                <div><div class="font-medium">${hw.title}${scoreHTML}</div>${hw.description ? `<div class="text-sm text-gray-500 mt-1">${escapeAttribute(hw.description)}</div>` : ''}<div class="text-xs text-gray-500 mt-1">⏰ 截止：${hw.deadline} · ${s.text}</div></div>
                <button class="${s.btnClass} px-4 py-1.5 rounded-lg text-sm ml-4 shrink-0" onclick="${hw.status === 'pending' ? `openSubmissionModal(${hw.id}, '${escapeAttribute(hw.title)}')` : `showHomeworkResult(${hw.score ?? 'null'}, '${escapeAttribute(hw.comment || '')}')`}">${s.btnText}</button>
            </div>`;
        }
    }).join('');
}

async function loadHomeworks() {
    if (!DOM.homeworkList) return;
    DOM.homeworkList.innerHTML = '<div class="text-center py-12 text-gray-400">加载中...</div>';
    try {
        const res = await apiClient.post(API_CONFIG.ENDPOINTS.HOMEWORKS, {});
        if (res?.data) renderHomeworks(res.data); else throw new Error();
    } catch { renderHomeworks(MockAI.getHomeworks()); }
}

function openSubmissionModal(homeworkId, title) {
    document.getElementById('submission-homework-id').value = homeworkId;
    document.getElementById('submission-homework-title').textContent = title;
    document.getElementById('submission-content').value = '';
    document.getElementById('submission-modal').classList.add('show');
}

function closeSubmissionModal() { document.getElementById('submission-modal').classList.remove('show'); }

async function submitHomeworkAssignment(homeworkId, content) {
    try {
        const response = await fetch(apiClient._url(`/homeworks/${homeworkId}/submit`), { method: 'POST', headers: apiClient._headers(), body: JSON.stringify({ content }), credentials: 'include' });
        const result = await response.json();
        showToast(result.message || '操作完成', result.code === 200 ? 'success' : 'error');
        if (result.code === 200) { closeSubmissionModal(); loadHomeworks(); }
    } catch (error) { showToast(`提交失败：${error.message}`, 'error'); }
}

function submitStudentHomework() {
    const content = document.getElementById('submission-content').value.trim();
    if (!content) return showToast('请填写作业内容', 'warning');
    submitHomeworkAssignment(Number(document.getElementById('submission-homework-id').value), content);
}

function showHomeworkResult(score, comment) {
    showToast(score === null ? '作业已提交，老师尚未批改' : `得分：${score}分${comment ? `，评语：${comment}` : ''}`, 'info');
}

async function openGradeModal(homeworkId, title) {
    document.getElementById('grade-homework-title').textContent = title;
    document.getElementById('grade-submissions').innerHTML = '<div class="text-center py-8 text-gray-400">加载中...</div>';
    document.getElementById('grade-modal').classList.add('show');
    try {
        const response = await apiClient.get(`/homeworks/${homeworkId}/submissions`);
        const submissions = response.data || [];
        document.getElementById('grade-submissions').innerHTML = submissions.length ? submissions.map(item => `<div class="border border-gray-200 rounded-lg p-4 space-y-3"><div class="flex justify-between"><strong>${escapeAttribute(item.student_name)}</strong><span class="text-xs text-gray-400">${item.score == null ? '待批改' : `${item.score} 分`}</span></div><div class="bg-gray-50 rounded p-3 text-sm whitespace-pre-wrap">${escapeAttribute(item.content)}</div><div class="grid grid-cols-[100px_1fr] gap-3 items-center"><input id="score-${item.id}" class="auth-input" type="number" min="0" max="100" value="${item.score ?? ''}" placeholder="分数"><input id="comment-${item.id}" class="auth-input" value="${escapeAttribute(item.comment)}" placeholder="评语"></div><button class="bg-[#2d8cf0] text-white rounded-lg px-3 py-1.5 text-sm" onclick="saveGrade(${homeworkId}, ${item.id})">保存批改</button></div>`).join('') : '<div class="text-center py-8 text-gray-400">暂无学生提交</div>';
    } catch (error) { document.getElementById('grade-submissions').innerHTML = `<div class="text-center py-8 text-red-500">加载失败：${escapeAttribute(error.message)}</div>`; }
}

function closeGradeModal() { document.getElementById('grade-modal').classList.remove('show'); }

async function saveGrade(homeworkId, submissionId) {
    const score = Number(document.getElementById(`score-${submissionId}`).value);
    const comment = document.getElementById(`comment-${submissionId}`).value.trim();
    if (!Number.isInteger(score) || score < 0 || score > 100) return showToast('请输入 0 到 100 的整数分数', 'warning');
    try {
        const response = await fetch(apiClient._url(`/homeworks/${homeworkId}/submissions/${submissionId}`), { method: 'PATCH', headers: apiClient._headers(), body: JSON.stringify({ score, comment }), credentials: 'include' });
        const result = await response.json();
        showToast(result.message || '操作完成', result.code === 200 ? 'success' : 'error');
        if (result.code === 200) { closeGradeModal(); loadHomeworks(); }
    } catch (error) { showToast(`保存失败：${error.message}`, 'error'); }
}

async function deleteHomework(homeworkId) {
    return openConfirmModal('确定删除这份作业吗？相关提交记录也会删除。', () => deleteHomeworkRecord(homeworkId));
}

async function deleteHomeworkRecord(homeworkId) {
    try {
        const response = await fetch(apiClient._url(`/homeworks/${homeworkId}`), { method: 'DELETE', headers: apiClient._headers(), credentials: 'include' });
        const result = await response.json();
        showToast(result.message || '操作完成', result.code === 200 ? 'success' : 'error');
        if (result.code === 200) loadHomeworks();
    } catch (error) { showToast(`删除失败：${error.message}`, 'error'); }
}

function openHomeworkForm() {
    document.getElementById('homework-modal')?.classList.add('show');
}

function closeHomeworkForm() {
    document.getElementById('homework-modal')?.classList.remove('show');
}

async function submitHomework() {
    const title = document.getElementById('homework-form-title').value.trim();
    const deadline = document.getElementById('homework-form-deadline').value;
    const description = document.getElementById('homework-form-description').value.trim();
    if (!title || !deadline) return showToast('请填写作业标题和截止时间', 'warning');
    try {
        const response = await apiClient.post('/homeworks/create', { title, deadline, description });
        showToast(response.message || '作业发布成功', 'success');
        document.getElementById('homework-form').reset();
        closeHomeworkForm();
        loadHomeworks();
    } catch (error) {
        showToast(`发布失败：${error.message}`, 'error');
    }
}

let currentVideoFolder = localStorage.getItem('video_folder') || '';

function renderVideos(data) {
    const videos = Array.isArray(data) ? data : (data.videos || []);
    const folders = Array.isArray(data) ? [] : (data.folders || []);
    if (!DOM.videoGrid) return;
    const countEl = document.getElementById('video-count');
    if (countEl) countEl.textContent = videos.length;
    const arg = value => encodeURIComponent(value);
    const parent = currentVideoFolder.includes('/') ? currentVideoFolder.split('/').slice(0, -1).join('/') : '';
    const back = currentVideoFolder ? `<div class="col-span-full flex items-center gap-3"><button class="text-sm text-[#2d8cf6] hover:underline" onclick="openVideoFolder(decodeURIComponent('${arg(parent)}'))"><i class="ph ph-arrow-left"></i> 返回上级</button><span class="text-xs text-gray-400">/${escapeAttribute(currentVideoFolder)}</span></div>` : '';
    const folderItems = folders.map(folder => `<div class="bg-white border border-amber-200 rounded-xl p-4 shadow-sm flex items-center justify-between"><button class="flex items-center gap-3 min-w-0" onclick="openVideoFolder(decodeURIComponent('${arg(folder.path)}'))"><i class="ph ph-folder text-3xl text-amber-500"></i><span class="font-medium truncate">${escapeAttribute(folder.name)}</span></button><div class="flex gap-2"><button class="text-gray-400 hover:text-blue-500" title="重命名文件夹" onclick="renameVideoFolder(decodeURIComponent('${arg(folder.path)}'))"><i class="ph ph-pencil-simple"></i></button><button class="text-gray-400 hover:text-red-500" title="删除空文件夹" onclick="deleteVideoFolder(decodeURIComponent('${arg(folder.path)}'))"><i class="ph ph-trash"></i></button></div></div>`).join('');
    const videoItems = videos.map(v => `
    <div class="group bg-white border border-gray-200 hover:border-[#2d8cf0] rounded-xl overflow-hidden shadow-sm hover:shadow-lg transition-all duration-300 cursor-pointer" onclick="playVideo('${v.filename || ''}', '${v.title.replace(/'/g, "\\'")}')">
    <div class="relative h-36 bg-gradient-to-br from-gray-100 to-gray-200 flex items-center justify-center text-5xl overflow-hidden">
    <span>${v.thumbnail || '🎬'}</span>
    <div class="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-all duration-300 flex items-center justify-center">
    <div class="w-12 h-12 rounded-full bg-white/90 shadow-lg flex items-center justify-center text-[#2d8cf0] opacity-0 group-hover:opacity-100 transition-all duration-300 translate-y-2 group-hover:translate-y-0">
    <i class="ph ph-play-fill text-2xl ml-0.5"></i>
    </div>
    </div>
    </div>
    <div class="p-4">
    <div class="font-medium text-sm text-gray-800 group-hover:text-[#2d8cf0] transition-colors truncate">${v.title}</div>
    <div class="flex items-center gap-3 mt-2 text-xs text-gray-400">
    ${v.size ? `<span class="flex items-center gap-1"><i class="ph ph-hard-drives text-[10px]"></i>${v.size}</span>` : ''}
    ${v.duration ? `<span class="flex items-center gap-1"><i class="ph ph-clock text-[10px]"></i>${v.duration}</span>` : ''}
    </div>
    </div>
    <div class="flex justify-end gap-2 px-4 pb-4"><button class="text-xs text-gray-500 hover:text-blue-500 border border-gray-200 rounded px-2 py-1" title="重命名视频" onclick="event.stopPropagation(); renameVideo('${arg(v.filename)}')"><i class="ph ph-pencil-simple"></i> 重命名</button><button class="text-xs text-gray-500 hover:text-red-500 border border-gray-200 rounded px-2 py-1" title="删除视频" onclick="event.stopPropagation(); deleteVideo('${arg(v.filename)}')"><i class="ph ph-trash"></i> 删除</button></div>
    </div>`).join('');
    DOM.videoGrid.innerHTML = back + folderItems + videoItems || '<div class="col-span-full text-center py-12 text-gray-400">暂无视频</div>';
}

function playVideo(filename, title) {
    if (!filename) return showToast('视频文件不可用', 'error');
    const existing = document.getElementById('video-player-overlay');
    if (existing) existing.remove();
    const overlay = document.createElement('div');
    overlay.id = 'video-player-overlay';
    overlay.className = 'fixed inset-0 bg-black/70 z-[70] flex items-center justify-center animate-fadeIn';
    overlay.style.animation = 'fadeIn 0.2s ease-out';
    overlay.onclick = function(e) { if (e.target === this) this.remove(); };
    overlay.innerHTML = `
    <div class="bg-white rounded-2xl w-full max-w-4xl mx-4 overflow-hidden shadow-2xl animate-slideUp">
    <div class="flex justify-between items-center px-6 py-4 border-b border-gray-100">
    <div class="flex items-center gap-3 min-w-0">
    <div class="w-8 h-8 rounded-lg bg-[#ebf4ff] flex items-center justify-center text-[#2d8cf0] flex-shrink-0"><i class="ph ph-video-camera text-base"></i></div>
    <span class="font-bold text-sm text-gray-800 truncate">${title}</span>
    </div>
    <button class="w-8 h-8 rounded-full hover:bg-gray-100 flex items-center justify-center text-gray-400 hover:text-gray-600 transition-colors" onclick="document.getElementById('video-player-overlay').remove()">
    <i class="ph ph-x text-xl"></i>
    </button>
    </div>
    <video controls autoplay class="w-full max-h-[75vh] bg-black" onerror="this.parentElement.innerHTML='<div class=\\'flex items-center justify-center h-64 text-gray-400\\'><i class=\\'ph ph-video-slash text-3xl mr-2\\'></i><span>视频加载失败</span></div>'">
    <source src="http://localhost:5000/video/${[...(currentVideoFolder ? currentVideoFolder.split('/') : []), filename].map(encodeURIComponent).join('/')}" type="video/mp4">
    您的浏览器不支持视频播放
    </video>
    </div>`;
    document.body.appendChild(overlay);
}

async function loadVideos() {
    if (!DOM.videoGrid) return;
    DOM.videoGrid.innerHTML = '<div class="text-center py-12 text-gray-400 col-span-full">加载中...</div>';
    try {
        const res = await apiClient.post(API_CONFIG.ENDPOINTS.VIDEOS, { folder: currentVideoFolder });
        if (res?.data) renderVideos(res.data); else throw new Error();
    } catch { 
        renderVideos(MockAI.getVideos());
    }
}

function openVideoFolder(folder) {
    currentVideoFolder = folder;
    localStorage.setItem('video_folder', folder);
    switchPage('video');
}

async function createVideoFolder() {
    const name = prompt('请输入文件夹名称');
    if (!name?.trim()) return;
    const response = await apiClient.post('/videos/folders', { name: name.trim(), parent: currentVideoFolder });
    showToast(response.message || '文件夹创建成功', 'success');
    loadVideos();
}

async function renameResource(endpoint, payload, successMessage) {
    const response = await fetch(apiClient._url(endpoint), { method: 'PATCH', headers: { ...apiClient._headers(), 'Content-Type': 'application/json' }, body: JSON.stringify(payload), credentials: 'include' });
    const result = await response.json();
    showToast(result.message || successMessage, result.code === 200 ? 'success' : 'error');
    if (result.code === 200) loadVideos();
}

async function renameVideo(filename) {
    const name = prompt('请输入新视频名称（可不填写扩展名）');
    if (!name?.trim()) return;
    await renameResource('/videos/rename', { folder: currentVideoFolder, filename: decodeURIComponent(filename), new_name: name.trim() }, '视频重命名成功');
}

async function deleteVideo(filename) {
    return openConfirmModal('确定删除这个视频吗？', () => deleteVideoRecord(filename));
}

async function deleteVideoRecord(filename) {
    const response = await fetch(apiClient._url('/videos/delete'), { method: 'DELETE', headers: { ...apiClient._headers(), 'Content-Type': 'application/json' }, body: JSON.stringify({ folder: currentVideoFolder, filename: decodeURIComponent(filename) }), credentials: 'include' });
    const result = await response.json();
    showToast(result.message || '操作完成', result.code === 200 ? 'success' : 'error');
    if (result.code === 200) loadVideos();
}

async function renameVideoFolder(folder) {
    const name = prompt('请输入新文件夹名称');
    if (!name?.trim()) return;
    await renameResource('/videos/folders', { folder, new_name: name.trim() }, '文件夹重命名成功');
}

async function deleteVideoFolder(folder) {
    return openConfirmModal('确定删除这个空文件夹吗？', () => deleteVideoFolderRecord(folder));
}

async function deleteVideoFolderRecord(folder) {
    const response = await fetch(apiClient._url('/videos/folders'), { method: 'DELETE', headers: { ...apiClient._headers(), 'Content-Type': 'application/json' }, body: JSON.stringify({ folder }), credentials: 'include' });
    const result = await response.json();
    showToast(result.message || '操作完成', result.code === 200 ? 'success' : 'error');
    if (result.code === 200) loadVideos();
}

async function handleVideoUpload(input) {
    const file = input.files?.[0];
    if (!file) return;
    
    // 校验文件大小（最大 500MB）
    const maxSize = 500 * 1024 * 1024;
    if (file.size > maxSize) {
        showToast('视频文件不能超过 500MB', 'error');
        input.value = '';
        return;
    }

// 校验是否是教师或管理员
    if (!AppState.isLoggedIn || (AppState.currentUser?.role !== 'teacher' && AppState.currentUser?.role !== 'admin')) {
        showToast('仅教师或管理员可上传视频', 'warning');
        input.value = '';
        return;
    }

    const formData = new FormData();
    formData.append('file', file);
    formData.append('folder', currentVideoFolder);

    showToast('正在上传视频...', 'info');

    try {
        const resp = await fetch(apiClient._url(API_CONFIG.ENDPOINTS.VIDEO_UPLOAD), {
            method: 'POST',
            headers: apiClient._uploadHeaders(),
            body: formData,
            credentials: 'include'
        });
        const result = await resp.json();
        if (result.code === 200) {
            showToast(`视频「${result.data.title}」上传成功`, 'success');
            input.value = '';
            loadVideos(); // 刷新列表
        } else {
            showToast(result.message || '上传失败', 'error');
        }
    } catch (err) {
        showToast(`上传失败：${err.message}`, 'error');
    }
    input.value = '';
}

function downloadMaterial(filename, name, folder = '') {
    if (!filename) return showToast('文件不可用', 'error');
    const path = [...(folder ? folder.split('/') : []), filename].map(encodeURIComponent).join('/');
    const url = `http://localhost:5000/materials/${path}`;
    // 对可预览的文件直接打开，其余下载
    const previewable = ['pdf', 'txt', 'md', 'png', 'jpg', 'jpeg'];
    const ext = filename.split('.').pop().toLowerCase();
    if (previewable.includes(ext)) {
        window.open(url, '_blank');
    } else {
        const a = document.createElement('a');
        a.href = url;
        a.download = name + '.' + ext;
        document.body.appendChild(a);
        a.click();
        a.remove();
    }
}

async function loadMaterials() {
    if (!DOM.materialList) return;
    DOM.materialList.innerHTML = '<div class="text-center py-12 text-gray-400">加载中...</div>';
    try {
        const res = await apiClient.post(API_CONFIG.ENDPOINTS.MATERIALS, { folder: currentMaterialFolder });
        if (res?.data) renderMaterials(res.data); else throw new Error();
    } catch { 
        renderMaterials(MockAI.getMaterials());
    }
}

let currentMaterialFolder = localStorage.getItem('material_folder') || '';

function escapeAttribute(value) {
    return String(value).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderMaterials(data) {
    const materials = Array.isArray(data) ? data : (data.materials || []);
    const folders = Array.isArray(data) ? [] : (data.folders || []);
    if (!DOM.materialList) return;
    const countEl = document.getElementById('material-count');
    if (countEl) countEl.textContent = materials.length;
        const jsArg = value => encodeURIComponent(value);
        const parent = currentMaterialFolder.includes('/') ? currentMaterialFolder.split('/').slice(0, -1).join('/') : '';
        const backItem = currentMaterialFolder ? `<div class="flex items-center px-5 py-3 border-b bg-gray-50"><button class="text-sm text-[#2d8cf6] hover:underline" onclick="openMaterialFolder(decodeURIComponent('${jsArg(parent)}'))"><i class="ph ph-arrow-left"></i> 返回上级</button><span class="ml-3 text-xs text-gray-400">/${escapeAttribute(currentMaterialFolder)}</span></div>` : '';
        const folderItems = folders.map(folder => `<div class="group flex items-center justify-between px-5 py-4 hover:bg-[#f8faff] cursor-pointer" onclick="openMaterialFolder(decodeURIComponent('${jsArg(folder.path)}'))"><div class="flex items-center gap-4"><i class="ph ph-folder text-2xl text-amber-500"></i><span class="text-sm font-medium">${escapeAttribute(folder.name)}</span></div><div class="flex gap-2"><button class="text-gray-400 hover:text-blue-500" title="重命名文件夹" onclick="event.stopPropagation(); renameMaterialFolder(decodeURIComponent('${jsArg(folder.path)}'))"><i class="ph ph-pencil-simple"></i></button><button class="text-gray-400 hover:text-red-500" title="删除空文件夹" onclick="event.stopPropagation(); deleteMaterialFolder(decodeURIComponent('${jsArg(folder.path)}'))"><i class="ph ph-trash text-lg"></i></button></div></div>`).join('');
        const materialItems = materials.map(m => `<div class="group flex items-center justify-between px-5 py-4 hover:bg-[#f8faff] cursor-pointer" onclick="downloadMaterial(decodeURIComponent('${jsArg(m.filename || '')}'), decodeURIComponent('${jsArg(m.name || '')}'), decodeURIComponent('${jsArg(currentMaterialFolder)}'))"><div class="flex items-center gap-4 min-w-0"><div class="w-11 h-11 rounded-xl bg-gray-50 border border-gray-100 flex items-center justify-center flex-shrink-0"><i class="ph ${m.icon || 'ph-file'} ${m.color || 'text-gray-500'} text-2xl"></i></div><div class="min-w-0"><div class="text-sm font-medium text-gray-800 truncate">${escapeAttribute(m.name)}</div><div class="text-xs text-gray-400 mt-0.5">${m.size} · ${(m.ext || '').toUpperCase()} 文件</div></div></div><div class="flex items-center gap-3 flex-shrink-0"><span class="text-[10px] px-2 py-1 rounded-full bg-gray-100 text-gray-500">查看</span><button class="text-gray-400 hover:text-blue-500" title="重命名文件" onclick="event.stopPropagation(); renameMaterial(decodeURIComponent('${jsArg(m.filename)}'))"><i class="ph ph-pencil-simple"></i></button><button class="text-gray-300 hover:text-red-500" title="删除文件" onclick="event.stopPropagation(); deleteMaterial(decodeURIComponent('${jsArg(m.filename)}'))"><i class="ph ph-trash text-lg"></i></button></div></div>`).join('');
        DOM.materialList.innerHTML = backItem + folderItems + materialItems || '<div class="text-center py-12 text-gray-400">暂无资料</div>';
}

function openMaterialFolder(folder) {
    currentMaterialFolder = folder;
    localStorage.setItem('material_folder', folder);
    switchPage('material');
}

async function createMaterialFolder() {
    const name = prompt('请输入文件夹名称');
    if (!name?.trim()) return;
    const response = await apiClient.post('/materials/folders', { name: name.trim(), parent: currentMaterialFolder });
    showToast(response.message || '文件夹创建成功', 'success');
    loadMaterials();
}

async function deleteMaterial(filename) {
    return openConfirmModal('确定删除这个文件吗？', () => deleteMaterialRecord(filename));
}

async function deleteMaterialRecord(filename) {
    const response = await fetch(apiClient._url('/materials/delete'), { method: 'DELETE', headers: { ...apiClient._headers(), 'Content-Type': 'application/json' }, body: JSON.stringify({ filename, folder: currentMaterialFolder }), credentials: 'include' });
    const result = await response.json();
    showToast(result.message || '操作完成', result.code === 200 ? 'success' : 'error');
    if (result.code === 200) loadMaterials();
}

async function deleteMaterialFolder(folder) {
    return openConfirmModal('确定删除这个空文件夹吗？', () => deleteMaterialFolderRecord(folder));
}

async function deleteMaterialFolderRecord(folder) {
    const response = await fetch(apiClient._url('/materials/folders'), { method: 'DELETE', headers: { ...apiClient._headers(), 'Content-Type': 'application/json' }, body: JSON.stringify({ folder }), credentials: 'include' });
    const result = await response.json();
    showToast(result.message || '操作完成', result.code === 200 ? 'success' : 'error');
    if (result.code === 200) loadMaterials();
}

async function renameMaterial(filename) {
    const name = prompt('请输入新文件名（可不填写扩展名）');
    if (!name?.trim()) return;
    const response = await fetch(apiClient._url('/materials/rename'), { method: 'PATCH', headers: { ...apiClient._headers(), 'Content-Type': 'application/json' }, body: JSON.stringify({ filename, folder: currentMaterialFolder, new_name: name.trim() }), credentials: 'include' });
    const result = await response.json();
    showToast(result.message || '操作完成', result.code === 200 ? 'success' : 'error');
    if (result.code === 200) loadMaterials();
}

async function renameMaterialFolder(folder) {
    const name = prompt('请输入新文件夹名称');
    if (!name?.trim()) return;
    const response = await fetch(apiClient._url('/materials/folders'), { method: 'PATCH', headers: { ...apiClient._headers(), 'Content-Type': 'application/json' }, body: JSON.stringify({ folder, new_name: name.trim() }), credentials: 'include' });
    const result = await response.json();
    showToast(result.message || '操作完成', result.code === 200 ? 'success' : 'error');
    if (result.code === 200) {
        if (currentMaterialFolder === folder) {
            currentMaterialFolder = folder.split('/').slice(0, -1).concat(name.trim()).join('/');
            localStorage.setItem('material_folder', currentMaterialFolder);
        }
        loadMaterials();
    }
}

async function handleMaterialUpload(input) {
    const files = input.files;
    if (!files?.length) return;

// 校验是否是教师或管理员
    if (!AppState.isLoggedIn || (AppState.currentUser?.role !== 'teacher' && AppState.currentUser?.role !== 'admin')) {
        showToast('仅教师或管理员可上传课件', 'warning');
        input.value = '';
        return;
    }

    const maxSize = 200 * 1024 * 1024;
    for (const file of files) {
        if (file.size > maxSize) {
            showToast(`「${file.name}」超过 200MB 限制`, 'error');
            continue;
        }
        const formData = new FormData();
        formData.append('file', file);
        formData.append('folder', currentMaterialFolder);

        showToast(`正在上传「${file.name}」...`, 'info');

        try {
            const resp = await fetch(apiClient._url(API_CONFIG.ENDPOINTS.MATERIAL_UPLOAD), {
                method: 'POST',
                headers: apiClient._uploadHeaders(),
                body: formData,
                credentials: 'include'
            });
            const result = await resp.json();
            if (result.code === 200) {
                showToast(`「${result.data.name}」上传成功`, 'success');
            } else {
                showToast(result.message || `「${file.name}」上传失败`, 'error');
            }
        } catch (err) {
            showToast(`「${file.name}」上传失败：${err.message}`, 'error');
        }
    }
    input.value = '';
    switchPage('material');
}

// ==================== 核心聊天与路由 ====================
function switchPage(target) {
    if (target !== 'ai-chat' && AppState.isGuest) return requireLogin();
    localStorage.setItem('active_page', target);
    DOM.sidebarItems.forEach(i => i.classList.remove('active'));
    document.querySelector(`[data-page="${target}"]`)?.classList.add('active');
    DOM.pages.forEach(p => p.classList.add('hidden'));
    document.getElementById(`page-${target}`)?.classList.remove('hidden');
    if (target === 'video') loadVideos();
    if (target === 'material') loadMaterials();
    if (target === 'homework') loadHomeworks();
    if (target === 'course' && typeof loadCoursePage === 'function') loadCoursePage();
}

function requireLogin() {
    openAuthModal();
    showToast('该功能需要登录后使用', 'warning');
    return false;
}

function setChatMode(mode) {
    const config = {
        code: { placeholder: '输入编程问题或粘贴代码...', btn: DOM.modeBtns.code },
        qa: { placeholder: '输入概念或疑问...', btn: DOM.modeBtns.qa },
        diagnosis: { placeholder: '输入学情诊断需求...', btn: DOM.modeBtns.diagnosis },
    };
    Object.values(DOM.modeBtns).filter(Boolean).forEach(b => b.className = 'px-3 py-1.5 text-gray-500 rounded-md text-xs font-medium transition-all');
    if (config[mode]?.btn) config[mode].btn.className = 'px-3 py-1.5 bg-white text-[#2d8cf0] shadow-sm rounded-md text-xs font-medium transition-all';
    DOM.userInput.placeholder = config[mode]?.placeholder || '输入您的问题...';
}

async function searchStudent() {
    const studentNumber = document.getElementById('student-search-number').value.trim();
    if (!studentNumber) return;
    try {
        const response = await apiClient.get(`/students/${encodeURIComponent(studentNumber)}`);
        const student = response.data;
        document.getElementById('student-info-number').value = student.student_number;
        document.getElementById('student-info-name').value = student.username;
        document.getElementById('student-info-email').value = student.email;
        document.getElementById('student-info-password').value = '';
        document.getElementById('student-info-form').classList.remove('hidden');
    } catch (error) {
        document.getElementById('student-info-form').classList.add('hidden');
        showToast(`查询失败：${error.message}`, 'error');
    }
}

async function updateStudentInfo() {
    const studentNumber = document.getElementById('student-info-number').value;
    const payload = {
        username: document.getElementById('student-info-name').value.trim(),
        email: document.getElementById('student-info-email').value.trim(),
        password: document.getElementById('student-info-password').value
    };
    try {
        const response = await fetch(apiClient._url(`/students/${encodeURIComponent(studentNumber)}`), { method: 'PATCH', headers: apiClient._headers(), body: JSON.stringify(payload), credentials: 'include' });
        const result = await response.json();
        if (!response.ok || result.code !== 200) throw new Error(result.message || '保存失败');
        document.getElementById('student-info-password').value = '';
        showToast('学生信息已更新', 'success');
    } catch (error) {
        showToast(`保存失败：${error.message}`, 'error');
    }
}

function insertPrompt(text) { DOM.userInput.value = text; DOM.userInput.dispatchEvent(new Event('input')); DOM.userInput.focus(); }
function scrollToBottom() { DOM.chatStream?.scrollTo({ top: DOM.chatStream.scrollHeight, behavior: 'smooth' }); }

function renderAttachmentPreview() {
    DOM.attachmentPreview.classList.remove('hidden');
    DOM.attachmentPreview.classList.add('flex');
    DOM.attachmentPreview.innerHTML = `<i class="ph ph-paperclip flex-shrink-0"></i><span class="truncate" title="${escapeAttribute(currentAttachment.name)}">${escapeAttribute(currentAttachment.name)}</span><button type="button" class="flex-shrink-0 text-gray-400 hover:text-red-500" title="移除附件" onclick="clearAttachment()"><i class="ph ph-x"></i></button>`;
}

async function uploadAttachment(file) {
    if (AppState.isGuest) return requireLogin();
    const allowed = ['.txt', '.pdf', '.doc', '.docx'];
    const extension = `.${file.name.split('.').pop().toLowerCase()}`;
    if (!allowed.includes(extension)) return showToast('仅支持 txt、pdf、word 文件', 'warning');
    if (file.size > 20 * 1024 * 1024) return showToast('附件不能超过 20MB', 'warning');
    const hadPrevious = !!currentAttachment;
    const formData = new FormData();
    formData.append('file', file);
    try {
        const response = await fetch(apiClient._url('/attachments/upload'), { method: 'POST', headers: apiClient._uploadHeaders(), body: formData, credentials: 'include' });
        const result = await response.json();
        if (!response.ok || result.code !== 200) throw new Error(result.message || '附件上传失败');
        currentAttachment = result.data;
        sessionStorage.setItem('current_attachment', JSON.stringify(currentAttachment));
        renderAttachmentPreview();
        showToast(hadPrevious ? '附件已替换' : '附件上传成功', 'success');
    } catch (error) { showToast(`附件上传失败：${error.message}`, 'error'); }
    DOM.attachmentInput.value = '';
}

function clearAttachment() {
    currentAttachment = null;
    sessionStorage.removeItem('current_attachment');
    DOM.attachmentPreview.classList.add('hidden');
    DOM.attachmentPreview.classList.remove('flex');
    DOM.attachmentPreview.innerHTML = '';
}

function toggleDarkMode() {
    const enabled = document.body.classList.toggle('dark-mode');
    localStorage.setItem('dark_mode', enabled ? '1' : '0');
    const button = document.getElementById('theme-toggle-btn');
    if (button) {
        button.innerHTML = `<i class="ph ${enabled ? 'ph-sun' : 'ph-moon'} text-lg"></i>`;
        button.title = enabled ? '切换亮色模式' : '切换暗色模式';
        button.setAttribute('aria-label', button.title);
    }
}

function initializeDarkMode() {
    if (localStorage.getItem('dark_mode') === '1') toggleDarkMode();
    document.getElementById('theme-toggle-btn')?.addEventListener('click', toggleDarkMode);
}

function setSidebarCollapsed(collapsed) {
    DOM.sidebar?.classList.toggle('sidebar-collapsed', collapsed);
    if (DOM.sidebarToggleBtn) {
        DOM.sidebarToggleBtn.title = collapsed ? '展开侧边栏' : '收缩侧边栏';
        DOM.sidebarToggleBtn.setAttribute('aria-label', DOM.sidebarToggleBtn.title);
    }
    localStorage.setItem('sidebar_collapsed', collapsed ? '1' : '0');
}

function toggleSidebar() {
    setSidebarCollapsed(!DOM.sidebar?.classList.contains('sidebar-collapsed'));
}

function initializeSidebar() {
    setSidebarCollapsed(localStorage.getItem('sidebar_collapsed') === '1');
}

function toggleVoiceInput() {
    if (AppState.isGuest) return requireLogin();
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) return showToast('当前浏览器不支持语音输入，请使用最新版 Chrome 或 Edge', 'warning');
    if (isSpeechRecognizing) {
        speechRecognition.stop();
        return;
    }
    speechRecognition = new Recognition();
    speechRecognition.lang = 'zh-CN';
    speechRecognition.continuous = true;
    speechRecognition.interimResults = true;
    speechBaseText = DOM.userInput.value.trim();
    speechFinalText = '';
    isSpeechRecognizing = true;
    DOM.voiceInputBtn.classList.add('text-[#e74c3c]', 'bg-red-50');
    DOM.voiceInputBtn.title = '停止语音输入';
    showToast('正在听，请开始说话', 'info');
    speechRecognition.onresult = event => {
        let interimText = '';
        for (let index = event.resultIndex; index < event.results.length; index++) {
            const transcript = event.results[index][0].transcript;
            if (event.results[index].isFinal) speechFinalText += transcript;
            else interimText += transcript;
        }
        const recognizedText = speechFinalText + interimText;
        DOM.userInput.value = `${speechBaseText}${speechBaseText && recognizedText ? ' ' : ''}${recognizedText}`;
        DOM.userInput.dispatchEvent(new Event('input'));
    };
    speechRecognition.onerror = event => {
        if (event.error === 'not-allowed') showToast('请允许浏览器使用麦克风', 'warning');
        else if (event.error !== 'aborted') showToast(`语音识别失败：${event.error}`, 'error');
    };
    speechRecognition.onend = () => {
        isSpeechRecognizing = false;
        DOM.voiceInputBtn.classList.remove('text-[#e74c3c]', 'bg-red-50');
        DOM.voiceInputBtn.title = '开始语音输入';
    };
    speechRecognition.start();
}

function appendUserMessage(content) {
    const safe = content.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const avatar = AppState.isLoggedIn ? `https://api.dicebear.com/7.x/notionists/svg?seed=${encodeURIComponent(AppState.currentUser.name)}&backgroundColor=e0e7ff` : 'https://api.dicebear.com/7.x/notionists/svg?seed=Guest&backgroundColor=f0f0f0';
    const html = `<div class="flex items-start max-w-3xl mx-auto flex-row-reverse msg-enter"><img class="w-8 h-8 rounded-full ml-3 border" src="${avatar}"><div class="bg-[#2d8cf0] text-white rounded-xl rounded-tr-none px-4 py-3 text-sm max-w-[80%] whitespace-pre-wrap shadow-sm">${safe}</div></div>`;
    DOM.chatStream.insertAdjacentHTML('beforeend', html); scrollToBottom();
}

function appendLoader() {
    const id = 'loader-' + Date.now();
    const html = `<div id="${id}" class="flex items-start max-w-3xl mx-auto msg-enter"><div class="w-8 h-8 rounded-full bg-[#ebf4ff] flex items-center justify-center text-[#2d8cf0] mr-3"><i class="ph ph-robot text-lg animate-pulse"></i></div><div class="bg-white border rounded-xl rounded-tl-none px-5 py-4 shadow-sm min-w-[200px]"><div class="flex items-center text-xs font-medium text-[#2d8cf0] mb-3"><i class="ph ph-spinner-gap animate-spin mr-2"></i>正在分析...</div><div class="space-y-2"><div class="h-2.5 w-full rounded shimmer"></div><div class="h-2.5 w-3/4 rounded shimmer"></div></div></div></div>`;
    DOM.chatStream.insertAdjacentHTML('beforeend', html); scrollToBottom(); return id;
}

async function handleSend() {
    const text = DOM.userInput.value.trim();
    if (!text || AppState.isGenerating) return;
    if (AppState.isGuest && AppState.guestQueryCount >= MAX_GUEST_QUERIES) { return requireLogin(); }

    AppState.isGenerating = true; DOM.userInput.value = ''; DOM.userInput.style.height = 'auto';
    DOM.userInput.disabled = true; DOM.sendBtn.disabled = true; DOM.sendBtn.classList.add('hidden'); DOM.stopBtn.classList.remove('hidden'); DOM.stopBtn.classList.add('flex');
    if (AppState.isGuest) {
        AppState.guestQueryCount++;
        localStorage.setItem('guest_query_count', String(AppState.guestQueryCount));
    }

    appendUserMessage(text);
    const loaderId = appendLoader();

    try {
        const res = await apiClient.post(API_CONFIG.ENDPOINTS.CHAT, { prompt: text, mode: AppState.currentMode, attachment_name: currentAttachment?.filename || '' });
        if (apiClient.isAborted) return document.getElementById(loaderId)?.remove();
        document.getElementById(loaderId)?.remove();
        
        const id = 'ai-' + Date.now();
        DOM.chatStream.insertAdjacentHTML('beforeend', `<div class="flex items-start max-w-3xl mx-auto msg-enter" id="${id}"><div class="w-8 h-8 rounded-full bg-[#ebf4ff] flex items-center justify-center text-[#2d8cf0] mr-3 mt-1"><i class="ph ph-robot text-lg"></i></div><div class="bg-white border rounded-xl rounded-tl-none px-5 py-4 text-sm w-full shadow-sm min-w-0"><div class="prose max-w-none min-h-[40px] prose-content"></div></div></div>`);
        const contentEl = document.querySelector(`#${id} .prose-content`);
        contentEl.innerHTML = marked.parse(res.data.content);
        contentEl.querySelectorAll('pre code').forEach(b => { try { hljs.highlightElement(b); } catch(e){} });
        scrollToBottom();
        loadHistoryList();
        clearAttachment();
    } catch (err) {
        document.getElementById(loaderId)?.remove();
        if (err.message !== '请求已取消') {
            const message = err.message === 'Failed to fetch' ? '无法连接后端服务，请确认后端已启动（python backend/app.py）' : `请求错误: ${err.message}`;
            showToast(message, 'error');
        }
    } finally {
        AppState.isGenerating = false; DOM.userInput.disabled = false; DOM.sendBtn.disabled = false;
        DOM.sendBtn.classList.remove('hidden'); DOM.stopBtn.classList.add('hidden'); DOM.stopBtn.classList.remove('flex'); DOM.userInput.focus();
    }
}

function stopGeneration() {
    apiClient.stopGeneration();
    apiClient.abort();
    AppState.isGenerating = false;
    showToast('已停止生成', 'warning');
}

async function init() {
    marked.setOptions({ breaks: true, gfm: true });
    hljs.configure({ ignoreUnescapedHTML: true });
    try { currentAttachment = JSON.parse(sessionStorage.getItem('current_attachment') || 'null'); } catch { currentAttachment = null; }
    if (currentAttachment?.name) renderAttachmentPreview();
    localStorage.removeItem('theme_color');
    initializeDarkMode();
    initializeSidebar();
    
    DOM.sidebarItems.forEach(item => item.addEventListener('click', () => switchPage(item.dataset.page)));
    DOM.userInput.addEventListener('input', function() {
        this.style.height = 'auto'; this.style.height = Math.min(this.scrollHeight, 150) + 'px';
        DOM.charCount.textContent = `${this.value.length}/2000`;
        DOM.charCount.classList.toggle('text-red-500', this.value.length >= 2000);
        DOM.sendBtn.disabled = this.value.length === 0 || AppState.isGenerating;
    });
    DOM.userInput.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } });
    DOM.voiceInputBtn?.addEventListener('click', toggleVoiceInput);
    DOM.attachmentBtn?.addEventListener('click', () => AppState.isGuest ? requireLogin() : DOM.attachmentInput?.click());
    DOM.attachmentInput?.addEventListener('change', event => { const file = event.target.files?.[0]; if (file) uploadAttachment(file); });
    
    updateUserUI();
    await restoreSession();
    await loadHistoryList();
    const savedPage = localStorage.getItem('active_page');
    if (savedPage && document.getElementById(`page-${savedPage}`)) switchPage(savedPage);
}

document.addEventListener('DOMContentLoaded', init);