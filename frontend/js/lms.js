// ==================== 课程空间（仿学习通 + 本地大模型） ====================
// 依赖：main.js 的 switchPage/showToast/AppState，agents.js 的 apiGet/apiSend/esc/md
const LMS = {
    courses: [], courseId: null, detail: null, tab: 'chapters',
    quizzes: [], quiz: null, posts: [], post: null, signins: [], notices: [],
    dashboard: null, advice: null, form: null, loading: false, aiBusy: false, msg: '',
};

function lmsRoot() { return document.getElementById('lms-root'); }

function lmsCard(inner, extra = '') {
    return '<div class="bg-white border border-gray-200 rounded-xl shadow-sm ' + extra + '">' + inner + '</div>';
}

function lmsPill(text, color = 'bg-gray-50 text-gray-500') {
    return '<span class="px-2 py-0.5 rounded-full text-[11px] ' + color + '">' + esc(text) + '</span>';
}

function lmsAiBadge(meta) {
    if (!meta) return '';
    const model = meta.model || '本地模型';
    const sec = meta.latency_ms ? (meta.latency_ms / 1000).toFixed(1) + ' s' : '';
    const label = meta.degraded ? '离线编排' : 'AI 生成';
    const color = meta.degraded ? 'bg-amber-50 text-amber-700' : 'bg-violet-50 text-violet-700';
    return '<span class="px-2 py-0.5 rounded-full text-[11px] ' + color + '"><i class="ph ph-sparkle mr-1"></i>'
        + esc(label + ' · ' + model + (sec ? ' · ' + sec : '')) + '</span>';
}

function lmsCiteChips(citations) {
    if (!citations || !citations.length) return '';
    return citations.map(function (c, i) {
        return '<span class="px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 text-[11px]" title="' + esc(c.citation || '') + '">['
            + (c.index || i + 1) + '] ' + esc(c.locator || c.doc_title || '出处') + '</span>';
    }).join(' ');
}

function lmsPercent(value) {
    if (value === null || value === undefined) return '—';
    return Math.round(value * 100) + '%';
}

// ---------- 首页 ----------
async function loadCoursePage() {
    const root = lmsRoot();
    if (!root) return;
    root.innerHTML = '<div class="text-sm text-gray-400 py-10 text-center"><i class="ph ph-circle-notch animate-spin mr-2"></i>加载课程…</div>';
    try {
        const overview = await apiGet('/lms/overview');
        const data = await apiGet('/lms/courses');
        LMS.courses = data.items || [];
        if (LMS.courseId && !LMS.courses.some(c => c.id === LMS.courseId)) LMS.courseId = null;
        if (!LMS.courseId && LMS.courses.length) LMS.courseId = LMS.courses[0].id;
        renderHome(overview);
        // 深链：#course-quiz / #course-discuss… 可直接定位到某门课的某个标签页
        const deep = location.hash.match(/^#course-(chapters|quiz|discuss|signin|notice|data)$/);
        if (deep && LMS.courses.length) {
            await lmsEnterCourse(LMS.courses[0].id);
            LMS.tab = deep[1];
            await lmsTab(deep[1]);
        }
        // 深链：#course-discuss-12 直接打开某个讨论帖（便于老师把帖子链接发到群里）
        const deepPost = location.hash.match(/^#course-discuss-(\d+)$/);
        if (deepPost && LMS.courses.length) {
            await lmsEnterCourse(LMS.courses[0].id);
            await lmsTab('discuss');
            await lmsOpenPost(parseInt(deepPost[1], 10));
        }
    } catch (error) {
        root.innerHTML = lmsCard('<div class="p-6 text-sm text-red-600">课程空间加载失败：' + esc(error.message)
            + '<div class="text-xs text-gray-400 mt-2">请确认已登录，且后端与本机模型在运行。</div></div>');
    }
}

function renderHome(overview) {
    const root = lmsRoot();
    if (!root) return;
    const todo = (overview && overview.todo) || [];
    const unread = (overview && overview.unread_notices) || 0;
    let html = '';

    html += '<div class="grid grid-cols-2 md:grid-cols-4 gap-3">'
        + lmsCard('<div class="p-4"><div class="text-[11px] text-gray-400">我的课程</div><div class="text-2xl font-bold text-gray-900 mt-1">' + LMS.courses.length + '</div></div>')
        + lmsCard('<div class="p-4"><div class="text-[11px] text-gray-400">待办事项</div><div class="text-2xl font-bold text-[#2d8cf0] mt-1">' + todo.length + '</div></div>')
        + lmsCard('<div class="p-4"><div class="text-[11px] text-gray-400">未读通知</div><div class="text-2xl font-bold text-amber-500 mt-1">' + unread + '</div></div>')
        + lmsCard('<div class="p-4"><div class="text-[11px] text-gray-400">AI 引擎</div><div class="text-xs font-medium text-emerald-600 mt-2">本机模型 · 不出网</div></div>')
        + '</div>';

    if (LMS.form === 'join' || LMS.form === 'create') {
        html += lmsCard('<div class="p-4 space-y-2">'
            + '<div class="text-xs font-semibold text-gray-700">' + (LMS.form === 'join' ? '输入课程邀请码加入' : '新建课程（教师）') + '</div>'
            + (LMS.form === 'join'
                ? '<input id="lms-join-code" class="auth-input" placeholder="例如 DS2026">'
                : '<input id="lms-new-title" class="auth-input" placeholder="课程名称，例如 数据结构与算法">'
                  + '<input id="lms-new-code" class="auth-input" placeholder="邀请码（留空自动生成）">'
                  + '<input id="lms-new-term" class="auth-input" placeholder="学期，例如 2026 秋">')
            + '<div class="flex gap-2"><button onclick="' + (LMS.form === 'join' ? 'lmsJoin()' : 'lmsCreateCourse()')
            + '" class="px-4 py-2 bg-[#2d8cf0] text-white rounded-lg text-xs">确定</button>'
            + '<button onclick="LMS.form=null;renderHome(null)" class="px-4 py-2 border border-gray-200 rounded-lg text-xs">取消</button></div>'
            + '</div>', 'mt-0');
    }

    if (todo.length) {
        html += '<div class="flex gap-2 overflow-x-auto pb-1">'
            + todo.map(function (item) {
                return '<button onclick="lmsEnterCourse(' + item.course_id + ')" class="flex-shrink-0 text-left bg-white border border-gray-200 rounded-xl px-4 py-3 hover:border-[#2d8cf0] transition-colors">'
                    + '<div class="text-[11px] text-gray-400">' + esc(item.course_title) + ' · ' + (item.type === 'quiz' ? '测验' : '任务点') + '</div>'
                    + '<div class="text-sm font-medium text-gray-800 mt-0.5 max-w-[220px] truncate">' + esc(item.label) + '</div>'
                    + '<div class="text-[11px] text-amber-600 mt-0.5">' + esc(item.detail) + '</div></button>';
            }).join('') + '</div>';
    }

    html += '<div class="grid grid-cols-1 md:grid-cols-2 gap-3">' + LMS.courses.map(function (course) {
        const rate = course.task_total ? Math.round((course.task_done / course.task_total) * 100) : 0;
        return '<button onclick="lmsEnterCourse(' + course.id + ')" class="text-left bg-white border border-gray-200 rounded-xl shadow-sm p-4 hover:shadow-md hover:border-[#2d8cf0] transition-all">'
            + '<div class="flex items-center gap-2 mb-2"><span class="w-2 h-2 rounded-full" style="background:' + esc(course.color) + '"></span>'
            + '<span class="font-semibold text-gray-900">' + esc(course.title) + '</span>'
            + '<span class="ml-auto text-[11px] text-gray-400">' + esc(course.term || '') + '</span></div>'
            + '<div class="text-[11px] text-gray-400 mb-3">' + esc(course.teacher_name || '') + ' · ' + (course.student_count || 0) + ' 名学生 · 邀请码 ' + esc(course.code) + '</div>'
            + '<div class="h-1.5 bg-gray-100 rounded-full overflow-hidden mb-2"><div class="h-full bg-[#2d8cf0]" style="width:' + rate + '%"></div></div>'
            + '<div class="flex flex-wrap gap-1.5">'
            + lmsPill('任务点 ' + course.task_done + '/' + course.task_total, 'bg-blue-50 text-blue-700')
            + lmsPill('测验均分 ' + (course.quiz_avg === null ? '—' : Math.round(course.quiz_avg * 100)), 'bg-emerald-50 text-emerald-700')
            + lmsPill('签到 ' + course.signin_checked + '/' + course.signin_total, 'bg-gray-50 text-gray-600')
            + (course.unread_notices ? lmsPill('未读 ' + course.unread_notices, 'bg-amber-50 text-amber-700') : '')
            + '</div></button>';
    }).join('') + '</div>';
    root.innerHTML = html;
}

function lmsOpenJoin() { LMS.form = 'join'; LMS.courseId = null; loadCoursePage().then(function () { showLmsForm(); }); }
function lmsOpenCreateCourse() { LMS.form = 'create'; loadCoursePage().then(function () { showLmsForm(); }); }
function showLmsForm() {
    const input = document.getElementById('lms-join-code') || document.getElementById('lms-new-title');
    if (input) input.focus();
}

async function lmsJoin() {
    const code = (document.getElementById('lms-join-code') || {}).value || '';
    if (!code.trim()) return showToast('请输入邀请码', 'warning');
    try {
        const course = await apiSend('/lms/courses/join', { code: code.trim() });
        showToast('已加入《' + course.title + '》', 'success');
        LMS.form = null; LMS.courseId = course.id;
        await loadCoursePage();
    } catch (error) { showToast(error.message, 'error'); }
}

async function lmsCreateCourse() {
    const title = (document.getElementById('lms-new-title') || {}).value || '';
    const code = (document.getElementById('lms-new-code') || {}).value || '';
    const term = (document.getElementById('lms-new-term') || {}).value || '';
    if (!title.trim()) return showToast('请输入课程名称', 'warning');
    try {
        const course = await apiSend('/lms/courses', { title: title.trim(), code: code.trim(), term: term.trim() });
        showToast('课程已创建，邀请码 ' + course.code, 'success');
        LMS.form = null; LMS.courseId = course.id;
        await loadCoursePage();
    } catch (error) { showToast(error.message, 'error'); }
}

// ---------- 课程详情 ----------
async function lmsEnterCourse(courseId) {
    LMS.courseId = courseId; LMS.tab = 'chapters'; LMS.detail = null; LMS.quiz = null; LMS.post = null;
    const root = lmsRoot();
    if (root) root.innerHTML = '<div class="text-sm text-gray-400 py-10 text-center"><i class="ph ph-circle-notch animate-spin mr-2"></i>进入课程…</div>';
    try {
        LMS.detail = await apiGet('/lms/courses/' + courseId);
        await lmsTab('chapters');
    } catch (error) {
        if (root) root.innerHTML = lmsCard('<div class="p-6 text-sm text-red-600">' + esc(error.message) + '</div>');
    }
}

function lmsCourseHeader() {
    const d = LMS.detail;
    if (!d) return '';
    const course = d.course;
    const rate = course.task_total ? Math.round((course.task_done / course.task_total) * 100) : 0;
    return '<div class="bg-white border border-gray-200 rounded-xl shadow-sm p-4">'
        + '<div class="flex items-start gap-3">'
        + '<button onclick="lmsBack()" class="mt-0.5 text-gray-400 hover:text-gray-600"><i class="ph ph-arrow-left text-lg"></i></button>'
        + '<div class="flex-1 min-w-0">'
        + '<div class="flex flex-wrap items-center gap-2"><h3 class="font-bold text-gray-900 text-lg">' + esc(course.title) + '</h3>'
        + lmsPill(course.term || '', 'bg-gray-50 text-gray-500') + lmsPill(d.teacher_view ? '教师视角' : '学生视角', d.teacher_view ? 'bg-violet-50 text-violet-700' : 'bg-blue-50 text-blue-700') + '</div>'
        + '<div class="text-[11px] text-gray-400 mt-1">' + esc(d.teacher_name || '') + ' · 邀请码 ' + esc(course.code)
        + (d.teacher_view ? ' · 全班 ' + ((d.class && d.class.student_count) || 0) + ' 人'
            : ' · 任务点 ' + course.task_done + '/' + course.task_total + ' · 学习 ' + course.minutes + ' 分钟') + '</div>'
        + '<div class="h-1.5 bg-gray-100 rounded-full overflow-hidden mt-2"><div class="h-full bg-[#2d8cf0]" style="width:' + rate + '%"></div></div>'
        + '</div></div></div>';
}

function lmsTabBar() {
    const tabs = [['chapters', '任务点'], ['quiz', '随堂测验'], ['discuss', '讨论区'], ['signin', '签到'], ['notice', '通知'], ['data', '学情']];
    return '<div class="flex flex-wrap gap-1.5">' + tabs.map(function (tab) {
        const active = LMS.tab === tab[0];
        return '<button onclick="lmsTab(\'' + tab[0] + '\')" class="px-3 py-1.5 rounded-lg text-xs font-medium '
            + (active ? 'bg-[#2d8cf0] text-white' : 'bg-white border border-gray-200 text-gray-600 hover:bg-gray-50') + '">'
            + tab[1] + '</button>';
    }).join('') + '</div>';
}

async function lmsTab(name) {
    LMS.tab = name;
    const root = lmsRoot();
    if (!root) return;
    root.innerHTML = lmsCourseHeader() + '<div class="mt-3">' + lmsTabBar() + '</div>'
        + '<div id="lms-body" class="mt-3"><div class="text-xs text-gray-400 py-6 text-center"><i class="ph ph-circle-notch animate-spin mr-1"></i>加载中…</div></div>';
    if (name === 'chapters') return renderChapters();
    if (name === 'quiz') return lmsLoadQuizzes();
    if (name === 'discuss') return lmsLoadDiscussions();
    if (name === 'signin') return lmsLoadSignins();
    if (name === 'notice') return lmsLoadNotices();
    if (name === 'data') return lmsLoadDashboard();
}

function lmsBody(html) {
    const body = document.getElementById('lms-body');
    if (body) body.innerHTML = html;
}

function lmsBack() { LMS.detail = null; LMS.courseId = null; loadCoursePage(); }

// ---------- 任务点 ----------
function renderChapters() {
    const chapters = (LMS.detail && LMS.detail.chapters) || [];
    if (!chapters.length) return lmsBody(lmsCard('<div class="p-6 text-sm text-gray-400">这门课还没有章节，请教师先导入课程内容。</div>'));
    const rows = chapters.map(function (chapter) {
        const progress = chapter.progress || {};
        const done = progress.status === 'done';
        const statusPill = done ? lmsPill('已完成', 'bg-emerald-50 text-emerald-700')
            : (progress.status === 'doing' ? lmsPill('学习中', 'bg-blue-50 text-blue-700') : lmsPill('未开始', 'bg-gray-100 text-gray-500'));
        return '<div class="border border-gray-100 rounded-lg p-3 hover:border-gray-200 transition-colors">'
            + '<div class="flex items-start gap-3">'
            + '<div class="w-8 h-8 rounded-lg ' + (done ? 'bg-emerald-50 text-emerald-600' : 'bg-gray-50 text-gray-400') + ' flex items-center justify-center text-xs font-bold">' + (chapter.order_index || '-') + '</div>'
            + '<div class="flex-1 min-w-0">'
            + '<div class="flex flex-wrap items-center gap-2"><span class="font-medium text-sm text-gray-900">' + esc(chapter.title) + '</span>'
            + (chapter.is_task_point ? lmsPill('任务点', 'bg-amber-50 text-amber-700') : '') + statusPill
            + '<span class="text-[11px] text-gray-400">约 ' + chapter.est_minutes + ' 分钟' + (progress.minutes ? ' · 已学 ' + progress.minutes + ' 分钟' : '') + '</span></div>'
            + '<div class="text-xs text-gray-500 mt-1">' + esc(chapter.summary || '') + '</div>'
            + (chapter.key_points && chapter.key_points.length ? '<div class="flex flex-wrap gap-1 mt-1.5">' + chapter.key_points.map(k => lmsPill(k, 'bg-gray-50 text-gray-500')).join('') + '</div>' : '')
            + '</div>'
            + '<div class="flex flex-col gap-1.5 flex-shrink-0">'
            + '<button onclick="lmsIntro(' + chapter.id + ')" class="px-3 py-1.5 text-[11px] border border-[#2d8cf0] text-[#2d8cf0] rounded-lg hover:bg-blue-50"><i class="ph ph-sparkle mr-1"></i>AI 导读</button>'
            + (done
                ? '<button onclick="lmsProgress(' + chapter.id + ',\'todo\')" class="px-3 py-1.5 text-[11px] border border-gray-200 text-gray-400 rounded-lg">撤销完成</button>'
                : '<button onclick="lmsProgress(' + chapter.id + ',\'done\')" class="px-3 py-1.5 text-[11px] bg-emerald-500 text-white rounded-lg hover:bg-emerald-600">标记完成</button>')
            + '<button onclick="lmsGenerateQuiz(' + chapter.id + ')" class="px-3 py-1.5 text-[11px] border border-gray-200 text-gray-500 rounded-lg hover:bg-gray-50">出题自测</button>'
            + '</div></div></div>';
    }).join('');
    lmsBody(lmsCard('<div class="p-4"><div class="text-xs text-gray-400 mb-3">学习通式「任务点」：完成状态与学习时长都入库，教师端可看全班进度。</div><div class="space-y-2">' + rows + '</div></div>'));
}

async function lmsProgress(chapterId, status) {
    try {
        const data = await apiSend('/lms/chapters/' + chapterId + '/progress', { status: status });
        if (LMS.detail) LMS.detail.course = Object.assign(LMS.detail.course, data.stats || {});
        showToast(status === 'done' ? '已标记完成' : '已撤销', 'success');
        renderChapters();
    } catch (error) { showToast(error.message, 'error'); }
}

async function lmsIntro(chapterId) {
    const body = document.getElementById('lms-body');
    lmsBody(lmsCard('<div class="p-6 text-sm text-gray-500"><i class="ph ph-circle-notch animate-spin mr-2"></i>本机模型正在读教材、写导读（8B 约 30–50 秒）…</div>'));
    try {
        const data = await apiSend('/lms/chapters/' + chapterId + '/intro', {});
        const meta = data.meta || {};
        const html = '<div class="p-4">'
            + '<div class="flex flex-wrap items-center gap-2 mb-3">' + lmsAiBadge(meta)
            + (data.cached ? lmsPill('缓存的导读', 'bg-gray-50 text-gray-500') : '')
            + '<button onclick="LMS.tab=\'chapters\';lmsTab(\'chapters\')" class="ml-auto text-[11px] text-gray-400 hover:text-gray-600">返回任务点</button></div>'
            + '<div class="prose prose-sm max-w-none text-sm text-gray-700">' + md(data.content || '') + '</div>'
            + (meta.citations && meta.citations.length ? '<div class="mt-3 flex flex-wrap gap-1">' + lmsCiteChips(meta.citations) + '</div>' : '')
            + '<div class="mt-3 text-[11px] text-gray-400">导读由本机视觉大模型依据学科知识库生成，结论均标注教材出处，可点开核对。</div>'
            + '</div>';
        lmsBody(lmsCard(html));
    } catch (error) {
        lmsBody(lmsCard('<div class="p-6 text-sm text-red-600">导读生成失败：' + esc(error.message)
            + '<button onclick="renderChapters()" class="ml-2 text-[#2d8cf0] underline">返回</button></div>'));
    }
}

// ---------- 测验 ----------
async function lmsLoadQuizzes() {
    try {
        const data = await apiGet('/lms/courses/' + LMS.courseId + '/quizzes');
        LMS.quizzes = data.items || [];
        const list = LMS.quizzes.map(function (quiz) {
            const attempt = quiz.my_attempt;
            return '<div class="flex items-center gap-3 border border-gray-100 rounded-lg p-3">'
                + '<div class="flex-1 min-w-0"><div class="text-sm font-medium text-gray-900">' + esc(quiz.title) + '</div>'
                + '<div class="text-[11px] text-gray-400 mt-0.5">' + quiz.count + ' 题 · ' + esc(quiz.created_at)
                + (quiz.ai_meta && quiz.ai_meta.model ? ' · AI 出题 ' + esc(quiz.ai_meta.model) : ' · 教师预置') + '</div></div>'
                + (attempt ? lmsPill('已作答 ' + attempt.score + ' 分', attempt.score >= 60 ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-600')
                    : lmsPill('未作答', 'bg-amber-50 text-amber-700'))
                + '<button onclick="lmsOpenQuiz(' + quiz.id + ')" class="px-3 py-1.5 text-[11px] bg-[#2d8cf0] text-white rounded-lg hover:bg-[#2080e0]">'
                + (attempt ? '再做一次' : '开始作答') + '</button></div>';
        }).join('') || '<div class="p-4 text-sm text-gray-400">还没有测验，可在「任务点」里点「出题自测」让 AI 现场出题。</div>';
        lmsBody(lmsCard('<div class="p-4"><div class="text-xs text-gray-400 mb-3">随堂测验：AI 按章节出题，提交后自动评分并给错题解析。</div><div class="space-y-2">' + list + '</div></div>'));
    } catch (error) { lmsBody(lmsCard('<div class="p-4 text-sm text-red-600">' + esc(error.message) + '</div>')); }
}

async function lmsGenerateQuiz(chapterId) {
    lmsBody(lmsCard('<div class="p-6 text-sm text-gray-500"><i class="ph ph-circle-notch animate-spin mr-2"></i>本机模型正在按章节出题（约 30–50 秒）…</div>'));
    try {
        const quiz = await apiSend('/lms/chapters/' + chapterId + '/quiz', { count: 5 });
        showToast('已生成 ' + quiz.count + ' 道题', 'success');
        LMS.tab = 'quiz';
        await lmsTab('quiz');
        lmsOpenQuiz(quiz.id);
    } catch (error) {
        showToast(error.message, 'error');
        renderChapters();
    }
}

async function lmsOpenQuiz(quizId) {
    try {
        const quiz = await apiGet('/lms/quizzes/' + quizId);
        LMS.quiz = quiz;
        const questions = quiz.questions || [];
        const form = questions.map(function (question, index) {
            const options = question.options.map(function (option, oi) {
                return '<label class="flex items-start gap-2 px-3 py-2 rounded-lg border border-gray-100 hover:bg-gray-50 cursor-pointer text-sm">'
                    + '<input type="radio" name="q' + index + '" value="' + oi + '" class="mt-1">'
                    + '<span class="text-gray-700">' + esc(option) + '</span></label>';
            }).join('');
            return '<div class="border border-gray-100 rounded-lg p-3"><div class="text-sm font-medium text-gray-900 mb-2">'
                + (index + 1) + '. ' + esc(question.stem) + '</div><div class="space-y-1.5">' + options + '</div></div>';
        }).join('');
        const html = '<div class="p-4"><div class="flex items-center gap-2 mb-3"><span class="font-semibold text-sm">' + esc(quiz.title) + '</span>'
            + lmsAiBadge(quiz.ai_meta) + '<button onclick="LMS.tab=\'quiz\';lmsTab(\'quiz\')" class="ml-auto text-[11px] text-gray-400">返回列表</button></div>'
            + '<div class="space-y-2">' + form + '</div>'
            + '<div class="mt-3 flex gap-2"><button onclick="lmsSubmitQuiz()" class="px-4 py-2 bg-[#2d8cf0] text-white rounded-lg text-xs">提交并评分</button>'
            + '<span class="text-[11px] text-gray-400 self-center">提交后立刻给出分数与逐题解析</span></div>'
            + '<div id="lms-quiz-result" class="mt-3"></div></div>';
        lmsBody(lmsCard(html));
    } catch (error) { lmsBody(lmsCard('<div class="p-4 text-sm text-red-600">' + esc(error.message) + '</div>')); }
}

async function lmsSubmitQuiz() {
    const quiz = LMS.quiz;
    if (!quiz) return;
    const answers = [];
    for (let i = 0; i < quiz.count; i += 1) {
        const picked = document.querySelector('input[name="q' + i + '"]:checked');
        if (!picked) return showToast('第 ' + (i + 1) + ' 题还没选', 'warning');
        answers.push(parseInt(picked.value, 10));
    }
    try {
        const data = await apiSend('/lms/quizzes/' + quiz.id + '/submit', { answers: answers });
        const attempt = data.attempt || {};
        const wrong = data.wrong || [];
        const detail = (attempt.detail || []).map(function (item) {
            const mark = item.correct ? '<span class="text-emerald-600">✓</span>' : '<span class="text-red-500">✗</span>';
            return '<div class="flex items-start gap-2 text-xs py-1 border-b border-gray-50">' + mark
                + '<span class="text-gray-600">第 ' + (item.index + 1) + ' 题' + (item.correct ? '答对' : '你选 ' + (item.chosen + 1) + '，正确 ' + (item.answer + 1))
                + (item.explanation ? ' — ' + esc(item.explanation) : '') + '</span></div>';
        }).join('');
        const result = '<div class="border border-gray-100 rounded-lg p-3 bg-gray-50">'
            + '<div class="flex items-center gap-2 mb-2"><span class="text-lg font-bold ' + (attempt.score >= 60 ? 'text-emerald-600' : 'text-red-500') + '">' + attempt.score + ' 分</span>'
            + '<span class="text-xs text-gray-500">正确 ' + attempt.correct + '/' + attempt.total + ' · 错 ' + wrong.length + ' 题</span></div>'
            + detail
            + '<div class="text-[11px] text-gray-500 mt-2">' + esc(data.advice || '') + '</div></div>';
        const target = document.getElementById('lms-quiz-result');
        if (target) target.innerHTML = result;
    } catch (error) { showToast(error.message, 'error'); }
}

// ---------- 讨论区 ----------
async function lmsLoadDiscussions() {
    try {
        const data = await apiGet('/lms/courses/' + LMS.courseId + '/discussions');
        LMS.posts = data.items || [];
        const list = LMS.posts.map(function (post) {
            return '<button onclick="lmsOpenPost(' + post.id + ')" class="w-full text-left border border-gray-100 rounded-lg p-3 hover:border-[#2d8cf0] transition-colors">'
                + '<div class="flex items-center gap-2"><span class="text-sm font-medium text-gray-900">' + esc(post.title) + '</span>'
                + (post.pinned ? lmsPill('置顶', 'bg-amber-50 text-amber-700') : '') + '</div>'
                + '<div class="text-xs text-gray-500 mt-1 line-clamp-2">' + esc((post.content || '').slice(0, 120)) + '</div>'
                + '<div class="text-[11px] text-gray-400 mt-1">' + esc(post.author) + ' · ' + esc(post.created_at) + ' · ' + post.reply_count + ' 条回复</div>'
                + '</button>';
        }).join('') || '<div class="p-4 text-sm text-gray-400">还没有讨论，发第一帖试试，AI 助教会自动回帖。</div>';
        const form = '<div class="border border-gray-100 rounded-lg p-3 space-y-2">'
            + '<input id="lms-post-title" class="auth-input" placeholder="标题，例如：为什么 Dijkstra 不能有负权边？">'
            + '<textarea id="lms-post-content" class="auth-input" rows="2" placeholder="补充你的疑问，AI 助教会结合教材回答并标注出处"></textarea>'
            + '<div class="flex justify-end"><button onclick="lmsPost()" class="px-4 py-2 bg-[#2d8cf0] text-white rounded-lg text-xs"><i class="ph ph-paper-plane-tilt mr-1"></i>发帖</button></div></div>';
        lmsBody(lmsCard('<div class="p-4 space-y-3"><div class="text-xs text-gray-400">讨论区：发帖后本机模型自动以「AI 助教」身份回帖，结论带 [编号] 教材引用。</div>'
            + form + '<div class="space-y-2">' + list + '</div></div>'));
    } catch (error) { lmsBody(lmsCard('<div class="p-4 text-sm text-red-600">' + esc(error.message) + '</div>')); }
}

async function lmsPost() {
    const title = (document.getElementById('lms-post-title') || {}).value || '';
    const content = (document.getElementById('lms-post-content') || {}).value || '';
    if (!title.trim()) return showToast('标题不能为空', 'warning');
    try {
        const data = await apiSend('/lms/courses/' + LMS.courseId + '/discussions', { title: title.trim(), content: content.trim() });
        showToast(data.ai_pending ? '已发布，AI 助教正在查看' : '已发布', 'success');
        lmsOpenPost(data.post.id);
    } catch (error) { showToast(error.message, 'error'); }
}

let lmsPollTimer = null;

async function lmsOpenPost(postId) {
    if (lmsPollTimer) { clearTimeout(lmsPollTimer); lmsPollTimer = null; }
    try {
        const post = await apiGet('/lms/posts/' + postId);
        const replies = post.replies || [];
        const aiPending = !replies.some(r => r.is_ai) && (post.reply_count || 0) === 0;
        const replyHtml = replies.map(function (reply) {
            const ai = reply.is_ai;
            return '<div class="rounded-lg p-3 ' + (ai ? 'bg-violet-50 border border-violet-100' : 'bg-gray-50') + '">'
                + '<div class="flex items-center gap-2 mb-1"><span class="text-xs font-medium ' + (ai ? 'text-violet-700' : 'text-gray-700') + '">'
                + (ai ? 'AI 助教' : esc(reply.author || '同学')) + '</span>'
                + (ai ? '<span class="text-[10px] text-violet-500">' + esc(reply.ai_model || '') + '</span>' : '')
                + '<span class="text-[11px] text-gray-400 ml-auto">' + esc(reply.created_at) + '</span></div>'
                + '<div class="text-sm text-gray-700">' + md(reply.content) + '</div>'
                + (ai && reply.citations && reply.citations.length ? '<div class="mt-2 flex flex-wrap gap-1">' + lmsCiteChips(reply.citations) + '</div>' : '')
                + '</div>';
        }).join('');
        const html = '<div class="p-4 space-y-3">'
            + '<div class="flex items-center gap-2"><span class="font-semibold text-sm">' + esc(post.title) + '</span>'
            + '<button onclick="LMS.tab=\'discuss\';lmsTab(\'discuss\')" class="ml-auto text-[11px] text-gray-400">返回讨论区</button></div>'
            + '<div class="text-sm text-gray-600">' + esc(post.content) + '</div>'
            + '<div class="text-[11px] text-gray-400">' + esc(post.author) + ' · ' + esc(post.created_at) + '</div>'
            + (aiPending ? '<div class="text-xs text-violet-600 bg-violet-50 rounded-lg px-3 py-2"><i class="ph ph-circle-notch animate-spin mr-1"></i>AI 助教正在读教材并组织回答（本机 8B 约 30–45 秒）…</div>' : '')
            + '<div class="space-y-2">' + replyHtml + '</div>'
            + '<div class="flex gap-2"><input id="lms-reply-text" class="auth-input" placeholder="补充你的想法…">'
            + '<button onclick="lmsReply(' + post.id + ')" class="px-4 py-2 bg-[#2d8cf0] text-white rounded-lg text-xs flex-shrink-0">回复</button></div>'
            + '</div>';
        lmsBody(lmsCard(html));
        if (aiPending) lmsPollTimer = setTimeout(function () { lmsOpenPost(postId); }, 6000);
    } catch (error) { lmsBody(lmsCard('<div class="p-4 text-sm text-red-600">' + esc(error.message) + '</div>')); }
}

async function lmsReply(postId) {
    const text = (document.getElementById('lms-reply-text') || {}).value || '';
    if (!text.trim()) return showToast('回复内容不能为空', 'warning');
    try {
        await apiSend('/lms/posts/' + postId + '/reply', { content: text.trim() });
        lmsOpenPost(postId);
    } catch (error) { showToast(error.message, 'error'); }
}

// ---------- 签到 ----------
async function lmsLoadSignins() {
    try {
        const data = await apiGet('/lms/courses/' + LMS.courseId + '/signins');
        const teacher = data.teacher_view;
        const list = (data.items || []).map(function (signin) {
            const closed = signin.closed || signin.expired;
            return '<div class="flex flex-wrap items-center gap-2 border border-gray-100 rounded-lg p-3">'
                + '<div class="flex-1 min-w-0"><div class="text-sm font-medium text-gray-900">' + esc(signin.title) + '</div>'
                + '<div class="text-[11px] text-gray-400 mt-0.5">' + esc(signin.created_at) + ' · 有效期 ' + signin.expire_minutes + ' 分钟'
                + (teacher ? ' · 已签 ' + (signin.checked || 0) + '/' + (signin.total || 0) : '') + '</div></div>'
                + lmsPill(closed ? '已结束' : '进行中', closed ? 'bg-gray-50 text-gray-500' : 'bg-emerald-50 text-emerald-700')
                + (signin.mine ? lmsPill('我已签到', 'bg-blue-50 text-blue-700') : '')
                + (teacher && !closed ? '<button onclick="lmsCloseSignin(' + signin.id + ')" class="px-3 py-1.5 text-[11px] border border-gray-200 rounded-lg text-gray-500">结束</button>' : '')
                + (!teacher && !signin.mine && !closed ? '<button onclick="lmsCheckin(' + signin.id + ')" class="px-3 py-1.5 text-[11px] bg-emerald-500 text-white rounded-lg">签到</button>' : '')
                + '</div>';
        }).join('') || '<div class="p-4 text-sm text-gray-400">还没有签到记录。</div>';
        const actions = teacher
            ? '<div class="flex gap-2"><input id="lms-signin-title" class="auth-input" placeholder="签到标题，例如 第 7 次课">'
              + '<button onclick="lmsCreateSignin()" class="px-4 py-2 bg-[#2d8cf0] text-white rounded-lg text-xs flex-shrink-0">发起签到</button></div>'
            : '<div class="text-[11px] text-gray-400">老师发起签到后，用黑板上的 4 位签到码在这里签到。</div>';
        lmsBody(lmsCard('<div class="p-4 space-y-3">' + actions + '<div class="space-y-2">' + list + '</div></div>'));
    } catch (error) { lmsBody(lmsCard('<div class="p-4 text-sm text-red-600">' + esc(error.message) + '</div>')); }
}

async function lmsCreateSignin() {
    const title = (document.getElementById('lms-signin-title') || {}).value || '课堂签到';
    try {
        const signin = await apiSend('/lms/courses/' + LMS.courseId + '/signin', { title: title, expire_minutes: 10 });
        showToast('签到已发起，签到码 ' + signin.code, 'success');
        LMS.tab = 'signin';
        await lmsTab('signin');
        lmsBody(lmsCard('<div class="p-6 text-center"><div class="text-xs text-gray-400 mb-2">请把签到码写在黑板上</div>'
            + '<div class="text-5xl font-black tracking-widest text-[#2d8cf0]">' + esc(signin.code) + '</div>'
            + '<div class="text-xs text-gray-400 mt-2">有效期 10 分钟 · 学生端「签到」输入即可</div>'
            + '<button onclick="lmsTab(\'signin\')" class="mt-4 text-[11px] text-gray-400 underline">回到签到列表</button></div>'));
    } catch (error) { showToast(error.message, 'error'); }
}

async function lmsCheckin(signinId) {
    const code = window.prompt('请输入 4 位签到码');
    if (!code) return;
    try {
        const data = await apiSend('/lms/signins/' + signinId + '/checkin', { code: code.trim() });
        showToast(data.already ? '你已经签过了' : '签到成功', 'success');
        lmsTab('signin');
    } catch (error) { showToast(error.message, 'error'); }
}

async function lmsCloseSignin(signinId) {
    try {
        await apiSend('/lms/signins/' + signinId + '/close', {});
        showToast('签到已结束', 'success');
        lmsTab('signin');
    } catch (error) { showToast(error.message, 'error'); }
}

// ---------- 通知 ----------
async function lmsLoadNotices() {
    try {
        const data = await apiGet('/lms/courses/' + LMS.courseId + '/notices');
        const teacher = data.teacher_view;
        const list = (data.items || []).map(function (notice) {
            return '<div class="border border-gray-100 rounded-lg p-3 ' + (notice.read ? '' : 'bg-amber-50/40') + '">'
                + '<div class="flex items-center gap-2"><span class="text-sm font-medium text-gray-900">' + esc(notice.title) + '</span>'
                + (notice.pinned ? lmsPill('置顶', 'bg-amber-50 text-amber-700') : '')
                + (notice.read ? '' : lmsPill('未读', 'bg-red-50 text-red-600'))
                + '<span class="text-[11px] text-gray-400 ml-auto">' + esc(notice.created_at) + '</span></div>'
                + '<div class="text-xs text-gray-600 mt-1">' + esc(notice.content) + '</div>'
                + (notice.read ? '' : '<button onclick="lmsReadNotice(' + notice.id + ')" class="mt-2 text-[11px] text-[#2d8cf0]">标记为已读</button>')
                + '</div>';
        }).join('') || '<div class="p-4 text-sm text-gray-400">还没有通知。</div>';
        const form = teacher
            ? '<div class="border border-gray-100 rounded-lg p-3 space-y-2"><input id="lms-notice-title" class="auth-input" placeholder="通知标题">'
              + '<textarea id="lms-notice-content" class="auth-input" rows="2" placeholder="通知内容"></textarea>'
              + '<div class="flex justify-end"><button onclick="lmsCreateNotice()" class="px-4 py-2 bg-[#2d8cf0] text-white rounded-lg text-xs">发布通知</button></div></div>'
            : '';
        lmsBody(lmsCard('<div class="p-4 space-y-3">' + form + '<div class="space-y-2">' + list + '</div></div>'));
    } catch (error) { lmsBody(lmsCard('<div class="p-4 text-sm text-red-600">' + esc(error.message) + '</div>')); }
}

async function lmsCreateNotice() {
    const title = (document.getElementById('lms-notice-title') || {}).value || '';
    const content = (document.getElementById('lms-notice-content') || {}).value || '';
    if (!title.trim()) return showToast('通知标题不能为空', 'warning');
    try {
        await apiSend('/lms/courses/' + LMS.courseId + '/notices', { title: title.trim(), content: content.trim() });
        showToast('通知已发布', 'success');
        lmsTab('notice');
    } catch (error) { showToast(error.message, 'error'); }
}

async function lmsReadNotice(noticeId) {
    try {
        await apiSend('/lms/notices/' + noticeId + '/read', {});
        lmsTab('notice');
    } catch (error) { showToast(error.message, 'error'); }
}

// ---------- 学情 ----------
async function lmsLoadDashboard() {
    try {
        const data = await apiGet('/lms/courses/' + LMS.courseId + '/dashboard');
        LMS.dashboard = data;
        const mine = data.mine || {};
        const klass = data.class;
        let html = '<div class="grid grid-cols-2 md:grid-cols-4 gap-3">';
        if (klass) {
            // 教师看到的是班级口径；学生看到的是自己的口径——两套数字不混用
            html += lmsCard('<div class="p-3"><div class="text-[11px] text-gray-400">班级人数</div><div class="text-xl font-bold text-gray-900 mt-1">' + klass.student_count + '</div><div class="text-[11px] text-gray-400">已选课</div></div>');
            html += lmsCard('<div class="p-3"><div class="text-[11px] text-gray-400">任务点平均完成</div><div class="text-xl font-bold text-gray-900 mt-1">' + lmsPercent(klass.avg_task_rate) + '</div><div class="text-[11px] text-gray-400">共 ' + mine.task_total + ' 个任务点</div></div>');
            html += lmsCard('<div class="p-3"><div class="text-[11px] text-gray-400">测验平均正确率</div><div class="text-xl font-bold text-gray-900 mt-1">' + lmsPercent(klass.avg_quiz_rate) + '</div><div class="text-[11px] text-gray-400">全班作答</div></div>');
            html += lmsCard('<div class="p-3"><div class="text-[11px] text-gray-400">平均出勤率</div><div class="text-xl font-bold text-gray-900 mt-1">' + lmsPercent(klass.avg_signin_rate) + '</div><div class="text-[11px] text-gray-400">' + mine.signin_total + ' 次签到</div></div>');
        } else {
            html += lmsCard('<div class="p-3"><div class="text-[11px] text-gray-400">任务点完成</div><div class="text-xl font-bold text-gray-900 mt-1">' + mine.task_done + '/' + mine.task_total + '</div><div class="text-[11px] text-gray-400">' + lmsPercent(mine.task_rate) + '</div></div>');
            html += lmsCard('<div class="p-3"><div class="text-[11px] text-gray-400">测验均分</div><div class="text-xl font-bold text-gray-900 mt-1">' + (mine.quiz_avg === null ? '—' : Math.round(mine.quiz_avg * 100)) + '</div><div class="text-[11px] text-gray-400">' + mine.quiz_count + ' 次作答</div></div>');
            html += lmsCard('<div class="p-3"><div class="text-[11px] text-gray-400">签到</div><div class="text-xl font-bold text-gray-900 mt-1">' + mine.signin_checked + '/' + mine.signin_total + '</div><div class="text-[11px] text-gray-400">' + lmsPercent(mine.signin_rate) + '</div></div>');
            html += lmsCard('<div class="p-3"><div class="text-[11px] text-gray-400">学习时长</div><div class="text-xl font-bold text-gray-900 mt-1">' + mine.minutes + '</div><div class="text-[11px] text-gray-400">分钟（累计）</div></div>');
        }
        html += '</div>';

        if (klass) {
            html += lmsCard('<div class="p-4 mt-3"><div class="flex items-center gap-2 mb-2">'
                + '<span class="text-xs font-semibold text-gray-700">学习预警名单</span>'
                + lmsPill(klass.at_risk_count + ' 人触发阈值', 'bg-red-50 text-red-600')
                + '<span class="text-[11px] text-gray-400">任务点 &lt;60% / 测验 &lt;60% / 出勤 &lt;80% 任一命中</span></div>'
                + ((klass.at_risk || []).length ? '<div class="space-y-1">' + klass.at_risk.map(function (item) {
                    return '<div class="text-xs text-gray-600"><span class="font-medium">' + esc(item.name) + '</span> — ' + esc(item.reasons.join('、')) + '</div>';
                }).join('') + '</div>' : '<div class="text-xs text-gray-400">当前没有学生触发预警阈值。</div>')
                + '</div>', 'mt-3');
        }

        html += lmsCard('<div class="p-4"><div class="flex items-center gap-2 mb-2"><span class="text-xs font-semibold text-gray-700">AI 学情建议</span>'
            + '<span class="text-[11px] text-gray-400">（把上面的真实数字交给本机模型解读）</span>'
            + '<button onclick="lmsAdvice()" class="ml-auto px-3 py-1.5 text-[11px] bg-[#2d8cf0] text-white rounded-lg"><i class="ph ph-sparkle mr-1"></i>生成建议</button></div>'
            + '<div id="lms-advice" class="text-sm text-gray-700">' + (LMS.advice || '<span class="text-gray-400">还没有生成，点右上角按钮（8B 约 30–50 秒）。</span>') + '</div>'
            + '</div>', 'mt-3');
        lmsBody(html);
    } catch (error) { lmsBody(lmsCard('<div class="p-4 text-sm text-red-600">' + esc(error.message) + '</div>')); }
}

async function lmsAdvice() {
    const target = document.getElementById('lms-advice');
    if (target) target.innerHTML = '<span class="text-gray-500"><i class="ph ph-circle-notch animate-spin mr-1"></i>本机模型正在解读学情数据…</span>';
    try {
        const data = await apiSend('/lms/courses/' + LMS.courseId + '/advice', {});
        LMS.advice = '<div class="prose prose-sm max-w-none">' + md(data.content || '') + '</div>' + '<div class="mt-2">' + lmsAiBadge(data.meta) + '</div>';
        if (target) target.innerHTML = LMS.advice;
    } catch (error) {
        if (target) target.innerHTML = '<span class="text-red-600">生成失败：' + esc(error.message) + '</span>';
    }
}