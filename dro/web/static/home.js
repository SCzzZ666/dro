const $ = (id) => document.getElementById(id);
const state = { threadId: null, timer: null, es: null, uploadedPath: null, history: [], lastProducts: [] };

const STATUS = {
  done: ['ok', '已完成'],
  succeeded: ['ok', '已完成'],
  success: ['ok', '已完成'],
  failed: ['err', '失败'],
  error: ['err', '失败'],
  cancelled: ['warn', '已停止'],
  stopped: ['warn', '已停止'],
  running: ['run', '执行中'],
  queued: ['run', '排队中'],
  pending: ['run', '排队中'],
  created: ['run', '已创建'],
};

function statusMeta(status) {
  const s = (status || '').toLowerCase();
  return STATUS[s] || ['neutral', status || '未知'];
}

function safeJson(obj) {
  try { return JSON.stringify(obj, null, 2); } catch { return String(obj); }
}

function escapeHtml(str) {
  return String(str ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

function priceText(price) {
  if (price === null || price === undefined || price === '') return '—';
  const n = Number(price);
  return Number.isFinite(n) ? `¥${n.toFixed(2)}` : String(price);
}

function fmtTime(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false });
}

function download(name, text, mime) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}

/* ---------- 图片上传 ---------- */
async function uploadImage(file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch('/v2/upload', { method: 'POST', body: fd });
  if (!res.ok) { const err = await res.json().catch(() => ({})); throw new Error(err.detail || '上传失败'); }
  return await res.json();
}

function showPreview(file) {
  $('previewImg').src = URL.createObjectURL(file);
  $('uploadPreview').style.display = 'block';
  $('uploadText').textContent = file.name;
}

$('fileInput').addEventListener('change', async (e) => {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  showPreview(file);
  try {
    const data = await uploadImage(file);
    state.uploadedPath = data.path;
    $('uploadText').textContent = file.name + ' ✓ 已上传';
  } catch (err) {
    alert(err.message);
    state.uploadedPath = null;
  }
});

$('clearImg').addEventListener('click', () => {
  state.uploadedPath = null;
  $('uploadPreview').style.display = 'none';
  $('fileInput').value = '';
  $('uploadText').textContent = '点击或拖拽上传图片';
});

['dragover', 'dragleave', 'drop'].forEach((evt) => {
  $('uploadZone').addEventListener(evt, (e) => { e.preventDefault(); e.stopPropagation(); });
});
$('uploadZone').addEventListener('dragover', () => $('uploadZone').classList.add('drag'));
$('uploadZone').addEventListener('dragleave', () => $('uploadZone').classList.remove('drag'));
$('uploadZone').addEventListener('drop', (e) => {
  $('uploadZone').classList.remove('drag');
  const file = e.dataTransfer.files && e.dataTransfer.files[0];
  if (file) $('fileInput').files = e.dataTransfer.files;
  if (file) $('fileInput').dispatchEvent(new Event('change'));
});

/* ---------- 任务操作 ---------- */
async function createTask() {
  const user_input = $('userInput').value.trim();
  if (!user_input) { alert('请输入任务需求'); return; }
  const payload = {
    user_input,
    task_type: $('taskType').value,
    image_path: state.uploadedPath || null,
  };
  const res = await fetch('/v2/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  state.threadId = data.thread_id;
  $('taskLog').textContent = safeJson(data);
  setStatusBar('created');
  startStream();
  await pollTask();
}

async function pollTask() {
  if (!state.threadId) return;
  try {
    const res = await fetch(`/v2/tasks/${state.threadId}`);
    if (!res.ok) {
      $('taskLog').textContent = `任务 ${state.threadId} 暂无状态，可能仍在排队或尚未写入 checkpoint。`;
      return;
    }
    const task = await res.json();
    renderTask(task);
    if (['done', 'failed', 'cancelled'].includes((task.status || '').toLowerCase())) stopPoll();
  } catch (e) {
    $('taskLog').textContent = `轮询失败：${e}`;
  }
}

/* SSE 优先，失败回退 1500ms 轮询 */
function startStream() {
  if (!state.threadId) return;
  stopPoll();
  if (!window.EventSource) { startPoll(); return; }
  try {
    const es = new EventSource(`/v2/tasks/${encodeURIComponent(state.threadId)}/events`);
    state.es = es;
    es.addEventListener('update', (e) => { renderTask(JSON.parse(e.data)); });
    es.addEventListener('done', () => { es.close(); state.es = null; });
    es.onerror = () => { es.close(); state.es = null; startPoll(); };
  } catch { startPoll(); }
}

function startPoll() {
  if (state.timer) clearInterval(state.timer);
  state.timer = setInterval(pollTask, 1500);
}

function stopPoll() {
  if (state.timer) { clearInterval(state.timer); state.timer = null; }
  if (state.es) { state.es.close(); state.es = null; }
}

async function stopTask() {
  if (!state.threadId) return alert('请先创建任务');
  const res = await fetch(`/v2/tasks/${state.threadId}/stop`, { method: 'POST' });
  const data = await res.json();
  $('taskLog').textContent = safeJson(data);
  await pollTask();
}

function setStatusBar(status) {
  const [cls, text] = statusMeta(status);
  $('statusBar').className = 'status-bar ' + cls;
  $('statusText').textContent = text;
  $('resultStatus').textContent = text;
}

function renderTask(task) {
  if (!task) return;
  setStatusBar(task.status);
  $('detailThread').textContent = task.thread_id || '—';
  $('metaType').textContent = task.task_type || '自动识别';
  $('metaStep').textContent = task.step_count ?? 0;
  $('metaProducts').textContent = (task.products || []).length;
  $('metaTime').textContent = fmtTime(task.updated_at);
  $('taskLog').textContent = (task.next && task.next.length ? `next: ${task.next.join(' → ')}\n\n` : '') + safeJson(task);

  const summary = $('resultSummary');
  if (task.final_reply) {
    summary.className = 'result-reply';
    summary.textContent = task.final_reply;
  } else {
    summary.className = 'empty';
    summary.textContent = '任务尚未输出结果，请稍候…';
  }

  const grid = $('resultGrid');
  grid.innerHTML = '';
  const products = Array.isArray(task.products) ? task.products : [];
  state.lastProducts = products;
  for (const p of products) {
    const el = document.createElement('div');
    el.className = 'product';
    el.innerHTML = `
      <div class="product-top">
        <div class="product-name">${escapeHtml(p.name || '未命名商品')}</div>
        <div class="product-price">${escapeHtml(priceText(p.price))}</div>
      </div>
      <div class="product-meta">
        平台：${escapeHtml(p.platform || '—')} · 店铺：${escapeHtml(p.shop_name || '—')}<br/>
        销量：${escapeHtml(p.sales || '—')} · 评分：${escapeHtml(p.rating || '—')}
        ${p.reason ? '<br/>推荐理由：' + escapeHtml(p.reason) : ''}
        ${p.link ? '<br/><a class="open-link" href="' + escapeHtml(p.link) + '" target="_blank" rel="noreferrer">打开链接 ↗</a>' : ''}
      </div>`;
    grid.appendChild(el);
  }
  addHistory(task);
}

/* ---------- 历史记录 ---------- */
function addHistory(task) {
  const item = {
    thread_id: task.thread_id,
    task_type: task.task_type || '',
    user_input: task.user_input || '',
    status: task.status || '',
    updated_at: task.updated_at || Date.now() / 1000,
    final_reply: task.final_reply || '',
  };
  const list = state.history.filter((x) => x.thread_id !== item.thread_id);
  list.unshift(item);
  state.history = list.slice(0, 20);
  renderHistory();
  renderStats();
}

function renderHistory() {
  $('historyCount').textContent = `${state.history.length} 条`;
  const box = $('historyList');
  if (!state.history.length) {
    box.innerHTML = '<div class="empty">暂无任务记录</div>';
    return;
  }
  box.innerHTML = '';
  state.history.forEach((item) => {
    const [cls, txt] = statusMeta(item.status);
    const el = document.createElement('div');
    el.className = 'history-item' + (item.thread_id === state.threadId ? ' active' : '');
    el.innerHTML = `
      <div class="history-top">
        <span class="history-title">${escapeHtml(item.task_type || '自动识别')}</span>
        <span class="badge ${cls}">${escapeHtml(txt)}</span>
      </div>
      <div class="history-sub">${escapeHtml(item.user_input || '—')}</div>`;
    el.addEventListener('click', () => {
      state.threadId = item.thread_id;
      renderHistory();
      startStream();
      pollTask();
    });
    box.appendChild(el);
  });
}

function renderStats() {
  let total = 0, running = 0, done = 0;
  state.history.forEach((t) => {
    total += 1;
    const s = (t.status || '').toLowerCase();
    if (['done', 'succeeded', 'success'].includes(s)) done += 1;
    else if (!['failed', 'cancelled', 'stopped', 'error'].includes(s)) running += 1;
  });
  $('statTotal').textContent = total;
  $('statRunning').textContent = running;
  $('statDone').textContent = done;
}

/* ---------- 服务与工具 ---------- */
async function loadHealth() {
  try {
    const res = await fetch('/healthz');
    const data = await res.json();
    const ok = data.status === 'ok';
    $('healthDot').className = 'status-dot ' + (ok ? 'on' : 'off');
    $('healthText').textContent = ok ? '服务在线' : '服务异常';
  } catch {
    $('healthDot').className = 'status-dot off';
    $('healthText').textContent = '服务离线';
  }
}

async function loadTools() {
  const box = $('toolList');
  try {
    const res = await fetch('/v2/tools');
    const data = await res.json();
    const names = Object.keys(data);
    $('toolCount').textContent = `${names.length} 个`;
    box.innerHTML = '';
    for (const [name, tool] of Object.entries(data)) {
      const el = document.createElement('div');
      el.className = 'tool-item';
      el.innerHTML = `<h4>${escapeHtml(name)}</h4><p>${escapeHtml(tool.description || '')}</p>`;
      box.appendChild(el);
    }
  } catch {
    box.innerHTML = '<div class="empty">工具加载失败</div>';
  }
}

/* 导出结果 */
function exportResult(fmt) {
  const products = state.lastProducts || [];
  if (!products.length) { alert('暂无结果可导出'); return; }
  if (fmt === 'json') {
    download('result.json', JSON.stringify(products, null, 2), 'application/json');
  } else {
    const head = ['名称', '价格', '平台', '销量', '评分', '推荐理由'];
    const rows = products.map((p) => [p.name || '', p.price ?? '', p.platform || '', p.sales || '', p.rating || '', p.reason || '']);
    const csv = [head, ...rows].map((r) => r.map((c) => `"${String(c ?? '').replaceAll('"', '""')}"`).join(',')).join('\n');
    download('result.csv', '﻿' + csv, 'text/csv;charset=utf-8');
  }
}

$('createBtn').addEventListener('click', createTask);
$('refreshBtn').addEventListener('click', pollTask);
$('stopBtn').addEventListener('click', stopTask);
$('exportJsonBtn').addEventListener('click', () => exportResult('json'));
$('exportCsvBtn').addEventListener('click', () => exportResult('csv'));

loadHealth();
loadTools();
