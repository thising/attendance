/* 笃行工作台：服务端是权限、分数及保存结果的唯一依据。名单草稿仅保留在本页内存。 */
(() => {
  'use strict';
  const source = document.getElementById('workspace-data');
  if (!source) return;
  let data;
  try { data = JSON.parse(source.textContent); } catch (_) { return; }
  const page = data.page || document.body.dataset.page;
  const root = document.getElementById('page-content');
  const q = (s, within = document) => within.querySelector(s);
  const qa = (s, within = document) => Array.from(within.querySelectorAll(s));
  const escape = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const urlPart = value => encodeURIComponent(String(value ?? ''));
  const base = (code = data.classroom?.code) => `/classes/${urlPart(code)}/`;
  const termQuery = () => data.term?.key ? `?term=${urlPart(data.term.key)}` : '';
  const owner = () => data.actor?.role === 'owner';
  const writable = () => !data.readonly && ['owner', 'committee'].includes(data.actor?.role);
  const actorContext = () => data.actor?.id !== undefined && data.actor.id !== null ? {actor_context:{role:data.actor.role,id:data.actor.id}} : {};
  const fmt = value => Number.isFinite(Number(value)) && value !== null && value !== '' ? Number(value).toFixed(2) : '—';
  const number = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  const countGroups = [
    {key:'attendance',label:'考勤',fields:['absent','late','leave']},
    {key:'activity',label:'活动',fields:['low','mid','high']},
    {key:'discipline',label:'违纪',fields:['dlow','dmid','dhigh']}
  ];
  const countFields = countGroups.flatMap(group => group.fields);
  const rewardFields = new Set(['low','mid','high']);
  const numberCollator = new Intl.Collator('zh-CN', {numeric:true, sensitivity:'variant'});
  const pinyinCollator = new Intl.Collator('zh-CN-u-co-pinyin', {sensitivity:'variant'});
  const sortModes = [['number-asc','学号 ↑'],['number-desc','学号 ↓'],['pinyin-asc','拼音首字母 A–Z'],['pinyin-desc','拼音首字母 Z–A']];
  function studentSortControl(id, selected = 'number-asc') {
    return `<label class="student-sort-control" for="${escape(id)}">排序<select id="${escape(id)}" aria-label="学生排序方式">${sortModes.map(([value,label]) => `<option value="${value}" ${selected === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label>`;
  }
  function sortedStudents(students, mode = 'number-asc') {
    const byNumber = (a,b) => numberCollator.compare(String(a.number ?? ''), String(b.number ?? '')) || String(a.number ?? '').localeCompare(String(b.number ?? ''));
    const byName = (a,b) => pinyinCollator.compare(String(a.name ?? ''), String(b.name ?? ''));
    const descending = mode.endsWith('-desc') ? -1 : 1;
    return students.slice().sort((a,b) => descending * ((mode.startsWith('pinyin') ? byName(a,b) || byNumber(a,b) : byNumber(a,b)) || number(a.id)-number(b.id)));
  }
  function studentIdentity(student, {href = null, showClass = false, showSex = false} = {}) {
    const name = href ? `<a class="student-name" href="${escape(href)}">${escape(student.name)}</a>` : `<span class="student-name">${escape(student.name)}</span>`;
    const sex = showSex && ['male','female'].includes(student.sex) ? `<svg class="sex-symbol ${student.sex}" viewBox="0 0 24 24" role="img" aria-label="${student.sex === 'male' ? '男' : '女'}"><title>${student.sex === 'male' ? '男' : '女'}</title>${student.sex === 'male' ? '<circle cx="9" cy="15" r="5"/><path d="M12.5 11.5 20 4m-5 0h5v5"/>' : '<circle cx="12" cy="9" r="5"/><path d="M12 14v8m-4-4h8"/>'}</svg>` : '';
    return `<div class="student-identity"><span class="student-name-line">${name}${sex}</span><span class="student-secondary"><span class="student-number">${escape(student.number)}</span>${showClass && student.class_name ? `<span class="student-class">${escape(student.class_name)}</span>` : ''}</span></div>`;
  }
  function monthlyPolicy(policy = data.policy) { return policy?.monthly || {base:'60.00',minimum:'0.00',maximum:null}; }
  function countBadge(value,key) {
    if (value === null || value === undefined) return '<span class="count-mark unavailable">—</span>';
    const n = number(value), rewarding = rewardFields.has(key), zeroWeight = data.policy?.weights && Number(data.policy.weights[key]) === 0;
    return `<span class="count-mark ${n === 0 ? 'zero' : zeroWeight ? 'neutral' : rewarding ? 'reward' : 'deduction'}" title="${escape(labels[key] || '活动')} ${n} 次${zeroWeight ? ' · 当期权重为0，不影响分数' : ''}">${n && !zeroWeight ? icon(rewarding ? 'plus' : 'minus') : ''}<span>${n}</span></span>`;
  }
  function groupedScoreHeader(scoreLabel) {
    return `<thead><tr class="score-group-row"><th scope="col" rowspan="2" class="identity-student">学生</th>${countGroups.map(group => `<th scope="colgroup" colspan="3" class="group-heading group-${group.key}">${group.label}</th>`).join('')}<th scope="col" rowspan="2" class="score-column">${scoreLabel}</th></tr><tr class="score-detail-row">${countGroups.map(group => group.fields.map((field,index) => `<th scope="col" aria-label="${labels[field]}" class="${index === 0 ? 'group-start ' : ''}group-${group.key}">${group.key === 'attendance' ? labels[field] : labels[field].replace(group.label,'')}</th>`).join('')).join('')}</tr></thead>`;
  }
  function groupedCountCells(counts) {
    return countGroups.map(group => group.fields.map((field,index) => `<td class="number count-cell ${index === 0 ? 'group-start ' : ''}group-${group.key}">${countBadge(counts?.[field],field)}</td>`).join('')).join('');
  }
  function scoreBadge(value,baseValue,href = null,places = 2) {
    if (value === null || value === undefined || value === '') return '<span class="score-mark unavailable">—</span>';
    const validBase = baseValue !== null && baseValue !== undefined && baseValue !== '' && Number.isFinite(Number(baseValue));
    const direction = validBase ? (Number(value) < Number(baseValue) ? 'below' : Number(value) > Number(baseValue) ? 'above' : 'equal') : 'unknown';
    const label = {below:'低于基础',above:'高于基础',equal:'等于基础',unknown:'当期分数'}[direction];
    const contents = `<span class="score-value">${number(value).toFixed(places)}</span><span class="score-indicator">${icon(direction === 'below' ? 'minus' : direction === 'above' ? 'plus' : 'check')}${label}</span>`;
    return href ? `<a class="score-mark ${direction}" href="${escape(href)}" title="${validBase ? `当期基础分 ${fmt(baseValue)}` : '查看个人计分依据'}">${contents}</a>` : `<span class="score-mark ${direction}" title="${validBase ? `当期基础分 ${fmt(baseValue)}` : '查看个人计分依据'}">${contents}</span>`;
  }
  function scoreLegend() { return data.historical ? '<p class="score-legend"><span>历史快照 · 灰色只读</span><span>分数及九项次数均按当期规则保存</span></p>' : '<p class="score-legend"><span class="legend-deduction">朱砂：计分扣减的行为次数</span><span class="legend-reward">青绿：计分奖励的活动次数</span><span>灰色：零次或零权重行为；次数仍保留</span></p>'; }
  function policyIdentity(policy = data.policy) {
    if (policy?.version !== undefined && policy.version !== null) return number(policy.version) > 0 ? `版本 ${policy.version}` : '默认规则';
    return number(policy?.revision) === 0 ? '默认规则' : '当期固定规则';
  }
  function policyCaption(policy = data.policy) {
    return `${data.readonly ? '历史规则' : '当前规则'} · ${policyIdentity(policy)}`;
  }
  const kindLabels = {class:'考勤点名', activity:'活动加分', discipline:'违纪扣分'};
  const kindIcons = {class:'clipboard', activity:'award', discipline:'shield'};
  const attendanceChoices = [['normal','到课','user'],['late','迟到','clock'],['absent','缺勤','absent'],['leave','请假','calendar']];
  const topThreeIcons = {...Object.fromEntries(attendanceChoices.filter(([key]) => key !== 'normal').map(([key,,iconName]) => [key,iconName])), activity:kindIcons.activity, discipline:kindIcons.discipline};
  const labels = {normal:'正常', absent:'缺勤', late:'迟到', leave:'请假', low:'班级活动', mid:'院级活动', high:'校级活动', dlow:'轻度违纪', dmid:'中度违纪', dhigh:'严重违纪'};
  const iconPaths = {
    home:'<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1Z"/><path d="M9 21v-8h6v8"/>',
    book:'<path d="M12 5v16M12 5C9 3 5 3 2 4v16c3-1 7-1 10 1 3-2 7-2 10-1V4c-3-1-7-1-10 1Z"/>',
    clipboard:'<rect x="5" y="4" width="14" height="18" rx="2"/><rect x="9" y="2" width="6" height="4" rx="1"/><path d="m9 14 2 2 4-4"/>',
    users:'<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M22 21v-2a4 4 0 0 0-3-3.9M16 3a4 4 0 0 1 0 8"/><circle cx="9" cy="7" r="4"/>',
    user:'<circle cx="9" cy="7" r="4"/><path d="M2 21v-2a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v2m1-11 2 2 4-4"/>',
    absent:'<circle cx="9" cy="7" r="4"/><path d="M2 21v-2a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v2m3-13 4 4m0-4-4 4"/>',
    history:'<path d="M3 3v5h5M3.5 8A9 9 0 1 1 3 15M12 7v5l3 2"/>',
    sliders:'<path d="M4 6h5m4 0h7M4 12h9m4 0h3M4 18h2m4 0h10M9 3v6m4 0v6M6 15v6"/>',
    logout:'<path d="M9 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h4m6-14 5 5-5 5m-8-5h13"/>',
    login:'<path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4M9 7l5 5-5 5M3 12h11"/>',
    lock:'<rect x="4" y="10" width="16" height="12" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4M12 15v3"/>',
    key:'<circle cx="8" cy="8" r="5"/><path d="m12 12 9 9m-5-5 3-3m-6 0 3-3"/>',
    plus:'<path d="M12 5v14M5 12h14"/>',
    save:'<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h12l4 4v12a2 2 0 0 1-2 2Z"/><path d="M7 3v6h10V3M7 21v-8h10v8"/>',
    edit:'<path d="m16 3 5 5M3 21l5-1L21 7a3.5 3.5 0 0 0-5-5L3 15Z"/>',
    calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18M8 15h2m4 0h2m-8 3h2"/>',
    check:'<path d="m5 12 4 4L19 6"/>',
    clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    award:'<circle cx="12" cy="8" r="6"/><path d="m8 13-1 9 5-3 5 3-1-9"/>',
    shield:'<path d="m12 2 9 4v6c0 5-6 9-9 10-3-1-9-5-9-10V6Z"/><path d="M12 7v6m0 4h.01"/>',
    warning:'<path d="m12 3 10 18H2Z"/><path d="M12 9v5m0 3h.01"/>',
    undo:'<path d="M3 3v6h6M3 9c3-7 16-6 16 3 0 5-4 8-9 8"/>',
    eye:'<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    search:'<circle cx="10" cy="10" r="7"/><path d="m15 15 6 6"/>',
    trash:'<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
    minus:'<path d="M5 12h14"/>',
    arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>',
    close:'<path d="m6 6 12 12M6 18 18 6"/>',
    refresh:'<path d="M21 3v6h-6M3 21v-6h6M3.5 9a9 9 0 0 1 15-4L21 9M3 15l2.5 4a9 9 0 0 0 15-4"/>',
    download:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
    upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/>',
    copy:'<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>'
  };
  const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${iconPaths[name] || iconPaths.book}</svg>`;
  const hydrateIcons = (within = document) => qa('[data-icon]', within).forEach(el => { el.innerHTML = icon(el.dataset.icon); el.setAttribute('aria-hidden', 'true'); });
  const button = (label, iconName, attributes = '', className = '') => `<button class="button ${className}" ${attributes}>${icon(iconName)}${escape(label)}</button>`;
  const link = (label, iconName, href, className = '') => `<a class="button ${className}" href="${escape(href)}">${icon(iconName)}${escape(label)}</a>`;
  const empty = (title, message = '', iconName = 'book') => `<div class="empty">${icon(iconName)}<strong>${escape(title)}</strong><p>${escape(message)}</p></div>`;
  function heading(title, subtitle, iconName, actions = '', eyebrow = '日常 · 操行管理') {
    return `<div class="heading"><div><div class="eyebrow">${escape(eyebrow)}</div><div class="title-line">${icon(iconName)}<h1>${escape(title)}</h1></div><p>${escape(subtitle)}</p></div><div class="actions">${actions}</div></div>`;
  }
  function showMessage(selector, message, error = false) {
    const el = q(selector);
    if (el) { el.textContent = message; el.dataset.error = String(error); }
  }
  let toastTimer;
  function toast(message) {
    const el = q('#app-toast'); el.textContent = message; el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, 5500);
  }
  function uuid() {
    if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
    const values = new Uint8Array(16); crypto.getRandomValues(values);
    values[6] = (values[6] & 15) | 64; values[8] = (values[8] & 63) | 128;
    const h = Array.from(values, n => n.toString(16).padStart(2, '0')).join('');
    return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
  }
  const csrf = () => {
    const cookie = document.cookie.split(';').map(item => item.trim()).find(item => item.startsWith('csrftoken='));
    return cookie ? decodeURIComponent(cookie.slice('csrftoken='.length)) : q('#csrf-source input')?.value || '';
  };
  class ApiError extends Error {
    constructor(message, status = 0, code = 'network_uncertain', details = null) { super(message); this.status = status; this.code = code; this.details = details; }
  }
  async function request(url, body, method = 'POST') {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 25000);
    try {
      const isMultipart = body instanceof FormData;
      const headers = method === 'GET' ? {'Accept':'application/json'} : {...(isMultipart ? {} : {'Content-Type':'application/json'}), 'Accept':'application/json', 'X-CSRFToken':csrf()};
      const response = await fetch(url, {method, credentials:'same-origin', headers, ...(method === 'GET' ? {} : {body:isMultipart ? body : JSON.stringify(body)}), signal:controller.signal});
      let result;
      try { result = await response.json(); } catch (_) {
        if (!response.ok && response.status < 500) throw new ApiError(response.status === 403 ? '请求未通过权限或安全校验。输入已保留，请恢复授权后重试。' : '当前请求未被接受，输入已保留。', response.status, 'request_rejected');
        throw new ApiError('未获得可确认的服务器回执。输入已保留，请使用原提交重试。', 0);
      }
      if (!response.ok || result.ok !== true) throw new ApiError(result.error?.message || '操作未完成，请稍后重试。', response.status, result.error?.code || 'request_failed', result.error?.details);
      return result.data || {};
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError('网络响应不确定，尚不能确认是否保存。输入已保留，请重试原提交。');
    } finally { clearTimeout(timer); }
  }
  let reauthCode = null, reauthActor = null;
  function openAuth(code = data.classroom?.code) {
    reauthCode = code;
    const dialog = q('#auth-dialog'); q('#auth-message').textContent = '';
    reauthActor = {...data.actor};
    q('#reauth-form').elements.username.value = reauthActor.username || '';
    q('#reauth-form').elements.password.value = '';
    let ownerNotice = q('#owner-reauth',dialog);
    if (!ownerNotice) { ownerNotice = document.createElement('div'); ownerNotice.id = 'owner-reauth'; dialog.prepend(ownerNotice); }
    ownerNotice.hidden = !owner(); q('#reauth-form').hidden = owner();
    if (owner()) ownerNotice.innerHTML = `<div class="section-title">${icon('login')}<h2>重新登录，保留当前输入</h2></div><p>班主任登录已失效。请在新页面完成登录，再返回这里重试；当前草稿仍保留。</p><div class="actions"><a class="button primary" href="/login/" target="_blank" rel="noopener">${icon('login')}在新页面登录</a>${button('返回草稿','undo','id="owner-auth-close"')}</div>`;
    q('#owner-auth-close')?.addEventListener('click',() => dialog.close());
    if (!dialog.open) dialog.showModal();
    if (!owner()) q('#reauth-form').elements.password.focus();
  }
  function explain(error, selector, code) {
    const row = error.details?.row ?? error.details?.line;
    const position = `${row ? `第 ${row} 行` : ''}${error.details?.column ? `（${error.details.column}列）` : ''}`;
    const message = `${position ? position+'：' : ''}${error.message}`;
    if (error.status === 401) { showMessage(selector, '授权已失效，输入已保留。重新授权后可继续。', true); openAuth(code); }
    else if (error.status === 409) showMessage(selector, `${message} 当前输入已保留，请核对冲突原因后再操作。`, true);
    else showMessage(selector, message, true);
  }
  q('#reauth-form').addEventListener('submit', async event => {
    event.preventDefault(); const form = event.currentTarget; const submit = q('[type=submit]', form); submit.disabled = true;
    try {
      if (!reauthActor?.username || form.elements.username.value !== reauthActor.username) throw new ApiError('请使用本页原班委账号恢复授权。',403,'actor_mismatch');
      const result = await request('/login/', {role:'committee',username:reauthActor.username,password:form.elements.password.value,reauthorize:true,class_id:data.classroom?.id});
      const returnedActor = result.actor || result;
      if (returnedActor.username !== reauthActor.username || (reauthActor.id !== undefined && String(returnedActor.id) !== String(reauthActor.id))) throw new ApiError('返回账号与原提交账号不一致，请保留本页输入并联系班主任。',403,'actor_mismatch');
      if (String(data.classroom?.code) === String(reauthCode)) data.actor = {...reauthActor,...returnedActor};
      q('#auth-dialog').close(); form.reset(); toast('班委授权已恢复，固定3小时有效。请核对后继续提交。'); updateExpiry();
    } catch (error) { showMessage('#auth-message', error.message, true); }
    finally { submit.disabled = false; }
  });
  qa('[data-close-dialog]').forEach(el => el.addEventListener('click', () => el.closest('dialog').close()));
  function updateExpiry() {
    if (data.actor?.role !== 'committee') return;
    const expiry = data.actor.expires_at;
    if (!expiry) return;
    const date = new Date(typeof expiry === 'number' ? expiry * 1000 : expiry);
    if (Number.isNaN(date.getTime())) return;
    const expired = date <= new Date();
    q('#identity-caption').textContent = expired ? '班委 · 授权已到期' : `班委 · 授权至 ${date.toLocaleTimeString('zh-CN', {timeZone:'Asia/Shanghai',hour:'2-digit', minute:'2-digit', hour12:false})}`;
    const badge = q('#record-grant-badge'); if (badge) badge.innerHTML = icon(expired ? 'lock' : 'key')+(expired ? '班委授权已到期' : '班委 · 3小时授权');
  }
  function readStudents() { return Array.isArray(data.students) ? sortedStudents(data.students) : []; }
  function recordHref(record) { return `${base()}records/${urlPart(record.id)}/${termQuery()}`; }
  function recordsTable(records, {studentMode = false} = {}) {
    if (!records?.length) return empty(data.records_unavailable ? '历史记录快照待核实' : '还没有业务记录', data.records_unavailable ? '该学期没有可用的记录快照，不能用当前事实推算历史记录。' : data.readonly ? '该学期暂无可展示的记录。' : '从一次点名开始；保存后立即参与计分。', 'clipboard');
    return `<div class="table-wrap"><table class="table"><thead><tr><th>记录与时间</th><th>类型</th><th class="desktop-only">${studentMode ? '本次状态' : '记录人数'}</th><th>查看</th></tr></thead><tbody>${records.map(record => `<tr><td><a class="student-name" href="${recordHref(record)}">${escape(record.name)}</a><span class="student-number">发生 ${escape(record.date)}${record.time ? ` · 录入 ${escape(String(record.time).replace('T',' ').slice(0,16))}` : ''}</span>${studentMode ? `<span class="mobile-record-status">${icon('clipboard')}本次：${escape(labels[record.value] || record.status_label || '—')}</span>` : ''}</td><td>${escape(kindLabels[record.kind] || record.kind)}</td><td class="desktop-only">${studentMode ? escape(labels[record.value] || record.status_label || '—') : escape(record.student_count ?? '—')}</td><td>${link('详情','eye',recordHref(record))}</td></tr>`).join('')}</tbody></table></div>`;
  }
  function pagination() {
    const p = data.pagination;
    if (!p || number(p.pages) < 2) return '';
    const url = new URL(location.href); const at = number(p.page) || 1;
    const prev = new URL(url); prev.searchParams.set('page', String(at-1));
    const next = new URL(url); next.searchParams.set('page', String(at+1));
    return `<div class="table-footer"><span>第 ${at} / ${number(p.pages)} 页</span><div class="actions">${at > 1 ? link('上一页','undo',prev.pathname+prev.search) : ''}${at < number(p.pages) ? link('下一页','arrow',next.pathname+next.search) : ''}</div></div>`;
  }
  function dashboardPage() {
    if (data.actor?.role === 'anonymous' || !data.actor?.role) { loginPage(); return; }
    const classes = (data.classes || []).map(c => ({...c,...(data.class_summaries || []).find(summary => String(summary.id) === String(c.id))}));
    const actions = owner() && !data.readonly ? button('创建班级','plus','id="create-class"','primary') : '';
    root.innerHTML = heading('今日笃行', data.term?.label ? `${data.term.label} · 从清楚的日常记录开始` : '管理日常表现，清楚看见每一步成长', 'home', actions, '工作台 · 班级一览') +
      (classes.length ? `<div class="card-grid">${classes.map(c => `<article class="card"><div class="card-title">${icon('book')}<h2><a href="${base(c.code)}${termQuery()}">${escape(c.name)}</a></h2></div>${c.pending ? '<p>本班数据尚待初始化核实。</p>' : `<div class="class-card-facts"><div><span class="metric-label">学生人数</span><strong>${number(c.student_count)}<small> 人</small></strong></div><div><span class="metric-label">所选学期活动</span><strong>${number(c.activity_count)}<small> 次</small></strong></div></div><p class="last-activity">${icon('clock')}学期内最近活动 ${escape(c.last_activity_at ? displayTime(c.last_activity_at) : '暂无活动')}</p>`}<div class="actions">${link('查看班级','eye',base(c.code)+termQuery())}${writable() && !c.pending && !c.archived ? link('快速录入','clipboard',`${base(c.code)}records/new/`,'primary') : ''}</div></article>`).join('')}</div>` : empty(owner() ? '创建第一个班级' : '暂无可用班级', owner() ? '创建班级后新增学生名单，即可开始使用。' : '请联系班主任确认当前账号的班级权限。')) +
      `<section class="dashboard-overview"><div class="toolbar"><div class="section-title">${icon('users')}<h2>全员学期概况</h2></div><div class="student-list-tools"><label class="visually-hidden" for="dashboard-search">搜索姓名或学号</label><input id="dashboard-search" class="search" type="search" placeholder="搜索姓名或学号"><label class="student-sort-control" for="dashboard-kind-filter">筛选<select id="dashboard-kind-filter" aria-label="按考勤活动违纪非0次数筛选学生"><option value="all">全部学生</option><option value="attendance">考勤非0</option><option value="activity">活动非0</option><option value="discipline">违纪非0</option></select></label>${studentSortControl('dashboard-sort')}${button('导出筛选结果 CSV','download','id="export-dashboard" type="button"')}</div></div><fieldset class="class-filters"><legend>班级范围 · 可多选</legend><div class="filter-actions">${button('全选','check','id="filter-all" type="button"')}${button('清空','minus','id="filter-none" type="button"')}</div><div class="filter-options">${classes.map(c => `<label class="filter-chip"><input type="checkbox" data-filter-class value="${escape(c.id)}" checked><span>${escape(c.name)}</span></label>`).join('')}</div></fieldset><p id="dashboard-count" class="small" role="status" aria-live="polite"></p><div id="dashboard-results"></div><p class="small table-scroll-note">非0按对应类型的学期次数筛选；导出与当前班级、搜索、类型和排序结果一致。横向滚动查看全部项目，点击姓名查看个人计分明细。</p></section>` +
      (data.recent_records?.length ? `<section style="margin-top:26px"><div class="section-title">${icon('history')}<h2>最近记录</h2></div>${recentRecords(data.recent_records)}</section>` : '');
    q('#create-class')?.addEventListener('click', createClass);
    setupDashboardStudents(classes);
  }
  function setupDashboardStudents(classes) {
    const rows = (data.dashboard_students || []).slice();
    let visibleRows = [];
    const render = () => {
      const selected = new Set(qa('[data-filter-class]').filter(el => el.checked).map(el => el.value));
      const search = q('#dashboard-search').value.trim().toLocaleLowerCase();
      const kind = q('#dashboard-kind-filter').value;
      const fields = countGroups.find(group => group.key === kind)?.fields || [];
      const list = sortedStudents(rows.filter(s => selected.has(String(s.class_id)) && `${s.name} ${s.number}`.toLocaleLowerCase().includes(search) && (!fields.length || fields.some(field => number(s.counts?.[field]) > 0))),q('#dashboard-sort').value);
      visibleRows = list;
      q('#export-dashboard').disabled = !list.length;
      q('#dashboard-count').textContent = `已选 ${selected.size} / ${classes.length} 个班级 · 显示 ${list.length} 位学生 · ${data.term?.label || ''}`;
      q('#dashboard-results').innerHTML = list.length ? scoreLegend()+`<div class="table-wrap overview-scroll" tabindex="0" role="region" aria-label="全员学期概况，可横向滚动"><table class="table overview-table">${groupedScoreHeader('学期分数')}<tbody>${list.map(s => `<tr><th scope="row" class="identity-student">${studentIdentity(s,{href:`${base(s.class_code)}students/${urlPart(s.id)}/${termQuery()}`,showClass:true,showSex:true})}</th>${groupedCountCells(s.counts)}<td class="number score-column">${scoreBadge(s.score,s.score_base,`${base(s.class_code)}students/${urlPart(s.id)}/${termQuery()}`,s.average_decimal_places ?? 2)}</td></tr>`).join('')}</tbody></table></div>` : empty(selected.size ? '没有匹配的学生' : '尚未选择班级',selected.size ? '可调整班级、姓名或非0类型筛选。' : '勾选一个或多个班级，或点击全选。','users');
    };
    q('#dashboard-search').addEventListener('input',render);
    q('#dashboard-kind-filter').addEventListener('change',render);
    q('#dashboard-sort').addEventListener('change',render);
    q('#export-dashboard').addEventListener('click',() => {
      if (!visibleRows.length) return;
      const header = ['班级','学号','姓名','性别','缺勤','迟到','请假','班级活动','院级活动','校级活动','轻度违纪','中度违纪','严重违纪','学期分数'];
      const csvCell = value => {
        let cell = String(value ?? '');
        if (/^[\s\uFEFF]*[=+\-@]/u.test(cell)) cell = `'${cell}`;
        return `"${cell.replaceAll('"','""')}"`;
      };
      const content = [header,...visibleRows.map(s => [s.class_name,s.number,s.name,({male:'男',female:'女'})[s.sex] || '',...countFields.map(field => s.counts?.[field] ?? 0),s.score])].map(row => row.map(csvCell).join(',')).join('\r\n');
      const url = URL.createObjectURL(new Blob(['\uFEFF',content],{type:'text/csv;charset=utf-8'}));
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = `笃行-学期汇总-${data.term?.key || 'current'}.csv`; document.body.append(anchor); anchor.click(); anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url),1000);
      toast(`已生成 ${visibleRows.length} 位学生的 CSV。`);
    });
    qa('[data-filter-class]').forEach(el => el.addEventListener('change',render));
    q('#filter-all').addEventListener('click',() => { qa('[data-filter-class]').forEach(el => el.checked = true); render(); });
    q('#filter-none').addEventListener('click',() => { qa('[data-filter-class]').forEach(el => el.checked = false); render(); });
    render();
  }
  function recentRecords(records) {
    return `<ul class="timeline">${records.map(r => `<li><small>${escape(r.date)} · ${escape(r.class_name || '')}</small><p><a href="${base(r.class_code || data.classroom?.code)}records/${urlPart(r.id)}/">${escape(r.name)}</a></p><div class="event-meta">${escape(kindLabels[r.kind] || '')}</div></li>`).join('')}</ul>`;
  }
  function augustPage() {
    root.innerHTML = heading('8 月旧记录', `${data.august_year} 年 8 月 · 仅查看明确保存的记录，不参与学期计分`, 'history', link('返回班级','book',base()+termQuery()), '班级 · 历史记录') +
      `<div class="notice">${icon('lock')}<div><strong>旧记录只读</strong><p>8 月不属于计分学期。没有完整名单快照的旧点名，仅展示当时明确保存的个人条目。</p></div></div><div class="toolbar"><div class="section-title">${icon('clipboard')}<h2>记录列表</h2></div><label>年份<select id="august-year">${(data.august_years || []).map(year => `<option value="${year}" ${year === data.august_year ? 'selected' : ''}>${year} 年 8 月</option>`).join('')}</select></label></div>${recordsTable(data.records)}${pagination()}`;
    q('#august-year').addEventListener('change',event => location.assign(`${base()}august/?year=${urlPart(event.target.value)}`));
  }
  function classPage() {
    const summary = data.summary || {}; const students = readStudents();
    root.innerHTML = heading('班级总览', data.term?.label || '名单、记录与自然月计分', 'book', writable() ? link('新增记录','plus',`${base()}records/new/`,'primary') : '', '班级 · 学期总览') +
      `<div class="metrics"><div><div class="metric-label">学生人数</div><div class="metric-value">${summary.student_count ?? students.length}<small> 人</small></div><div class="metric-note">所选学期名单</div></div><div><div class="metric-label">所选学期活动</div><div class="metric-value">${number(summary.activity_count)}<small> 次</small></div><div class="metric-note">本学期内全部记录类型</div></div><div><div class="metric-label">学期内最近活动</div><div class="metric-value metric-time">${escape(summary.last_activity_at ? displayTime(summary.last_activity_at) : '暂无活动')}</div><div class="metric-note">按本学期内录入时间显示</div></div></div>` +
      `<section class="top-three-section" aria-label="五类学生次数前三名"><div class="section-title">${icon('award')}<h2>班级Top 3</h2></div><p class="small">按所选学期个人记录次数排列，零次不入榜；活动与违纪分别合并三级次数，同次数按学号排序。</p><div class="top-three-grid">${(data.top_three || []).map(group => `<article class="top-three-card"><h3><span class="top-three-heading">${icon(topThreeIcons[group.key])}${escape(group.label)}</span><small>次数前三</small></h3>${group.people?.length ? `<ol>${group.people.map((person,index) => `<li><span class="top-three-rank">${index + 1}</span>${studentIdentity(person,{href:`${base()}students/${urlPart(person.id)}/${termQuery()}`,showSex:true})}<strong>${number(person.count)}<small> 次</small></strong></li>`).join('')}</ol>` : '<p class="top-three-empty">暂无非0记录</p>'}</article>`).join('')}</div></section>` +
      `<div class="tabs" role="tablist" aria-label="班级内容"><button role="tab" id="tab-students" aria-selected="true" aria-controls="panel-students" data-tab="students">${icon('users')}学期汇总</button><button role="tab" id="tab-monthly" aria-selected="false" aria-controls="panel-monthly" data-tab="monthly" tabindex="-1">${icon('calendar')}月度明细</button><button role="tab" id="tab-records" aria-selected="false" aria-controls="panel-records" data-tab="records" tabindex="-1">${icon('clipboard')}业务记录</button></div>` +
      `<section id="panel-students" role="tabpanel" aria-labelledby="tab-students"><div class="toolbar"><div class="section-title">${icon('users')}<h2>学生汇总</h2></div><div class="student-list-tools"><label class="visually-hidden" for="student-search">搜索学生姓名或学号</label><input type="search" id="student-search" class="search" placeholder="搜索姓名或学号">${studentSortControl('student-sort')}</div></div><div id="student-results"></div><div class="table-footer"><span>${escape(policyCaption())}${data.readonly ? ' · 已固定' : ''}</span><span>点击姓名查看计分组成</span></div></section>` +
      `<section id="panel-monthly" role="tabpanel" aria-labelledby="tab-monthly" hidden></section><section id="panel-records" role="tabpanel" aria-labelledby="tab-records" hidden><form class="record-filters" method="get"><input type="hidden" name="term" value="${escape(data.term?.key || '')}"><input type="hidden" name="panel" value="records"><label>名称<input name="name" type="search" maxlength="64" value="${escape(data.record_filters?.name || '')}" placeholder="查找记录"></label><label>发生日期<input name="date" type="date" value="${escape(data.record_filters?.date || '')}"></label><label>类型<select name="kind"><option value="">全部</option>${Object.entries(kindLabels).map(([key,label]) => `<option value="${key}" ${data.record_filters?.kind === key ? 'selected' : ''}>${label}</option>`).join('')}</select></label>${button('筛选','search','type="submit"')}${link('清除','close',base()+termQuery()+'&panel=records')}</form>${recordsTable(data.records)}${pagination()}${data.august_years?.length ? `<p class="legacy-august-link">${icon('history')}8 月旧记录单独只读：${data.august_years.map(year => `<a href="${base()}august/?year=${year}">${year} 年 8 月</a>`).join(' · ')}</p>` : ''}</section>` +
      (!data.historical ? '<section id="public-report-panel" class="public-report-panel" aria-label="公开成绩报告"></section>' : '');
    const renderStudents = () => {
      const search = q('#student-search').value.trim().toLocaleLowerCase();
      const filtered = sortedStudents(students.filter(s => `${s.name} ${s.number}`.toLocaleLowerCase().includes(search)),q('#student-sort').value);
      q('#student-results').innerHTML = filtered.length ? scoreLegend()+`<div class="table-wrap overview-scroll" tabindex="0" role="region" aria-label="班级学期汇总，可横向滚动"><table class="table overview-table">${groupedScoreHeader('学期分数')}<tbody>${filtered.map(s => `<tr><th scope="row" class="identity-student">${studentIdentity(s,{href:`${base()}students/${urlPart(s.id)}/${termQuery()}`,showSex:true})}</th>${groupedCountCells(s.counts)}<td class="number score-column">${scoreBadge(s.score,s.score_base ?? monthlyPolicy().base,null,data.policy?.average_decimal_places ?? 2)}</td></tr>`).join('')}</tbody></table></div>` : empty(search ? '没有匹配的学生' : '当前学期暂无学生', search ? '试试姓名或完整学号。' : '班主任可在班级管理中新增当前学期名单。', 'users');
    };
    q('#student-search').addEventListener('input', renderStudents);
    q('#student-sort').addEventListener('change', renderStudents); renderStudents();
    qa('[data-tab]').forEach((el, index, tabs) => {
      el.addEventListener('click', () => { tabs.forEach(tab => { const chosen = tab === el; tab.setAttribute('aria-selected', String(chosen)); tab.tabIndex = chosen ? 0 : -1; q(`#panel-${tab.dataset.tab}`).hidden = !chosen; }); });
      el.addEventListener('keydown', event => { if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return; event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length-1 : (index+(event.key === 'ArrowRight' ? 1 : -1)+tabs.length)%tabs.length; tabs[next].click(); tabs[next].focus(); });
    });
    renderMonthlyOverview();
    renderPublicReport();
    if (new URL(location.href).searchParams.has('page') || new URL(location.href).searchParams.get('panel') === 'records') q('#tab-records').click();
    else if (data.monthly_overview?.months?.length) q('#tab-monthly').click();
  }
  function renderPublicReport() {
    const panel = q('#public-report-panel'); if (!panel) return;
    const report = data.public_report || {active:false};
    panel.innerHTML = `<div class="section-title">${icon('eye')}<h2>班级公开成绩报告</h2></div><p class="small">无需学生账号。持有链接或二维码的人可查看本班当前学期的学期汇总与逐月分数；不显示业务记录与管理入口。停用或更换链接后，原链接立即失效。</p>` +
      (report.active ? `<div class="public-report-link"><img src="${escape(report.qr_data_uri)}" alt="本班公开成绩报告二维码" width="164" height="164"><div><a href="${escape(report.url)}" target="_blank" rel="noopener noreferrer">打开公开报告</a><p class="small public-report-url">${escape(report.url)}</p><div class="actions">${button('复制访问链接','copy','id="copy-public-report"')}${button('下载二维码','download','id="download-public-qr"')}${owner() ? button('更换链接','refresh','id="rotate-public-report"')+button('停用链接','lock','id="disable-public-report"','danger') : ''}</div></div></div>` : `<div class="actions">${button('启用公开报告','plus','id="enable-public-report"','primary')}</div>`);
    q('#copy-public-report')?.addEventListener('click',async () => { try { await copyText(report.url); toast('公开报告链接已复制。'); } catch (_) { toast('复制失败，请手动选择链接。'); } });
    q('#download-public-qr')?.addEventListener('click',() => {
      const url=URL.createObjectURL(new Blob([report.qr_svg],{type:'image/svg+xml'}));
      const anchor=document.createElement('a'); anchor.href=url; anchor.download=`duxing-class-${data.classroom?.code || 'report'}-qr.svg`; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url),1000);
    });
    for (const action of ['enable','rotate','disable']) q(`#${action}-public-report`)?.addEventListener('click',async () => {
      if (action !== 'enable' && !confirm(action === 'rotate' ? '更换后原链接和二维码将立即失效，确定继续吗？' : '停用后现有链接和二维码将无法访问，确定继续吗？')) return;
      try { data.public_report = await request(`${base()}public-report/`,{action}); renderPublicReport(); toast(action === 'disable' ? '公开报告已停用。' : '公开报告链接已更新。'); }
      catch (error) { toast(error.message); }
    });
  }
  function renderMonthlyOverview() {
    const overview = data.monthly_overview, panel = q('#panel-monthly');
    if (!overview?.months?.length) { panel.innerHTML = empty('暂无可展示的月度明细','月度数据将在对应学期依据齐备后展示。','calendar'); return; }
    panel.innerHTML = `<div class="toolbar"><div class="section-title">${icon('calendar')}<h2>月度学生明细</h2></div><div class="student-list-tools"><label class="student-sort-control" for="monthly-period">月份<select id="monthly-period" aria-label="选择本学期月份">${overview.months.map(month => `<option value="${escape(month.key)}">${escape(month.label)}</option>`).join('')}</select></label>${studentSortControl('monthly-sort')}</div></div><p class="small">${escape(overview.caption)}</p>${scoreLegend()}<div id="monthly-results"></div>`;
    const render = key => {
      const month = overview.months.find(item => item.key === key);
      const rows = sortedStudents(overview.rows || [],q('#monthly-sort').value);
      q('#monthly-results').innerHTML = rows.length ? `<div class="table-wrap overview-scroll" tabindex="0" role="region" aria-label="${escape(month.label)}学生明细，可横向滚动"><table class="table overview-table monthly-overview-table">${groupedScoreHeader('月分')}<tbody>${rows.map(student => { const detail = (student.months || []).find(item => item.key === key); const available = detail && detail.score !== null && detail.score !== undefined && detail.counts; return `<tr><th scope="row" class="identity-student">${studentIdentity(student,{href:detail?.url || null,showSex:true})}</th>${available ? groupedCountCells(detail.counts)+`<td class="number score-column">${scoreBadge(detail.score,detail.score_base,detail.url)}</td>` : `<td colspan="10" class="month-unavailable">${icon('calendar')}${escape(detail?.reason || '该月暂无可用计分依据')}</td>`}</tr>`; }).join('')}</tbody></table></div>` : empty('该月暂无学生明细',overview.caption,'users');
    };
    q('#monthly-period').addEventListener('change',event => render(event.target.value));
    q('#monthly-sort').addEventListener('change',() => render(q('#monthly-period').value));
    const currentMonth = data.today?.slice(0,7);
    q('#monthly-period').value = overview.months.some(month => month.key===currentMonth) ? currentMonth : overview.months.at(-1).key;
    render(q('#monthly-period').value);
  }
  const drafts = new Map();
  let entrySort = 'number-asc';
  const recordContexts = new Map();
  const recordKinds = new Map();
  let recordKind = data.record?.kind || 'class';
  let classSwitchSequence = 0;
  function draftKey() { return `${data.classroom?.code}:${data.record?.id || 'new'}:${recordKind}`; }
  function currentDraft() {
    const key = draftKey();
    if (!drafts.has(key)) {
      const record = data.record?.kind === recordKind ? data.record : null;
      drafts.set(key, {kind:recordKind, name:record?.name || '', details:record?.details || '', date:record?.date || data.today || '', values:Object.fromEntries((record?.students || []).map(s => [String(s.id), s.value || 'normal'])), undo:[], dirty:false, saved:false, saving:false, pending:null, conflict:false, message:'', error:false, result:null});
    }
    return drafts.get(key);
  }
  function optionsFor(kind) {
    return kind === 'class' ? attendanceChoices : kind === 'activity' ? [['normal','未参加','minus'],['low','班级','award'],['mid','院级','award'],['high','校级','award']] : [['normal','正常','user'],['dlow','轻度','warning'],['dmid','中度','warning'],['dhigh','严重','shield']];
  }
  function draftLocked(draft = currentDraft()) { return !writable() || draft.saved || draft.saving || !!draft.pending || draft.conflict; }
  function entryChoices(student, draft) {
    const value = draft.values[String(student.id)] || 'normal';
    return optionsFor(recordKind).map(([key, label, iconName]) => `<button type="button" class="choice" data-student="${escape(student.id)}" data-value="${key}" aria-pressed="${value === key}" aria-label="${escape(student.name)}，${label}" ${draftLocked(draft) ? 'disabled' : ''}>${icon(value === key ? 'check' : iconName)}${label}</button>`).join('');
  }
  function renderEntryRows() {
    const draft = currentDraft(); const search = q('#entry-search')?.value.trim().toLocaleLowerCase() || '';
    const students = sortedStudents(readStudents().filter(s => `${s.name} ${s.number}`.toLocaleLowerCase().includes(search)),entrySort);
    q('#entry-rows').innerHTML = students.length ? students.map(s => `<div class="entry-row" data-student-row="${escape(s.id)}"><div class="entry-person">${studentIdentity(s,{showSex:true})}</div><div class="entry-options" role="group" aria-label="${escape(s.name)}的${escape(kindLabels[recordKind])}">${entryChoices(s,draft)}</div></div>`).join('') : empty(search ? '没有匹配的学生' : '当前学期暂无学生', search ? '搜索只筛选显示，其他学生的状态仍然保留。' : '请联系班主任新增当前学期名单。', 'users');
  }
  function entrySummary() {
    const draft = currentDraft(), students = readStudents(), options = optionsFor(recordKind);
    const counts = Object.fromEntries(options.map(([key]) => [key, 0]));
    students.forEach(s => { counts[draft.values[String(s.id)] || 'normal']++; });
    q('#entry-count').innerHTML = `<strong>共 ${students.length} 人</strong>${options.map(([key,label]) => `<span>${label} ${counts[key]} 人</span>`).join('')}`;
    const exceptions = students.filter(s => (draft.values[String(s.id)] || 'normal') !== 'normal');
    q('#entry-exceptions').textContent = exceptions.length ? `核对${recordKind === 'activity' ? '参与' : '异常'}：${exceptions.map(s => `${s.name}（${options.find(o => o[0] === draft.values[String(s.id)])?.[1] || '未知'}）`).join('、')}` : recordKind === 'activity' ? '当前全部为未参加，请核对本次活动参与情况。' : '当前无异常标记；默认值不代表已经逐一点名。';
    const state = draft.saving ? '保存中…' : draft.saved ? '已保存 · 已参与计分' : draft.conflict ? '版本冲突 · 输入已保留' : draft.pending ? '等待确认 · 原提交可重试' : data.readonly ? '只读记录' : '未提交';
    q('#entry-status').innerHTML = `${icon(draft.saved ? 'check' : draft.pending || draft.conflict ? 'warning' : 'clipboard')}${state}`;
    q('#entry-status').dataset.error = String(draft.error);
    showMessage('#entry-message', draft.message, draft.error);
    const save = q('#save-record');
    if (save) { save.disabled = draft.saving || draft.saved || draft.conflict || !students.length; save.innerHTML = icon(draft.pending ? 'refresh' : draft.saved ? 'check' : 'save') + (draft.saving ? '保存中…' : draft.saved ? '已保存' : draft.pending ? '重试原提交' : '核对无误，保存'); }
    const undo = q('#undo-record'); if (undo) undo.disabled = draftLocked(draft) || !draft.undo.length;
    const next = q('#next-record'); if (next) next.hidden = !draft.saved;
    const latest = q('#view-latest'); if (latest) latest.hidden = !draft.conflict || draft.rosterRefresh;
    const refresh = q('#refresh-roster'); if (refresh) refresh.hidden = !draft.rosterRefresh;
  }
  function recordPage() {
    const draft = currentDraft(), record = data.record || {}, editable = writable();
    const subtitle = data.record_return_url ? '8 月旧记录只读；不参与学期计分，仅展示当时明确保存的个人条目。' : data.readonly ? '历史学期仅供查看，记录和分数已经固定。' : '直接点选状态，核对人数后一次保存。保存即计分。';
    root.innerHTML = heading(record.id ? (data.readonly ? '查看记录' : '修正日常记录') : '快速录入', subtitle, kindIcons[recordKind], editable ? `<span class="badge" id="record-grant-badge">${icon('key')}${owner() ? '班主任已授权' : '班委 · 3小时授权'}</span>` : '', data.record_return_url ? '历史 · 8 月旧记录' : '录入 · 日常表现') +
      `<div class="entry-types" role="group" aria-label="记录类型">${Object.entries(kindLabels).map(([key,label]) => `<button type="button" class="type-button" data-kind="${key}" aria-pressed="${recordKind === key}" ${draft.saving || record.id || data.readonly ? 'disabled' : ''}>${icon(kindIcons[key])}${label}</button>`).join('')}</div>` +
      `<div class="form-grid"><label class="field"><span>${icon('edit')}记录名称</span><input id="record-name" maxlength="64" required value="${escape(draft.name)}" placeholder="例如：专业课 · 上午" ${draftLocked(draft) ? 'disabled' : ''}></label><label class="field"><span>${icon('calendar')}发生日期</span><input id="record-date" type="date" required value="${escape(draft.date)}" ${data.start_date ? `min="${escape(data.start_date)}"` : ''} ${data.today ? `max="${escape(data.today)}"` : ''} ${draftLocked(draft) ? 'disabled' : ''}></label></div>` +
      `<label class="field"><span>${icon('book')}详情 · 可选</span><textarea id="record-details" maxlength="500" rows="3" placeholder="补充地点、课程或事件说明；旧记录可留空。" ${draftLocked(draft) ? 'disabled' : ''}>${escape(draft.details)}</textarea></label>` +
      `<div class="toolbar"><div class="entry-context"><span>${escape(data.term?.label || '')} · 学生名单</span></div><div class="student-list-tools"><label class="visually-hidden" for="entry-search">搜索姓名或学号</label><input class="search" id="entry-search" type="search" placeholder="搜索姓名或学号">${studentSortControl('entry-sort',entrySort)}</div></div><div id="entry-rows" class="roster" aria-label="学生记录录入"></div>` +
      `<div class="entry-review"><div id="entry-count" class="entry-count" aria-live="polite"></div><div id="entry-exceptions" class="exceptions"></div><div class="actions"><span id="entry-status" class="entry-status" role="status"></span><div class="actions">${editable ? button('撤销上一步','undo','id="undo-record" disabled') + button('核对无误，保存','save','id="save-record"','primary') + button('下一条','plus','id="next-record" hidden') : ''}</div></div></div><p id="entry-message" class="form-message" role="alert"></p>` +
      `<div class="actions">${link(data.record_return_url ? '返回 8 月旧记录' : '返回班级','book',data.record_return_url || base()+termQuery())}${button('刷新名单并重新点名','refresh','id="refresh-roster" hidden','primary')}${record.id ? `<a id="view-latest" class="button" href="${recordHref(record)}" target="_blank" rel="noopener" hidden>${icon('eye')}在新页面核对最新记录</a>` : ''}${record.id && owner() && editable ? button('删除这条记录','trash','id="delete-record"','danger') : ''}</div>`;
    renderEntryRows(); entrySummary(); updateExpiry();
    q('#entry-search').addEventListener('input', renderEntryRows);
    q('#entry-sort').addEventListener('change',event => { entrySort = event.target.value; renderEntryRows(); });
    q('#entry-rows').addEventListener('click', event => {
      const el = event.target.closest('[data-student]'); if (!el || draftLocked()) return;
      const current = currentDraft(), id = el.dataset.student, old = current.values[id] || 'normal';
      if (old === el.dataset.value) return;
      current.undo.push({id, old, value:el.dataset.value}); current.values[id] = el.dataset.value; current.dirty = true; current.message = ''; current.error = false;
      const student = readStudents().find(s => String(s.id) === id);
      const group = el.closest('.entry-options'); group.innerHTML = entryChoices(student,current);
      qa('[data-value]',group).find(btn => btn.dataset.value === current.values[id])?.focus({preventScroll:true});
      entrySummary();
    });
    if (!record.id && !data.readonly) qa('[data-kind]',root).forEach(el => el.addEventListener('click', () => { if (recordKind === el.dataset.kind) return; recordKind = el.dataset.kind; recordPage(); qa('[data-kind]',root).find(btn => btn.dataset.kind === recordKind)?.focus(); }));
    q('#record-name').addEventListener('input', event => { const current = currentDraft(); current.name = event.target.value; current.dirty = true; });
    q('#record-details').addEventListener('input', event => { const current = currentDraft(); current.details = event.target.value; current.dirty = true; });
    q('#record-date').addEventListener('input', event => { const current = currentDraft(); current.date = event.target.value; current.dirty = true; });
    q('#undo-record')?.addEventListener('click', () => {
      const current = currentDraft(), undo = current.undo.pop(); if (!undo || draftLocked(current)) return;
      current.values[undo.id] = undo.old; current.dirty = true; renderEntryRows(); entrySummary(); toast('已撤销上一步。');
    });
    q('#save-record')?.addEventListener('click', saveRecord);
    q('#refresh-roster')?.addEventListener('click', () => {
      if (!confirm('刷新会清除本页尚未提交的点名输入。请在最新名单上重新点名并提交，确定刷新吗？')) return;
      drafts.clear(); location.reload();
    });
    q('#next-record')?.addEventListener('click', () => {
      if (data.record?.id) { location.assign(`${base()}records/new/`); return; }
      drafts.delete(draftKey()); recordPage(); q('#record-name').focus();
    });
    q('#delete-record')?.addEventListener('click', () => deleteRecord(record));
  }
  async function saveRecord() {
    const draft = currentDraft(); if (draft.saving || draft.saved || draft.conflict || !writable()) return;
    if (!draft.pending) {
      if (!q('#record-name').reportValidity() || !q('#record-date').reportValidity()) return;
      if (!draft.name.trim()) { showMessage('#entry-message','请填写记录名称。',true); q('#record-name').focus(); return; }
      draft.pending = {submission_id:uuid(), ...actorContext(), revision:data.record?.revision ?? 0, roster_revision:data.classroom?.revision ?? 0, term_key:data.term?.key, record:{kind:recordKind, name:draft.name.trim(), details:draft.details.trim(), date:draft.date, students:readStudents().map(s => ({id:s.id,value:draft.values[String(s.id)] || 'normal'}))}};
    }
    const requestUrl = data.record?.id ? `${base()}records/${urlPart(data.record.id)}/` : `${base()}records/new/`;
    const savingCode = data.classroom?.code;
    draft.saving = true; draft.message = ''; draft.error = false; recordPage();
    try {
      const result = await request(requestUrl,draft.pending);
      draft.saved = true; draft.dirty = false; draft.pending = null; draft.result = result; draft.undo = [];
      draft.message = result.replayed ? '此前的同一次提交已成功，本次没有重复计分。' : '服务器已确认保存，当前学期汇总已更新。';
    } catch (error) {
      draft.error = true;
      draft.rosterRefresh = error.code === 'roster_changed';
      draft.message = error.status === 401 ? `${owner() ? '班主任登录' : '班委授权'}已失效，名单与原提交均已保留。恢复授权后请重试。` : draft.rosterRefresh ? '学生名单已更新。请刷新名单、重新点名后再提交；当前输入仅供核对。' : error.status === 409 ? `${error.message} 请在新页面核对最新记录，当前输入已保留。` : error.message;
      if (error.status === 409) draft.conflict = true;
      if (error.status && error.status !== 401) draft.pending = null;
      if (error.status === 401) openAuth(savingCode);
    } finally { draft.saving = false; recordPage(); }
  }
  async function switchRecordClass(code) {
    const select = q('#class-switch');
    const oldCode = data.classroom?.code;
    if (!code) { select.value = oldCode || ''; toast('请选择一个班级后录入。'); return; }
    if (String(code) === String(oldCode)) return;
    if (currentDraft().saving) { select.value = oldCode || ''; toast('正在保存，请等待服务器回执后切换。'); return; }
    const sequence = ++classSwitchSequence;
    recordContexts.set(String(oldCode),data); recordKinds.set(String(oldCode),recordKind); select.disabled = true;
    try {
      const context = recordContexts.get(String(code)) || await request(`${base(code)}records/new/?format=json`, null, 'GET');
      if (sequence !== classSwitchSequence) return;
      if (context.actor?.role === 'anonymous') throw new ApiError('该班级授权已失效。',401,'authorization_expired');
      if (String(context.classroom?.code) !== String(code)) throw new ApiError('班级上下文不一致，原草稿已保留。',409,'class_context_conflict');
      data = context; recordKind = recordKinds.get(String(code)) || context.record?.kind || 'class'; updateClassNavigation(); recordPage(); toast('已切换班级；原班级各类型草稿仍保留在本页。');
    } catch (error) { select.value = oldCode || ''; if (error.status === 401) openAuth(code); else toast(error.message); }
    finally { select.disabled = false; }
  }
  function updateClassNavigation() {
    const paths = {class:'',record:'records/new/',roster:'roster/',events:'events/'};
    qa('[data-nav]').forEach(el => { el.href = base()+paths[el.dataset.nav]+(el.dataset.nav === 'record' ? '' : termQuery()); });
    const nav = q('.class-nav'), systemNav = q('.main-nav');
    if (q('#class-context-name')) q('#class-context-name').textContent = data.classroom?.name || '班级空间';
    if (q('#class-context-term')) q('#class-context-term').textContent = data.term?.label || '';
    const ownerLinks = [['roster','班级管理','users'],['events','操作记录','history']];
    ownerLinks.forEach(([name,label,iconName]) => {
      let el = q(`[data-nav="${name}"]`,nav);
      if (!el && owner()) { el = document.createElement('a'); el.dataset.nav = name; el.innerHTML = icon(iconName)+label; nav.append(el); }
      if (el) { el.hidden = !owner(); el.href = base()+paths[name]+termQuery(); }
    });
    let rules = q('[data-global-nav="rules"]',systemNav);
    if (!rules && owner()) { rules = document.createElement('a'); rules.dataset.globalNav = 'rules'; rules.innerHTML = icon('sliders')+'评分规则'; systemNav.append(rules); }
    if (rules) rules.hidden = !owner();
    qa('[data-global-nav]').forEach(el => { el.href = (el.dataset.globalNav === 'rules' ? '/rules/' : '/')+termQuery(); });
    let recordLink = q('[data-nav="record"]',nav);
    if (!recordLink && writable()) { recordLink = document.createElement('a'); recordLink.dataset.nav = 'record'; recordLink.setAttribute('aria-current','page'); recordLink.innerHTML = icon('clipboard')+'快速录入'; nav.append(recordLink); }
    if (recordLink) { recordLink.href = `${base()}records/new/`; recordLink.hidden = !writable(); }
    q('.identity strong').textContent = data.actor?.display_name || data.actor?.username || data.actor?.label || '访客';
    q('#identity-caption').textContent = owner() ? '班主任' : data.actor?.role === 'committee' ? '班委账号 · 固定3小时' : '请先登录';
    let readonlyNotice = q('.readonly-notice');
    if (data.readonly) {
      if (!readonlyNotice) { readonlyNotice = document.createElement('div'); readonlyNotice.className = 'notice readonly-notice'; q('#workspace-main').prepend(readonlyNotice); }
      readonlyNotice.innerHTML = `${icon('lock')}<div><strong>${escape(data.readonly_label || '已结束 · 只读')}</strong><p>${escape(data.readonly_reason || '本学期仅可查看。')}</p></div>`;
    } else readonlyNotice?.remove();
    const select = q('#class-switch'); if (select) select.value = data.classroom?.code || '';
    const term = q('#term-switch'); if (term && data.term) { term.innerHTML = (data.terms || [data.term]).map(t => `<option value="${escape(t.key)}" ${t.key === data.term.key ? 'selected' : ''}>${escape(t.label)}</option>`).join(''); }
    updateExpiry();
  }
  function studentPage() {
    const student = data.student || {}, months = data.months || student.months || [], selected = data.selected_month, monthly = monthlyPolicy();
    const selectedCounts = selected ? selected.counts : student.counts;
    const monthHref = month => `${base()}students/${urlPart(student.id)}/?term=${urlPart(data.term?.key)}&month=${urlPart(month.key)}`;
    root.innerHTML = heading(student.name || '学生详情', `${student.number || ''} · ${selected?.label || data.term?.label || ''}`, 'user', link('返回班级','book',base()+termQuery())+(selected && data.term_detail_url ? link('查看整学期','calendar',data.term_detail_url) : ''), '学生 · 计分明细') +
      (selected && selected.available === false ? `<div class="notice">${icon('calendar')}<div><strong>该月计分依据暂不可用</strong><p>${escape(selected.reason)}</p></div></div>` : '') +
      `<div class="metrics"><div><div class="metric-label">${selected ? '所选月分' : '学期分数'}</div><div class="metric-value">${scoreBadge(selected ? selected.score : student.score,selected ? selected.score_base : student.score_base ?? monthly.base,null,selected ? 2 : data.policy?.average_decimal_places ?? 2)}</div><div class="metric-note">${selected ? escape(selected.label) : '各计分月分数的平均值'}</div></div><div><div class="metric-label">${selected ? '当期月度基础分' : '计分月份'}</div><div class="metric-value">${selected ? fmt(selected.score_base) : months.length}<small>${selected ? ' 分' : ' 个月'}</small></div><div class="metric-note">进入自然月即有基础分</div></div><div><div class="metric-label">评分规则</div><div class="metric-value">${number(data.policy?.version) > 0 ? `v${escape(data.policy.version)}` : policyIdentity() === '默认规则' ? '默认' : '当期'}</div><div class="metric-note">${escape(policyCaption())}</div></div></div>` +
      `<div class="detail-formula">月分依据：月度基础分 + 活动加分 − 考勤扣分 − 违纪扣分<p>${escape(monthlySummary(monthly))}。先按当期上下限确定每月分数，再取学期平均。无活动计分月采用当期基础分。</p></div><div class="section-title">${icon('clipboard')}<h2>${selected ? '所选月份' : '所选学期'}次数明细</h2></div>${scoreLegend()}${selectedCounts ? `<div class="detail-counts">${countFields.map(key => `<div><span>${labels[key]}</span>${countBadge(selectedCounts[key],key)}</div>`).join('')}</div>` : empty('暂无可用次数明细',selected?.reason || '具体依据尚待核实。','clipboard')}<div class="section-title" style="margin-top:26px">${icon('calendar')}<h2>自然月分值</h2></div>` +
      (months.length ? `<div class="table-wrap"><table class="table"><thead><tr><th>月份</th><th>活动加分</th><th>扣分</th><th>月分</th></tr></thead><tbody>${months.map(month => `<tr${selected?.key === month.key ? ' class="selected-month-row"' : ''}><td><a href="${escape(monthHref(month))}">${escape(month.label || month.key)}</a></td><td class="number"><span class="amount-reward">${month.addition !== undefined ? `+${fmt(month.addition)}` : monthlyDelta(month.counts,'add')}</span></td><td class="number"><span class="amount-deduction">${month.deduction !== undefined ? `−${fmt(month.deduction)}` : monthlyDelta(month.counts,'subtract')}</span></td><td class="number">${scoreBadge(month.score,month.score_base ?? monthly.base,monthHref(month))}</td></tr>`).join('')}</tbody></table></div>` : empty('暂无月份明细','具体计分依据以当期汇总为准。','calendar')) +
      `<div class="section-title" style="margin-top:28px">${icon('history')}<h2>${selected ? escape(selected.label)+' · ' : ''}日常记录</h2></div><form class="record-filters" method="get"><input type="hidden" name="term" value="${escape(data.term?.key || '')}">${selected ? `<input type="hidden" name="month" value="${escape(selected.key)}">` : ''}<label>名称<input name="name" type="search" maxlength="64" value="${escape(data.record_filters?.name || '')}" placeholder="查找记录"></label><label>发生日期<input name="date" type="date" value="${escape(data.record_filters?.date || '')}"></label><label>类型<select name="kind"><option value="">全部</option>${Object.entries(kindLabels).map(([key,label]) => `<option value="${key}" ${data.record_filters?.kind === key ? 'selected' : ''}>${label}</option>`).join('')}</select></label>${button('筛选','search','type="submit"')}${link('清除','close',selected ? monthHref(selected) : data.term_detail_url)}</form>${recordsTable(data.records,{studentMode:true})}${pagination()}`;
  }
  function monthlyDelta(counts, direction) {
    if (!counts || !data.policy?.weights) return '—';
    const keys = direction === 'add' ? ['low','mid','high'] : ['absent','late','leave','dlow','dmid','dhigh'];
    const sum = keys.reduce((total,key) => total+number(counts[key])*number(data.policy.weights[key]),0);
    return `${direction === 'add' ? '+' : '−'}${fmt(sum)}`;
  }
  const policyGroups = [
    {name:'考勤扣分', icon:'clipboard', direction:'扣', items:['absent','late','leave']},
    {name:'活动加分', icon:'award', direction:'加', items:['low','mid','high']},
    {name:'违纪扣分', icon:'shield', direction:'扣', items:['dlow','dmid','dhigh']}
  ];
  let policyDirty = false, policyPreview = null, policyPreviewKey = null, policyPending = null, policySaving = false;
  function readWeights() {
    const values = {};
    for (const input of qa('[data-weight]',root)) {
      if (!input.reportValidity() || !/^\d+(?:\.\d{1,2})?$/.test(input.value.trim())) { input.focus(); throw new Error('权重须为非负数，且每次以 0.5 分递增。'); }
      values[input.dataset.weight] = input.value.trim();
    }
    return values;
  }
  function readAverageDecimalPlaces() { return Number(q('#average-decimal-places').value); }
  function readMonthly() {
    const values = {};
    for (const input of qa('[data-monthly]',root)) {
      const value = input.value.trim();
      if (input.dataset.monthly === 'maximum' && !value) { values.maximum = null; continue; }
      if (!input.reportValidity() || !/^\d+(?:\.\d{1,2})?$/.test(value)) { input.focus(); throw new Error('月度分值须为非负数，且每次以 0.5 分递增。'); }
      values[input.dataset.monthly] = value;
    }
    if (Number(values.minimum) > Number(values.base) || (values.maximum !== null && Number(values.base) > Number(values.maximum))) throw new Error('月度最低分不能高于基础分；最高分不能低于基础分。');
    return values;
  }
  function monthlySummary(monthly = monthlyPolicy()) { return `月基础 ${fmt(monthly.base)} 分 · 最低 ${fmt(monthly.minimum)} 分 · ${monthly.maximum === null ? '无上限' : `最高 ${fmt(monthly.maximum)} 分`}`; }
  function cancelPolicyChanges() {
    if (policySaving || policyPending) return;
    const policy = data.policy || {}, monthly = monthlyPolicy(policy);
    qa('[data-weight]').forEach(input => { input.value = policy.weights?.[input.dataset.weight] ?? '0'; });
    qa('[data-monthly]').forEach(input => { input.value = monthly[input.dataset.monthly] ?? ''; });
    q('#average-decimal-places').value = String(policy.average_decimal_places ?? 2);
    policyDirty = false; policyPreview = null; policyPreviewKey = null;
    q('#policy-preview').hidden = true;
    q('#save-policy').disabled = true;
    q('#cancel-policy').disabled = true;
    q('#monthly-summary').textContent = monthlySummary(monthly);
    showMessage('#policy-message','已取消修改，恢复到已保存的规则。');
  }
  function rulesPage() {
    const policy = data.policy || {revision:0,weights:{}};
    const monthly = monthlyPolicy(policy), readonly = data.readonly || !owner();
    root.innerHTML = heading('统一规则，清楚计分','每次事件对应的加减分值，由班主任统一设置。','sliders',`<span class="badge">${icon(data.readonly ? 'lock' : 'key')}${data.readonly ? '历史规则只读' : '班主任设置'}</span>`,'规则 · 操行评分') +
      `<div class="notice">${icon('book')}<div><strong>${data.readonly ? '本学期评分依据已固定' : `应用于本人全部 ${(data.classes || []).length} 个班级`}</strong><p>${data.readonly ? '历史学期保留原有权重和分数。' : '从当前学期开始生效，未来学期沿用；历史学期的规则和分数不受影响。'}</p></div></div>` +
      `<form id="rules-form"><section class="monthly-policy-group"><div class="section-title">${icon('calendar')}<h2>月度基础与分数边界</h2></div><div class="monthly-fields"><label class="field">月度基础分<input data-monthly="base" type="number" inputmode="decimal" min="0" max="999999.5" step="0.5" required value="${escape(monthly.base)}" ${readonly ? 'disabled' : ''}></label><label class="field">月度最低分<input data-monthly="minimum" type="number" inputmode="decimal" min="0" max="999999.5" step="0.5" required value="${escape(monthly.minimum)}" ${readonly ? 'disabled' : ''}></label><label class="field">月度最高分<input data-monthly="maximum" type="number" inputmode="decimal" min="0" max="999999.5" step="0.5" value="${escape(monthly.maximum ?? '')}" placeholder="留空表示无上限" ${readonly ? 'disabled' : ''}></label></div><label class="field average-digits">学期平均分显示位数<select id="average-decimal-places" ${readonly ? 'disabled' : ''}>${[0,1,2,3,4].map(places => `<option value="${places}" ${Number(policy.average_decimal_places ?? 2) === places ? 'selected' : ''}>${places} 位</option>`).join('')}</select></label><p class="small">先按基础分及各项加减分计算每月分数，再限制在最低分与最高分之间；学期分数取各计分月平均。最高分可留空。所有分值以 0.5 分为最小调整单位；显示位数只影响学期平均分展示。</p></section><div class="policy-grid">${policyGroups.map(group => `<section class="policy-group"><h2>${icon(group.icon)}${group.name}</h2>${group.items.map(key => `<label class="weight-row"><span>${labels[key]}</span><input type="number" inputmode="decimal" min="0" max="999999.5" step="0.5" required data-weight="${key}" value="${escape(policy.weights[key] ?? '0')}" ${readonly ? 'disabled' : ''} aria-label="${labels[key]}${group.direction}分值"><span>${group.direction}分</span></label>`).join('')}</section>`).join('')}</div><div class="actions" style="justify-content:space-between"><span class="small" id="monthly-summary">${escape(monthlySummary(monthly))}</span>${!readonly ? button('预览变化','eye','id="preview-policy" type="button"')+button('取消修改','undo','id="cancel-policy" type="button" disabled') : ''}</div><div id="policy-preview" hidden></div><div class="actions" style="justify-content:space-between;margin-top:24px"><span class="small" id="policy-version">${escape(policyCaption(policy))}</span>${!readonly ? button('从当前学期应用','save','id="save-policy" type="submit" disabled','primary') : ''}</div><p id="policy-message" class="form-message" role="alert"></p></form>`;
    qa('[data-weight],[data-monthly],#average-decimal-places').forEach(input => input.addEventListener('input',() => { policyDirty = true; policyPreview = null; policyPreviewKey = null; q('#policy-preview').hidden = true; q('#save-policy').disabled = true; q('#cancel-policy').disabled = false; showMessage('#policy-message','修改后请重新预览，再确认应用。'); }));
    q('#cancel-policy')?.addEventListener('click',cancelPolicyChanges);
    q('#preview-policy')?.addEventListener('click',previewPolicy);
    q('#rules-form').addEventListener('submit',savePolicy);
  }
  function renderPolicyPreview(preview) {
    const el = q('#policy-preview'); el.hidden = false; el.className = 'policy-preview';
    const classes = preview.classes || [];
    el.innerHTML = `<div class="toolbar"><div class="section-title">${icon('eye')}<h2>当前学期影响预览</h2></div><span class="small">${classes.length} 个班级${preview.student_count !== undefined ? ` · ${number(preview.student_count)} 位学生` : ''}</span></div>` +
      (classes.length ? `<div class="table-wrap"><table class="table policy-impact-table"><thead><tr><th>班级</th><th>影响学生数</th><th>分数变化人数</th><th>最大个人变化</th></tr></thead><tbody>${classes.map(c => `<tr><td>${escape(c.name)}</td><td class="number">${number(c.student_count)} 人</td><td class="number">${number(c.changed_count)} 人</td><td class="number">${fmt(c.max_absolute_change)} 分</td></tr>`).join('')}</tbody></table></div>` : empty('当前没有受影响的班级','保存后仍会作为未来学期默认规则。')) +
      `<p class="small" style="margin-top:14px">学期平均分将显示 ${readAverageDecimalPlaces()} 位小数。已结束学期不受影响；若预览后有新的业务记录，保存时将按最新记录重新计算。</p>`;
  }
  function lockPolicy(locked) {
    qa('[data-weight],[data-monthly],#average-decimal-places').forEach(input => input.disabled = locked);
    const preview = q('#preview-policy'); if (preview) preview.disabled = locked;
    const cancel = q('#cancel-policy'); if (cancel) cancel.disabled = locked || !policyDirty || !!policyPending;
  }
  async function previewPolicy() {
    if (policySaving || policyPending) return;
    let weights, monthly, average_decimal_places; try { weights = readWeights(); monthly = readMonthly(); average_decimal_places = readAverageDecimalPlaces(); } catch (error) { showMessage('#policy-message',error.message,true); return; }
    lockPolicy(true); q('#save-policy').disabled = true; showMessage('#policy-message','正在读取当前学期数据并计算影响…');
    try {
      const result = await request('/rules/',{action:'preview', revision:data.policy?.revision ?? 0, term_key:data.term?.key, weights,monthly,average_decimal_places});
      policyPreview = result.preview || result; policyPreviewKey = JSON.stringify({weights,monthly,average_decimal_places}); renderPolicyPreview(policyPreview); q('#monthly-summary').textContent = monthlySummary(monthly);
      q('#save-policy').disabled = false; showMessage('#policy-message','预览完成，确认后点击应用。');
    } catch (error) { explain(error,'#policy-message'); }
    finally { lockPolicy(false); }
  }
  async function savePolicy(event) {
    event.preventDefault(); if (policySaving || data.readonly || !owner()) return;
    if (!policyPending) {
      let weights, monthly, average_decimal_places; try { weights = readWeights(); monthly = readMonthly(); average_decimal_places = readAverageDecimalPlaces(); } catch (error) { showMessage('#policy-message',error.message,true); return; }
      if (!policyPreview || policyPreviewKey !== JSON.stringify({weights,monthly,average_decimal_places})) { showMessage('#policy-message','请先预览当前规则的影响。',true); return; }
      policyPending = {submission_id:uuid(), ...actorContext(), action:'save', revision:data.policy?.revision ?? 0, term_key:data.term?.key, weights,monthly,average_decimal_places};
    }
    policySaving = true; lockPolicy(true); const submit = q('#save-policy'); submit.disabled = true; submit.innerHTML = icon('save')+'正在应用…'; showMessage('#policy-message','正在保存评分规则…');
    try {
      const result = await request('/rules/',policyPending);
      data.policy = result.policy || {...data.policy,revision:result.revision ?? data.policy?.revision,weights:policyPending.weights,monthly:policyPending.monthly,average_decimal_places:policyPending.average_decimal_places};
      policyPending = null; policyDirty = false; policyPreview = null; policyPreviewKey = null; q('#cancel-policy').disabled = true;
      q('#policy-version').textContent = policyCaption();
      q('#monthly-summary').textContent = monthlySummary();
      if (result.preview) renderPolicyPreview(result.preview);
      showMessage('#policy-message',result.replayed ? '此前同一次保存已应用，没有重复创建规则。' : '新设置从当前学期开始生效；历史学期的规则和分数不受影响。');
      submit.innerHTML = icon('check')+'已应用';
    } catch (error) {
      explain(error,'#policy-message');
      if (error.status && error.status !== 401) policyPending = null;
      if (error.status === 409) { policyPreview = null; policyPreviewKey = null; showMessage('#policy-message',`${error.message} 请在新页面查看最新规则，保留本页输入进行核对。`,true); }
      submit.innerHTML = icon(policyPending ? 'refresh' : 'save')+(policyPending ? '重试原提交' : '从当前学期应用');
      submit.disabled = !policyPending && !policyPreview;
    } finally { policySaving = false; lockPolicy(!!policyPending); }
  }
  let modalDirty = false, modalPending = false, modalBusy = false;
  function modalForm({title,description,fields,submitLabel='确认保存',danger=false,url,build,onSuccess,onReady}) {
    const dialog = q('#action-dialog'); const previousFocus = document.activeElement;
    dialog.innerHTML = `<form id="action-form"><div class="section-title">${icon(danger ? 'warning' : 'edit')}<h2 id="action-title">${escape(title)}</h2></div><p>${escape(description)}</p>${fields}<p id="action-message" class="form-message" role="alert"></p><div class="actions">${button('取消','close','type="button" id="action-cancel"')}${button(submitLabel,danger ? 'trash' : 'save','type="submit"',danger ? 'danger' : 'primary')}</div></form>`;
    let pending = null, busy = false;
    modalDirty = false; modalPending = false; modalBusy = false;
    const form = q('#action-form'), cancel = q('#action-cancel'), submit = q('[type=submit]',form);
    form.addEventListener('input',() => { modalDirty = true; });
    form.addEventListener('change',() => { modalDirty = true; });
    const attemptClose = event => { event?.preventDefault(); if (busy || pending) { showMessage('#action-message','请先确认原提交的结果；输入和提交编号仍保留。',true); return; } if (modalDirty && !confirm('这项输入尚未保存，确认放弃并关闭吗？')) return; modalDirty = false; form.reset(); dialog.close(); previousFocus?.focus(); };
    cancel.addEventListener('click',attemptClose);
    dialog.oncancel = attemptClose;
    form.addEventListener('submit',async event => {
      event.preventDefault(); if (busy) return;
      if (!pending) {
        if (!form.reportValidity()) return;
        try { pending = {submission_id:uuid(),...actorContext(),term_key:data.term?.key,...build(form)}; modalPending = true; } catch (error) { showMessage('#action-message',error.message,true); return; }
      }
      busy = true; modalBusy = true; submit.disabled = true; cancel.disabled = true; qa('input,textarea,select',form).forEach(el => el.disabled = true);
      showMessage('#action-message','正在保存…');
      try {
        const result = await request(url,pending); const submitted = pending; pending = null; modalPending = false; modalDirty = false; modalBusy = false; form.reset(); dialog.close();
        try { await onSuccess(result,submitted); } finally { if ('password' in submitted) submitted.password = ''; }
      } catch (error) {
        explain(error,'#action-message');
        if (error.status && error.status !== 401) { pending = null; modalPending = false; }
        submit.innerHTML = icon(pending ? 'refresh' : 'save')+(pending ? '重试原提交' : submitLabel);
      } finally { busy = false; modalBusy = false; submit.disabled = false; cancel.disabled = !!pending; qa('input,textarea,select',form).forEach(el => el.disabled = !!pending); }
    });
    dialog.showModal(); if (onReady) onReady(form); q('input,button',dialog)?.focus();
  }
  function createClass() {
    modalForm({title:'创建班级',description:'班级由当前班主任管理。创建后可新增学生并设置独立班委账号。',fields:'<label class="field">班级名称<input name="name" required maxlength="30" autocomplete="off"></label>',url:'/',build:form => ({action:'create',name:form.elements.name.value.trim()}),onSuccess:result => { location.assign(result.url || base(result.classroom?.code || result.code)); }});
  }
  function deleteRecord(record) {
    modalForm({title:'删除这条记录',description:`将删除「${record.name}」及其 ${record.student_count ?? readStudents().length} 位学生的记录，当前学期分数将重新计算，概要日志会保留。`,fields:'',submitLabel:'确认删除',danger:true,url:`${base()}records/${urlPart(record.id)}/`,build:() => ({action:'delete',revision:record.revision}),onSuccess:() => { currentDraft().dirty = false; location.assign(base()+termQuery()); }});
  }
  function rosterPage() {
    const students = readStudents(), canManage = owner() && !data.readonly;
    root.innerHTML = heading('班级名单与授权',data.classroom?.name || '当前学期名单管理','users',canManage ? button('批量新增','users','id="open-bulk"')+button('新增学生','plus','id="add-student"','primary') : '', '班级 · 管理') +
      `<div class="notice">${icon(data.readonly ? 'lock' : 'calendar')}<div><strong>${data.readonly ? '已结束学期名单固定' : '新增仅加入当前学期'}</strong><p>${data.readonly ? '当前班级的名单变更不会改变本学期保存的身份与成绩。' : '新增学生参与当前学期全部已进入月份的计分，不补入已结束学期；姓名修改和移除不改动历史名单。'}</p></div></div>` +
      `<div class="toolbar"><div class="section-title">${icon('users')}<h2>在册学生 · ${students.length} 人</h2></div><div class="student-list-tools"><label class="visually-hidden" for="roster-search">搜索姓名或学号</label><input id="roster-search" class="search" type="search" placeholder="搜索姓名或学号">${studentSortControl('roster-sort')}</div></div><div id="roster-results"></div>` +
      (canManage ? `<details id="bulk-panel" class="bulk-panel"><summary>${icon('users')}批量新增学生</summary><p class="small">可下载模板填写后直接上传 Excel，也可复制学号、姓名、性别三列并粘贴，或每行填写“学号|姓名|男/女”。性别支持 male/female，一次最多1000人。</p><div class="import-tools"><a class="button" href="/static/downloads/ams-template-add-students.xlsx" download>${icon('download')}下载 Excel 模板</a><label class="field import-file-label">选择 Excel 文件<input id="roster-file" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"></label>${button('上传并预览','upload','id="upload-roster" type="button"')}</div><p class="small">请清除模板中的六行示例后填写，学号列保持文本格式，输入列不要使用公式；文件不超过2 MiB。上传仅解析和校验，确认预览后才会正式新增。</p><form id="bulk-form"><label class="field" for="bulk-text">待新增名单<textarea id="bulk-text" name="students_text" rows="7" maxlength="140000" required autocomplete="off" spellcheck="false" placeholder="可粘贴 Excel 的学号、姓名、性别三列&#10;2026001|林沐|女&#10;2026002|陈知夏|男"></textarea></label><div class="actions" style="margin-top:14px">${button('预览名单','eye','id="preview-bulk" type="button"')}<span class="small">预览无误后，一次性加入当前学期</span></div><div id="bulk-preview" hidden></div><div class="actions" style="margin-top:16px">${button('确认新增','save','id="save-bulk" type="submit" disabled','primary')}${button('继续下一批','plus','id="next-bulk" type="button" hidden')}</div><p id="bulk-message" class="form-message" role="alert"></p></form></details>` : '') +
      (owner() ? '<section class="account-panel" id="committee-panel" aria-label="班委账号管理"></section>' : '') +
      (canManage && data.class_management ? classDangerZone() : '');
    const render = () => {
      const search = q('#roster-search').value.trim().toLocaleLowerCase(); const list = sortedStudents(students.filter(s => `${s.name} ${s.number}`.toLocaleLowerCase().includes(search)),q('#roster-sort').value);
      q('#roster-results').innerHTML = list.length ? `<div class="table-wrap"><table class="table"><thead><tr><th>学生</th><th>性别</th>${canManage ? '<th>当前学期管理</th>' : ''}</tr></thead><tbody>${list.map(s => `<tr><td>${studentIdentity(s)}</td><td>${escape(({male:'男',female:'女'})[s.sex] || '未记录')}</td>${canManage ? `<td><div class="actions end">${button('修改','edit',`data-edit-student="${escape(s.id)}"`)}${button('移除','trash',`data-remove-student="${escape(s.id)}"`,'danger')}</div></td>` : ''}</tr>`).join('')}</tbody></table></div>` : empty(search ? '没有匹配的学生' : '当前学期暂无学生',search ? '试试姓名或学号。' : canManage ? '点击新增学生添加当前学期名单。' : '该学期没有可展示的名单。','users');
    };
    q('#roster-search').addEventListener('input',render);
    q('#roster-sort').addEventListener('change',render); render();
    q('#add-student')?.addEventListener('click',() => editStudent(null));
    q('#roster-results').addEventListener('click',event => {
      const edit = event.target.closest('[data-edit-student]'), remove = event.target.closest('[data-remove-student]');
      if (edit) editStudent(students.find(s => String(s.id) === edit.dataset.editStudent));
      if (remove) removeStudent(students.find(s => String(s.id) === remove.dataset.removeStudent));
    });
    if (canManage) setupBulkRoster();
    if (owner()) renderCommitteeAccounts();
    if (canManage && data.class_management) setupClassDangerZone();
  }

  function classDangerZone() {
    const state = data.class_management || {}, records = number(state.record_count), students = number(state.student_count);
    const deleteReason = state.has_history ? '该班已有历史学期资料，按只读规则不能删除。' : records || students ? '请依次清空当前学期数据和学生后再删除。' : '班级为空且没有历史资料，可以删除。';
    return `<section class="danger-zone" aria-labelledby="danger-zone-title"><div class="section-title">${icon('warning')}<h2 id="danger-zone-title">危险操作</h2></div><p class="small">以下操作仅限班主任，均需输入完整班级名称确认。历史学期快照不会被清空或改写。</p><div class="danger-actions"><article><strong>清空数据</strong><p>删除当前学期 ${records} 条考勤、活动和违纪记录，保留学生、班委账号及评分规则。</p>${button('清空当前学期数据','trash',`id="clear-class-data" ${records ? '' : 'disabled'}`,'danger')}</article><article><strong>清空学生</strong><p>移出当前学期 ${students} 名学生。存在业务记录时须先清空数据；历史身份仍保留。</p>${button('清空当前学期学生','users',`id="clear-class-students" ${students && !records ? '' : 'disabled'}`,'danger')}</article><article><strong>删除班级</strong><p>${escape(deleteReason)} 删除后班委账号和公开链接同时失效。</p>${button('删除班级','trash',`id="delete-class" ${state.can_delete ? '' : 'disabled'}`,'danger')}</article></div></section>`;
  }

  function setupClassDangerZone() {
    const configs = {
      clear_data:{button:'#clear-class-data',title:'清空当前学期数据',submit:'确认清空数据',description:`将永久删除「${data.classroom?.name || ''}」当前学期的考勤、活动和违纪记录，并立即重新计算成绩。学生名单、历史学期和操作日志保留。`},
      clear_students:{button:'#clear-class-students',title:'清空当前学期学生',submit:'确认清空学生',description:`将「${data.classroom?.name || ''}」的所有学生移出当前学期名单。历史学期身份和成绩保留；此操作仅在当前学期已无业务记录时执行。`},
      delete_class:{button:'#delete-class',title:'删除班级',submit:'确认删除班级',description:`将永久删除空班级「${data.classroom?.name || ''}」，班委账号与公开链接会立即失效。已有历史学期资料的班级不允许删除。`},
    };
    Object.entries(configs).forEach(([action,config]) => q(config.button)?.addEventListener('click',() => {
      modalForm({title:config.title,description:config.description,fields:`<label class="field">输入完整班级名称以确认<input name="confirmation" required autocomplete="off" maxlength="30" placeholder="${escape(data.classroom?.name || '')}"></label>`,submitLabel:config.submit,danger:true,url:`${base()}management/`,build:form => ({action,revision:data.classroom?.revision ?? 0,confirmation:form.elements.confirmation.value.trim()}),onSuccess:result => location.assign(result.url || '/'),});
    }));
  }
  let bulkDirty = false, bulkPending = null, bulkSaving = false;
  function setupBulkRoster() {
    let preview = null, previewText = null, previewSort = 'number-asc';
    const input = q('#bulk-text'), previewButton = q('#preview-bulk'), saveButton = q('#save-bulk');
    const fileInput = q('#roster-file'), uploadButton = q('#upload-roster');
    const setLock = locked => { input.disabled = locked; previewButton.disabled = locked; fileInput.disabled = locked; uploadButton.disabled = locked; };
    const invalidatePreview = message => { bulkDirty = !!input.value.trim() || !!fileInput.files.length; preview = null; previewText = null; bulkPending = null; q('#bulk-preview').hidden = true; saveButton.disabled = true; q('#next-bulk').hidden = true; q('#bulk-refresh')?.remove(); saveButton.innerHTML = icon('save')+'确认新增'; showMessage('#bulk-message',message); };
    const applyPreview = (result,text) => {
      preview = result.students || []; previewText = text;
      const el = q('#bulk-preview'); el.hidden = false; el.className = 'policy-preview';
      el.innerHTML = `<div class="toolbar"><div class="section-title">${icon('eye')}<h2>待新增 ${number(result.count ?? preview.length)} 人</h2></div>${studentSortControl('bulk-sort',previewSort)}</div><div class="table-wrap bulk-preview-table"><table class="table"><thead><tr><th>学生</th><th>性别</th></tr></thead><tbody></tbody></table></div><p class="small" style="margin-top:13px">已检查批次内重复学号和本班当前名单；任一重复都会整批拒绝。${number(result.reactivate_count) ? `其中 ${number(result.reactivate_count)} 人曾移出本学期，将恢复为在册并采用本次姓名、性别。` : ''} 这批学生将参与当前学期全部已进入月份的计分，不加入已结束学期。整批同时成功或全部不写入。</p>`;
      const renderPreviewRows = () => { q('#bulk-preview tbody').innerHTML = sortedStudents(preview,previewSort).map(s => `<tr><td>${studentIdentity(s)}</td><td>${s.sex === 'female' ? '女' : '男'}</td></tr>`).join(''); };
      q('#bulk-sort').addEventListener('change',event => { previewSort = event.target.value; renderPreviewRows(); }); renderPreviewRows();
      saveButton.disabled = !preview.length; bulkDirty = true; showMessage('#bulk-message','预览已通过，请核对名单与数量后确认新增。');
    };
    q('#open-bulk').addEventListener('click',() => { q('#bulk-panel').open = true; input.focus(); });
    input.addEventListener('input',() => invalidatePreview('文本已修改，请重新预览名单。'));
    fileInput.addEventListener('change',() => invalidatePreview('文件已选择，请上传并预览；原预览已失效。'));
    uploadButton.addEventListener('click',async () => {
      if (bulkSaving || bulkPending) return;
      const file = fileInput.files[0];
      if (!file) { showMessage('#bulk-message','请先选择填写好的 Excel 文件。',true); fileInput.focus(); return; }
      if (!/\.xlsx$/i.test(file.name)) { showMessage('#bulk-message','请使用 .xlsx 格式，可先下载本页 Excel 模板。',true); return; }
      if (file.size > 2*1024*1024) { showMessage('#bulk-message','Excel 文件不能超过2 MiB，请按模板分批整理。',true); return; }
      const body = new FormData(); body.append('file',file); body.append('term_key',data.term?.key || ''); body.append('revision',String(data.classroom?.revision ?? 0));
      setLock(true); saveButton.disabled = true; showMessage('#bulk-message','正在上传并校验 Excel；尚未新增学生…');
      try {
        const result = await request(`${base()}roster/import/`,body);
        input.value = result.students_text || ''; bulkPending = null; q('#next-bulk').hidden = true;
        applyPreview(result,input.value.trim());
      } catch (error) { preview = null; previewText = null; q('#bulk-preview').hidden = true; explain(error,'#bulk-message'); }
      finally { setLock(false); }
    });
    const textValue = () => {
      if (!input.reportValidity()) return null;
      const value = input.value.trim(); const count = value.split(/\r?\n/).filter(line => line.trim()).length;
      if (count > 1000) { showMessage('#bulk-message','一次最多新增1000人，请分批处理。',true); return null; }
      if (!count) { showMessage('#bulk-message','请先粘贴学生名单。',true); return null; }
      return value;
    };
    previewButton.addEventListener('click',async () => {
      if (bulkSaving || bulkPending) return;
      const text = textValue(); if (!text) return;
      setLock(true); saveButton.disabled = true; showMessage('#bulk-message','正在检查姓名、学号、性别和重复项…');
      try {
        const result = await request(`${base()}roster/`,{action:'preview_add',students_text:text,revision:data.classroom?.revision ?? 0,term_key:data.term?.key});
        applyPreview(result,text);
      } catch (error) { preview = null; previewText = null; q('#bulk-preview').hidden = true; explain(error,'#bulk-message'); }
      finally { setLock(false); }
    });
    q('#bulk-form').addEventListener('submit',async event => {
      event.preventDefault(); if (bulkSaving) return;
      if (!bulkPending) {
        const text = textValue(); if (!text) return;
        if (!preview?.length || previewText !== text) { showMessage('#bulk-message','请先预览当前文本中的名单。',true); return; }
        bulkPending = {action:'bulk_add',submission_id:uuid(),...actorContext(),students_text:text,revision:data.classroom?.revision ?? 0,term_key:data.term?.key};
      }
      bulkSaving = true; setLock(true); saveButton.disabled = true; saveButton.innerHTML = icon('save')+'正在新增…'; showMessage('#bulk-message','正在保存整批名单…');
      try {
        const result = await request(`${base()}roster/`,bulkPending);
        data.classroom.revision = result.revision; data.students = [...readStudents(),...(result.students || [])];
        bulkPending = null; bulkDirty = false; preview = null; previewText = null;
        saveButton.innerHTML = icon('check')+'已新增'; q('#next-bulk').hidden = false;
        showMessage('#bulk-message',`${result.replayed ? '此前同一次提交已完成，未重复新增。' : '整批名单已保存。'} 本次共 ${number(result.count)} 人${number(result.reactivate_count) ? `，其中恢复 ${number(result.reactivate_count)} 人` : ''}。刷新页面可查看最新名单。`);
        const old = q('#bulk-refresh'); old?.remove();
        const refresh = document.createElement('a'); refresh.id = 'bulk-refresh'; refresh.className = 'button'; refresh.href = `${base()}roster/${termQuery()}`; refresh.innerHTML = icon('refresh')+'查看最新名单'; q('#next-bulk').parentElement.append(refresh);
      } catch (error) {
        explain(error,'#bulk-message');
        if (error.status && error.status !== 401) bulkPending = null;
        if (error.status === 409) { preview = null; previewText = null; }
        saveButton.innerHTML = icon(bulkPending ? 'refresh' : 'save')+(bulkPending ? '重试原提交' : '确认新增'); saveButton.disabled = !bulkPending && !preview?.length;
      } finally { bulkSaving = false; setLock(!!bulkPending); }
    });
    q('#next-bulk').addEventListener('click',() => { input.value = ''; fileInput.value = ''; input.disabled = false; bulkDirty = false; q('#bulk-preview').hidden = true; q('#next-bulk').hidden = true; saveButton.disabled = true; saveButton.innerHTML = icon('save')+'确认新增'; showMessage('#bulk-message',''); input.focus(); });
  }
  function editStudent(student) {
    modalForm({title:student ? '修改当前学期学生' : '新增当前学期学生',description:'本次操作只影响当前学期名单，历史姓名、学号和成绩保持原样。',fields:`<label class="field">姓名<input name="name" value="${escape(student?.name || '')}" required maxlength="20" autocomplete="off"></label><label class="field">学号<input name="number" value="${escape(student?.number || '')}" required maxlength="20" autocomplete="off"></label><label class="field">性别<select name="sex"><option value="male" ${student?.sex !== 'female' ? 'selected' : ''}>男</option><option value="female" ${student?.sex === 'female' ? 'selected' : ''}>女</option></select></label>`,url:`${base()}roster/`,build:form => ({action:student ? 'edit' : 'add',revision:data.classroom?.revision ?? 0,student:{...(student ? {id:student.id} : {}),name:form.elements.name.value.trim(),number:form.elements.number.value.trim(),sex:form.elements.sex.value}}),onSuccess:() => location.reload()});
  }
  function removeStudent(student) {
    if (!student) return;
    modalForm({title:'移除当前学期学生',description:`确认将 ${student.name}（${student.number}）移出当前学期名单？其历史学期身份、记录及成绩保持原样。`,fields:'',submitLabel:'确认移除',danger:true,url:`${base()}roster/`,build:() => ({action:'remove',revision:data.classroom?.revision ?? 0,student:{id:student.id}}),onSuccess:() => location.reload()});
  }
  function renderCommitteeAccounts() {
    const accounts = data.committee_accounts || [], limit = number(data.committee_limit) || 5;
    const activeCount = accounts.filter(account => account.active).length;
    q('#committee-panel').innerHTML = `<div class="toolbar"><div class="section-title">${icon('key')}<h2>班委账号</h2></div>${button('新增班委账号','plus',`id="create-committee" ${activeCount >= limit ? 'disabled' : ''}`)}</div><p class="small">已启用 ${activeCount} / ${limit} 个账号。本班固定前缀 <strong>${escape(data.committee_prefix || '—')}.</strong>，每位班委独立登录，仅授权本班。</p><p class="small">班主任可按需一键复制完整登录信息。尚未开通复制的旧账号需要先重置一次密码；停用账号即使持有登录信息也不能登录。</p>${data.readonly ? '<p class="small account-history-note">当前学期业务数据只读。账号安全管理不改动历史名单与成绩。</p>' : ''}<div class="committee-accounts">${accounts.length ? accounts.map(account => `<article class="committee-account"><div class="account-heading"><div><strong>${escape(account.display_name || account.username)}</strong><span class="student-number">${escape(account.username)}</span></div><span class="badge ${account.active ? '' : 'muted'}">${icon(account.active ? 'check' : 'lock')}${account.active ? '已启用' : '已停用'}</span></div><div class="account-actions">${button('一键复制登录信息','copy',`data-account-copy="${escape(account.id)}" ${account.credentials_available ? '' : 'disabled'}`)}${button('修改名称','edit',`data-account-action="update" data-account-id="${escape(account.id)}"`)}${button('重置密码','key',`data-account-action="reset_password" data-account-id="${escape(account.id)}"`)}${button(account.active ? '停用账号' : '启用账号',account.active ? 'lock' : 'check',`data-account-action="${account.active ? 'deactivate' : 'activate'}" data-account-id="${escape(account.id)}" ${!account.active && activeCount >= limit ? 'disabled' : ''}`,account.active ? 'danger' : '')}</div>${!account.credentials_available ? '<p class="small" style="margin-top:10px">请先重置一次密码，即可复制登录信息。</p>' : ''}</article>`).join('') : empty('尚未创建班委账号','为需要录入的班委分配独立账号与密码。','users')}</div>`;
    q('#create-committee').addEventListener('click',() => manageCommittee('create'));
    qa('[data-account-action]').forEach(el => el.addEventListener('click',() => manageCommittee(el.dataset.accountAction,accounts.find(account => String(account.id) === el.dataset.accountId))));
    qa('[data-account-copy]').forEach(el => el.addEventListener('click',() => copyAccountCredentials(number(el.dataset.accountCopy),el)));
  }
  function credentialText(credentials) {
    return `笃行 · 学生操行管理系统\n班级：${credentials.class_name || data.classroom?.name || ''}\n班委用户名：${credentials.username}\n密码：${credentials.password}\n登录后仅可操作本班，授权有效3小时。${credentials.active === false ? '\n账号当前已停用，启用后方可登录。' : ''}`;
  }
  async function copyText(text) {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard_unavailable');
      await navigator.clipboard.writeText(text); toast('完整登录信息已复制。');
    } catch (_) {
      const dialog = q('#copy-dialog');
      dialog.innerHTML = `<div class="section-title">${icon('copy')}<h2 id="copy-title">手动复制登录信息</h2></div><p>浏览器未允许自动写入剪贴板。请复制下方已选中的完整文本。</p><label class="field">完整登录信息<textarea id="manual-copy-text" rows="7" readonly spellcheck="false"></textarea></label><div class="actions">${button('重新全选','copy','id="select-copy-text"')}${button('关闭','close','id="close-copy-dialog"')}</div>`;
      q('#manual-copy-text').value = text;
      if (!dialog.open) dialog.showModal();
      const select = () => { q('#manual-copy-text').focus(); q('#manual-copy-text').select(); };
      q('#select-copy-text').addEventListener('click',select); q('#close-copy-dialog').addEventListener('click',() => dialog.close()); select();
    }
  }
  async function copyAccountCredentials(id,buttonElement) {
    if (buttonElement) buttonElement.disabled = true;
    let credentials = null;
    try { credentials = await request(`${base()}committee/credentials/`,{id}); await copyText(credentialText(credentials)); }
    catch (error) { toast(error.message); if (error.status === 401) openAuth(); }
    finally { if (credentials) credentials.password = ''; if (buttonElement) buttonElement.disabled = false; }
  }
  let credentialReceipt = null;
  function showCredentialReceipt(receipt,password) {
    const dialog = q('#credential-dialog');
    credentialReceipt = {id:receipt.id,username:receipt.username,password:receipt.credentials_current ? password : null,class_name:data.classroom?.name,active:true};
    dialog.innerHTML = `<div class="section-title">${icon('check')}<h2 id="credential-title">账号信息已保存</h2></div><p>${receipt.credentials_current ? '可将用户名、密码及使用说明合并复制。以后也可在班委账号列表中再次复制。' : '这次提交后，账号凭据已被后续操作更新。请复制当前登录信息，勿使用旧密码。'}</p><label class="field">完整用户名<input id="receipt-username" readonly></label>${receipt.credentials_current ? '<label class="field">本次设置的密码<input id="receipt-password" type="text" readonly autocomplete="off"></label>' : ''}<div class="actions">${button('一键复制登录信息','copy','id="copy-receipt"','primary')}${button('关闭','close','id="close-receipt"')}</div>`;
    q('#receipt-username').value = receipt.username;
    if (q('#receipt-password')) q('#receipt-password').value = password;
    q('#copy-receipt').addEventListener('click',() => { if (!credentialReceipt) return; if (credentialReceipt.password !== null) copyText(credentialText(credentialReceipt)); else copyAccountCredentials(receipt.id,q('#copy-receipt')); });
    q('#close-receipt').addEventListener('click',() => dialog.close()); dialog.showModal();
  }
  q('#credential-dialog').addEventListener('close',() => { if (credentialReceipt) credentialReceipt.password = ''; credentialReceipt = null; qa('input,textarea',q('#credential-dialog')).forEach(el => el.value=''); q('#credential-dialog').replaceChildren(); });
  q('#copy-dialog').addEventListener('close',() => { qa('input,textarea',q('#copy-dialog')).forEach(el => el.value=''); q('#copy-dialog').replaceChildren(); });
  function manageCommittee(action, account = null) {
    const passwordFields = '<label class="field">账号密码<input name="password" type="password" required minlength="8" maxlength="128" autocomplete="new-password"><span class="field-hint">8–128位，避免纯数字和常见密码。</span></label><label class="field">再次输入密码<input name="confirm" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></label>';
    const configs = {
      create:{title:'新增班委账号',description:'账号固定绑定当前班级。输入短用户名，系统自动添加本班前缀；创建后不可更改。',fields:`<label class="field">用户名<div class="username-composer"><span aria-label="本班固定前缀">${escape(data.committee_prefix || '')}.</span><input name="username" required minlength="3" maxlength="40" pattern="[A-Za-z0-9._\\-]{3,40}" autocomplete="off" placeholder="例如：linmu"></div><span class="field-hint">短名3–40位，可用英文字母、数字或 . _ -；跨班可使用相同短名。</span></label><p class="small username-preview">完整登录用户名：<strong id="full-username-preview">${escape(data.committee_prefix || '')}.…</strong></p><label class="field">显示名称<input name="display_name" required maxlength="40" autocomplete="off" placeholder="例如：林沐 · 班长"></label>${passwordFields}`,submitLabel:'创建账号'},
      update:{title:'修改显示名称',description:`账号 ${account?.username || ''} 的用户名和所属班级保持不变。`,fields:`<label class="field">显示名称<input name="display_name" value="${escape(account?.display_name || '')}" required maxlength="40" autocomplete="off"></label>`,submitLabel:'保存名称'},
      reset_password:{title:'重置班委密码',description:`为 ${account?.display_name || account?.username || ''} 设置新密码。该账号已有授权将失效，需要重新登录。`,fields:passwordFields,submitLabel:'重置密码'},
      deactivate:{title:'停用班委账号',description:`确认停用 ${account?.display_name || account?.username || ''}？该账号无法继续登录或写入，历史操作日志保留。`,fields:'',submitLabel:'确认停用'},
      activate:{title:'启用班委账号',description:`重新启用 ${account?.display_name || account?.username || ''}，仍仅授权当前班级。`,fields:'',submitLabel:'确认启用'}
    };
    const config = configs[action]; if (!config || (action !== 'create' && !account)) return;
    modalForm({...config,danger:action === 'deactivate',url:`${base()}committee/`,build:form => {
      const payload = {action,...(account ? {id:account.id,revision:account.revision} : {})};
      if (action === 'create') payload.username = form.elements.username.value.trim();
      if (action === 'create' || action === 'update') payload.display_name = form.elements.display_name.value.trim();
      if (action === 'create' || action === 'reset_password') { if (form.elements.password.value !== form.elements.confirm.value) throw new Error('两次输入的密码不一致。'); payload.password = form.elements.password.value; }
      return payload;
    },onReady:form => { if (action === 'create') form.elements.username.addEventListener('input',() => { q('#full-username-preview').textContent = `${data.committee_prefix}.${form.elements.username.value.trim().toLowerCase() || '…'}`; }); },onSuccess:(result,submitted) => { data.committee_accounts = result.accounts; data.committee_limit = result.limit || 5; data.committee_prefix = result.prefix || data.committee_prefix; renderCommitteeAccounts(); if (['create','reset_password'].includes(action) && result.receipt) showCredentialReceipt(result.receipt,submitted.password); else toast('班委账号设置已保存。'); }});
  }
  function eventsPage() {
    const eventLabels = {record_created:'新增记录',record_updated:'修改记录',record_deleted:'删除记录',roster_add:'新增学生',roster_edit:'修改名单',roster_remove:'移除学生',class_data_cleared:'清空数据',class_students_cleared:'清空学生',rules_updated:'评分规则更新',committee_create:'新增班委账号',committee_update:'修改班委名称',committee_reset_password:'班委密码重置',committee_deactivate:'停用班委账号',committee_activate:'启用班委账号',committee_credentials_read:'读取登录信息用于复制',class_created:'创建班级',class_archived:'归档班级'};
    // Normalize only the old system-generated roster label; audit storage stays immutable.
    const eventSummary = event => event.kind === 'roster_add' ? String(event.summary).replace(/^(批量)?补录(?=\d+名学生)/,'$1新增') : event.summary;
    const events = data.events || []; const currentKind = new URL(location.href).searchParams.get('kind') || '';
    root.innerHTML = heading('全学期操作记录',`${data.classroom?.name || '班级'} · 汇集所有学期的重要操作，仅班主任可查看`,'history','', '班级 · 操作记录') +
      `<div class="notice">${icon('history')}<div><strong>本页不按上方学期筛选</strong><p>展示该班所有学期的概要日志，仅供查看。班委操作按具体账号记录；早期未绑定账号的日志保留原有身份标记。</p></div></div><div class="toolbar"><div class="section-title">${icon('history')}<h2>操作时间线</h2></div><label class="inline-field">事件类型<select id="event-kind"><option value="">全部类型</option>${Object.entries(eventLabels).map(([key,label]) => `<option value="${key}" ${key === currentKind ? 'selected' : ''}>${label}</option>`).join('')}</select></label></div>` +
      (events.length ? `<ul class="timeline">${events.map(event => `<li><small>${escape(displayTime(event.time))} · ${escape(event.actor_label || '班委')}</small><p>${escape(eventSummary(event))}</p><div class="event-meta">${escape(eventLabels[event.kind] || event.kind || '业务变更')}${event.affected_count !== undefined ? ` · 影响 ${number(event.affected_count)} 人` : ''}${event.source ? ` · ${escape(({web:'网页',admin:'后台',agent:'Agent'})[event.source] || event.source)}` : ''}</div></li>`).join('')}</ul>${pagination()}` : empty('暂无概要事件',currentKind ? '该类型当前没有可展示的事件。' : '新增记录、名单变化、评分调整及密码重设将在这里留下摘要。','history'));
    q('#event-kind').addEventListener('change',event => { const url = new URL(location.href); if (event.target.value) url.searchParams.set('kind',event.target.value); else url.searchParams.delete('kind'); url.searchParams.delete('page'); location.assign(url.pathname+url.search); });
  }
  function displayTime(value) {
    if (!value) return '';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
  }
  function loginPage() {
    const role = (data.login_role || new URL(location.href).searchParams.get('role')) === 'committee' ? 'committee' : 'owner';
    const committee = role === 'committee';
    root.innerHTML = `<div class="auth-layout"><div class="auth-intro"><div class="eyebrow">笃行 · 学生操行管理系统</div><div class="title-line">${icon('book')}<h1>日常有序，<br>成长有迹</h1></div><p>将考勤、活动与纪律清楚记录，<br>让每一步成长都有据可循。</p></div><section class="auth-form"><div class="login-roles" role="group" aria-label="选择登录身份"><button class="type-button" type="button" data-login-role="owner" aria-pressed="${!committee}">${icon('book')}班主任</button><button class="type-button" type="button" data-login-role="committee" aria-pressed="${committee}">${icon('users')}班委</button></div><div class="section-title">${icon('login')}<h2>${committee ? '班委账号登录' : '班主任登录'}</h2></div><form id="login-form"><label class="field">${committee ? '班委账号' : '账号'}<input name="username" required autocomplete="username" maxlength="${committee ? '45' : '150'}" ${committee ? 'minlength="8" pattern="[0-9A-Fa-f]{4}\\.[A-Za-z0-9._\\-]{3,40}"' : ''}></label><label class="field">密码<input name="password" type="password" required maxlength="128" autocomplete="current-password"></label>${button('登录工作台','login','type="submit"','primary')}<p id="login-message" class="form-message" role="alert"></p></form><p class="small">${committee ? '输入班主任提供的完整用户名（如 a1b2.linmu）。登录后仅可操作所属班级，授权固定3小时有效。' : '管理本人班级、学生名单、评分规则与班委账号。'}</p></section></div>`;
    qa('[data-login-role]').forEach(el => el.addEventListener('click',() => { data.login_role = el.dataset.loginRole; loginPage(); q('#login-form input').focus(); }));
    q('#login-form').addEventListener('submit',async event => {
      event.preventDefault(); const form = event.currentTarget, submit = q('[type=submit]',form); submit.disabled = true;
      qa('[data-login-role]').forEach(el => el.disabled = true);
      try { const result = await request('/login/',{role,username:form.elements.username.value.trim(),password:form.elements.password.value}); location.assign(result.url || '/'); }
      catch (error) { showMessage('#login-message',error.message,true); }
      finally { submit.disabled = false; qa('[data-login-role]').forEach(el => el.disabled = false); }
    });
  }
  function authorizePage() {
    data.login_role = 'committee'; loginPage();
  }
  function errorPage() {
    const error = data.error || {}, code = String(error.code || '');
    const title = error.title || (['401','unauthorized','authorization_expired'].includes(code) ? '授权已失效' : ['403','forbidden'].includes(code) ? '当前身份无法查看此页' : ['404','not_found'].includes(code) ? '没有找到这项内容' : '暂时无法完成操作');
    root.innerHTML = `<section class="error-panel">${icon('warning')}<div class="eyebrow">${escape(code || '笃行')}</div><h1>${escape(title)}</h1><p>${escape(error.message || '请返回工作台，确认班级与当前授权后重新进入。')}</p><div class="actions">${link('返回工作台','home','/')}${data.actor?.role === 'anonymous' ? link('班主任登录','login','/login/') : ''}</div></section>`;
  }
  function hasUnsavedChanges() { return modalDirty || modalPending || modalBusy || policyDirty || policyPending || policySaving || bulkDirty || bulkPending || bulkSaving || Array.from(drafts.values()).some(draft => draft.dirty || draft.pending || draft.saving); }
  window.addEventListener('beforeunload',event => { if (hasUnsavedChanges()) { event.preventDefault(); event.returnValue = ''; } });
  function navigateFromRules(url, restore = () => {}) {
    const recordChanged = page === 'record' && Array.from(drafts.values()).some(draft => draft.dirty || draft.pending || draft.saving);
    const rosterChanged = page === 'roster' && (bulkDirty || bulkPending || bulkSaving);
    const ruleChanged = page === 'rules' && (policyDirty || policyPending || policySaving);
    if (!recordChanged && !rosterChanged && !ruleChanged) { location.assign(url); return; }
    if (modalBusy || modalPending || policySaving || policyPending || bulkSaving || bulkPending || Array.from(drafts.values()).some(draft => draft.saving || draft.pending)) {
      restore();
      showMessage(modalBusy || modalPending ? '#action-message' : page === 'record' ? '#entry-message' : page === 'rules' ? '#policy-message' : '#roster-message','提交结果尚未确认，请先核对或重试原提交。',true);
      return;
    }
    const dialog = q('#action-dialog');
    if (dialog.open) { restore(); return; }
    const description = ruleChanged ? '离开将放弃本页规则修改。新设置只有保存后才会从当前学期开始生效；历史学期不受影响。' : '离开将丢失当前尚未保存的输入。请确认已保存，或继续编辑。';
    dialog.innerHTML = `<div class="section-title">${icon('warning')}<h2 id="action-title">尚有未保存的输入</h2></div><p>${description}</p><div class="actions">${button('继续编辑','edit','id="keep-policy" type="button"')}${button('放弃输入并离开','arrow','id="discard-policy" type="button"','primary')}</div>`;
    const stay = () => { restore(); dialog.close(); };
    q('#keep-policy',dialog).addEventListener('click',stay);
    dialog.oncancel = event => { event.preventDefault(); stay(); };
    q('#discard-policy',dialog).addEventListener('click',() => { policyDirty = false; bulkDirty = false; drafts.clear(); dialog.close(); location.assign(url); });
    dialog.showModal(); q('#keep-policy',dialog).focus();
  }
  document.addEventListener('click',event => {
    if (!hasUnsavedChanges() || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const anchor = event.target.closest('a[href]');
    if (!anchor || anchor.hasAttribute('download') || (anchor.target && anchor.target !== '_self')) return;
    const url = new URL(anchor.href,location.href);
    if (url.origin !== location.origin || (url.pathname === location.pathname && url.search === location.search && url.hash)) return;
    event.preventDefault(); navigateFromRules(url.href);
  },true);
  q('form[action="/logout/"]')?.addEventListener('submit',async event => {
    event.preventDefault();
    if (modalBusy || modalPending || policySaving || policyPending || bulkSaving || bulkPending || Array.from(drafts.values()).some(draft => draft.saving || draft.pending)) { toast('提交结果尚未确认，请先核对或重试原提交。'); return; }
    if (hasUnsavedChanges() && !confirm('本页有尚未保存的输入。确认退出并放弃这些草稿吗？')) return;
    const submit = q('button',event.currentTarget); submit.disabled = true;
    try { const result = await request('/logout/',{}); drafts.clear(); policyDirty = false; bulkDirty = false; bulkPending = null; location.assign(result.url || '/'); }
    catch (error) { toast(error.message); submit.disabled = false; }
  });
  q('#class-switch')?.addEventListener('change',event => {
    const code = event.target.value;
    if (page === 'record') { switchRecordClass(code); return; }
    const paths = {roster:'roster/',events:'events/'};
    navigateFromRules(code ? base(code)+(paths[page] || '')+termQuery() : '/'+termQuery(),() => { event.target.value = data.classroom?.code || ''; });
  });
  q('#term-switch')?.addEventListener('change',event => {
    const url = new URL(location.href); url.searchParams.set('term',event.target.value); url.searchParams.delete('page'); url.searchParams.delete('month'); url.searchParams.delete('date');
    if (page === 'record') { navigateFromRules(base()+url.search,() => { event.target.value = data.term?.key || ''; }); return; }
    navigateFromRules(url.pathname+url.search,() => { event.target.value = data.term?.key || ''; });
  });
  hydrateIcons(); updateExpiry();
  const pages = {dashboard:dashboardPage,class:classPage,august:augustPage,record:recordPage,student:studentPage,rules:rulesPage,roster:rosterPage,events:eventsPage,login:loginPage,authorize:authorizePage,error:errorPage};
  (pages[page] || errorPage)(); root.setAttribute('aria-busy','false');
  setInterval(updateExpiry,60000);
})();
