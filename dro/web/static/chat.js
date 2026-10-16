const $ = (id) => document.getElementById(id);
const session = {
  id: null,           // 当前会话
  pollTimer: null,
  es: null,           // EventSource（SSE）
  cursor: 0,
  activeTurn: false,
  attached: null,     // {path, url}
  progressOpen: false,
  progressBubble: null,
};

/* ---------------- 工具 ---------------- */
function esc(s) {
  return String(s ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')
    .replaceAll('"','&quot;').replaceAll("'",'&#39;');
}
function fmtTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return `${d.getMonth()+1}/${d.getDate()} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
}
function priceText(p) {
  if (p === null || p === undefined || p === '') return '—';
  const n = Number(p);
  return Number.isFinite(n) && n > 0 ? `¥${n.toFixed(2)}` : String(p ?? '—');
}
async function api(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || res.statusText); }
  return res.json();
}
function download(name, text, mime) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/* ---------------- 消息渲染 ---------------- */
function addRow(role, html) {
  const wrap = document.createElement('div');
  wrap.className = 'row ' + role;
  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = role === 'user' ? '我' : 'Dro';
  const bw = document.createElement('div');
  bw.className = 'bubble-wrap';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerHTML = html;
  bw.appendChild(bubble);
  wrap.appendChild(avatar);
  wrap.appendChild(bw);
  $('msgList').appendChild(wrap);
  scrollDown();
  return bubble;
}

function addProgressBubble() {
  const wrap = document.createElement('div');
  wrap.className = 'row assistant';
  const avatar = document.createElement('div');
  avatar.className = 'avatar'; avatar.textContent = 'Dro';
  const pb = document.createElement('div');
  pb.className = 'progress-bubble';
  pb.innerHTML = '<div class="step waiting">⏳ 正在准备执行…</div>';
  const inner = document.createElement('div');
  inner.className = 'bubble-wrap';
  inner.appendChild(pb);
  wrap.appendChild(avatar); wrap.appendChild(inner);
  $('msgList').appendChild(wrap);
  scrollDown();
  return pb;
}

function productCard(p) {
  const reason = (p.reason || '').trim();
  const over = reason.includes('超出预算');
  const winner = p.is_winner ? ' winner' : '';
  const rank = p.rank ? `<span style="color:var(--muted);font-size:11px;">#${p.rank}</span>` : '';
  const link = p.link
    ? `<div class="actions"><a href="${esc(p.link)}" target="_blank" rel="noreferrer">打开链接 ↗</a></div>`
    : '';
  return `<div class="product${winner}">
    <div class="name">${esc(p.name || '未命名')} ${rank}</div>
    <div class="price">${esc(priceText(p.price))}</div>
    <div class="meta">
      平台：${esc(p.platform || '—')}
      ${p.shop_name ? ' · 店铺：' + esc(p.shop_name) : ''}<br/>
      销量：${esc(p.sales || '—')} · 评分：${esc(p.rating || '—')}
    </div>
    ${reason ? `<div class="reason ${over ? 'over' : ''}">${esc(reason)}</div>` : ''}
    ${link}
  </div>`;
}

function resultToolbar(products) {
  const wrap = document.createElement('div');
  wrap.className = 'result-toolbar';
  const jsonBtn = document.createElement('button');
  jsonBtn.className = 'btn'; jsonBtn.textContent = '导出 JSON';
  jsonBtn.onclick = () => download('compare.json', JSON.stringify(products, null, 2), 'application/json');
  const csvBtn = document.createElement('button');
  csvBtn.className = 'btn'; csvBtn.textContent = '导出 CSV';
  csvBtn.onclick = () => {
    const head = ['名称', '价格', '平台', '销量', '评分', '推荐理由'];
    const rows = products.map((p) => [p.name || '', p.price ?? '', p.platform || '', p.sales || '', p.rating || '', p.reason || '']);
    const csv = [head, ...rows].map((r) => r.map((c) => `"${String(c ?? '').replaceAll('"', '""')}"`).join(',')).join('\n');
    download('compare.csv', '﻿' + csv, 'text/csv;charset=utf-8');
  };
  const checkout = document.createElement('button');
  checkout.className = 'btn'; checkout.textContent = '代下单';
  checkout.title = '代下单需二次确认，系统不会自动点击支付';
  checkout.onclick = () => {
    const note = document.createElement('div');
    note.className = 'checkout-card';
    note.textContent = '代下单为高风险操作：需在前端二次确认后由人工完成支付，系统不会自动点击「提交订单/立即支付」。';
    wrap.appendChild(note);
    checkout.disabled = true;
  };
  wrap.appendChild(jsonBtn); wrap.appendChild(csvBtn); wrap.appendChild(checkout);
  return wrap;
}

function renderAssistantTurn(turn) {
  const texts = (turn.text || '').split('\n');
  if (turn.text) {
    addRow('assistant', esc(turn.text).replace(/\n/g, '<br/>'));
  } else if (turn.status === 'failed') {
    addRow('assistant', '<span style="color:var(--danger)">任务执行出错，请稍后重试。</span>');
  }

  if (turn.choices && turn.choices.length) {
    const wrap = document.createElement('div');
    wrap.className = 'row assistant';
    const avatar = document.createElement('div'); avatar.className = 'avatar'; avatar.textContent = 'Dro';
    const bw = document.createElement('div'); bw.className = 'bubble-wrap';
    const c = document.createElement('div'); c.className = 'choices';
    for (const ch of turn.choices) {
      const b = document.createElement('button'); b.className = 'choice';
      b.textContent = ch; b.onclick = () => sendMessage(ch);
      c.appendChild(b);
    }
    bw.appendChild(c); wrap.appendChild(avatar); wrap.appendChild(bw);
    $('msgList').appendChild(wrap);
  }

  if (turn.products && turn.products.length) {
    const wrap = document.createElement('div');
    wrap.className = 'row assistant';
    const avatar = document.createElement('div'); avatar.className = 'avatar'; avatar.textContent = 'Dro';
    const bw = document.createElement('div'); bw.className = 'bubble-wrap';
    const grid = document.createElement('div'); grid.className = 'product-grid';
    for (const p of turn.products) {
      const card = document.createElement('div');
      card.innerHTML = productCard(p);
      grid.appendChild(card.firstElementChild);
    }
    bw.appendChild(grid);
    bw.appendChild(resultToolbar(turn.products));
    wrap.appendChild(avatar); wrap.appendChild(bw);
    $('msgList').appendChild(wrap);
  }
  scrollDown();
}

function scrollDown() {
  const el = $('msgScroll');
  el.scrollTop = el.scrollHeight;
}

/* ---------------- 历史加载 ---------------- */
async function loadHistory(sid) {
  const data = await api(`/v2/chat/${encodeURIComponent(sid)}/history`);
  $('msgList').innerHTML = '';
  const items = data.messages || [];
  if (!items.length) {
    const tip = document.createElement('div');
    tip.className = 'empty-tip'; tip.innerHTML = '<div class="big">🛍️</div><div>开始新的对话吧</div>';
    $('msgList').appendChild(tip);
    return;
  }
  for (const m of items) {
    if (m.role === 'user') addRow('user', esc(m.content));
    else renderAssistantTurn({ text: m.content });
  }
}

async function openSession(sid, preview) {
  stopPoll();
  session.id = sid;
  session.cursor = 0;
  session.activeTurn = false;
  session.progressOpen = false;
  $('chatTitle').textContent = '会话 ' + (preview ? preview : sid.slice(-6));
  $('chatSub').textContent = sid;
  markActive(sid);
  try { await loadHistory(sid); } catch { /* 空会话 */ }
}

function newChat() {
  stopPoll();
  session.id = null;
  session.cursor = 0;
  session.activeTurn = false;
  session.progressOpen = false;
  session.attached = null;
  $('attachPreview').style.display = 'none';
  $('chatTitle').textContent = '新的会话';
  $('chatSub').textContent = '向助手描述你的购物需求';
  $('msgList').innerHTML = '<div class="empty-tip" id="welcome"><div class="big">🛍️</div><div>你好，我是你的购物客服助手。<br/>可以帮你比价、找相似款、按预算推荐商品。</div></div>';
  markActive(null);
}

/* ---------------- 会话侧栏 ---------------- */
function markActive(sid) {
  document.querySelectorAll('.session-item').forEach((el) => {
    el.classList.toggle('active', el.dataset.sid === sid);
  });
}
async function refreshSessions() {
  try {
    const data = await api('/v2/chat/sessions');
    const box = $('sessionList');
    box.innerHTML = '';
    if (!data.sessions || !data.sessions.length) {
      box.innerHTML = '<div class="empty-tip" style="padding:24px 10px;">暂无历史会话</div>';
      return;
    }
    for (const s of data.sessions) {
      const el = document.createElement('div');
      el.className = 'session-item' + (s.session_id === session.id ? ' active' : '');
      el.dataset.sid = s.session_id;
      const preview = s.last_content || '（空）';
      el.innerHTML = `<div class="t">${esc(preview.slice(0, 26))}</div>
                      <div class="s">${esc(fmtTime(s.last_ts))} · ${s.message_count} 条消息</div>`;
      el.onclick = () => openSession(s.session_id, preview);
      box.appendChild(el);
    }
  } catch (e) { /* 忽略 */ }
}

/* ---------------- 发送 & 实时进度 ---------------- */
async function sendMessage(textOverride) {
  const raw = (textOverride !== undefined ? textOverride : $('msgInput').value).trim();
  if (!raw) return;
  const hasImg = !!session.attached;
  $('msgInput').value = '';
  if (session.activeTurn) return;

  const imgHtml = hasImg && session.attached.url ? `<img class="attach" src="${session.attached.url}" />` : '';
  addRow('user', esc(raw) + imgHtml);

  let sid = session.id;
  try {
    const payload = { message: raw, session_id: sid, image_path: session.attached ? session.attached.path : null };
    const data = await api('/v2/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    sid = data.session_id;
    session.id = sid;
    session.attached = null;
    $('attachPreview').style.display = 'none';
    $('chatTitle').textContent = '会话中…';
    $('chatSub').textContent = sid;
    markActive(sid);
    startStream(sid);
    refreshSessions();
  } catch (e) {
    addRow('assistant', '<span style="color:var(--danger)">发送失败：' + esc(e.message) + '</span>');
  }
}

async function finishTurn(sid) {
  stopPoll();
  const state = await api(`/v2/chat/${encodeURIComponent(sid)}/state`);
  renderAssistantTurn({ text: state.final_reply, choices: state.choices, products: state.products, status: state.status });
  if (session.progressBubble) {
    session.progressBubble.innerHTML = session.progressBubble.innerHTML.replace('⏳ 正在准备执行…', '🛠 执行结束');
  }
  session.progressOpen = false;
  refreshSessions();
}

/* SSE 优先，EventSource 不可用/出错回退轮询 */
function startStream(sid) {
  stopPoll();
  session.activeTurn = true;
  session.progressOpen = false;
  $('sendBtn').disabled = true;
  $('stopBtn').disabled = false;
  if (!window.EventSource) { startPoll(sid); return; }
  try {
    const es = new EventSource(`/v2/chat/${encodeURIComponent(sid)}/stream`);
    session.es = es;
    es.addEventListener('progress', (e) => {
      const ev = JSON.parse(e.data);
      if (!session.progressOpen) { session.progressBubble = addProgressBubble(); session.progressOpen = true; }
      const pb = session.progressBubble;
      const icon = ev.name === 'finish' ? '✅' : (ev.ok ? '✅' : '❌');
      const line = document.createElement('div');
      line.className = 'step';
      line.innerHTML = `<span class="${ev.ok ? 'ok' : 'fail'}">${icon}</span><span>${esc(ev.name)}${ev.message ? ' — ' + esc(ev.message.slice(0, 80)) : ''}</span>`;
      pb.appendChild(line);
      scrollDown();
    });
    es.addEventListener('done', () => { es.close(); session.es = null; finishTurn(sid); });
    es.onerror = () => { es.close(); session.es = null; startPoll(sid); };
  } catch { startPoll(sid); }
}

function startPoll(sid) {
  stopPoll();
  session.activeTurn = true;
  session.cursor = 0;
  session.progressOpen = false;
  $('sendBtn').disabled = true;
  $('stopBtn').disabled = false;
  session.pollTimer = setInterval(() => pollOnce(sid), 900);
  pollOnce(sid);
}

function stopPoll() {
  if (session.pollTimer) { clearInterval(session.pollTimer); session.pollTimer = null; }
  if (session.es) { session.es.close(); session.es = null; }
  session.activeTurn = false;
  session.progressOpen = false;
  $('sendBtn').disabled = false;
  $('stopBtn').disabled = true;
}

async function pollOnce(sid) {
  try {
    const data = await api(`/v2/chat/${encodeURIComponent(sid)}/events?cursor=${session.cursor}`);
    if (data.events && data.events.length) {
      if (!session.progressOpen) { session.progressBubble = addProgressBubble(); session.progressOpen = true; }
      for (const ev of data.events) {
        const pb = session.progressBubble;
        if (ev.type === 'progress' && pb) {
          const icon = ev.name === 'finish' ? '✅' : (ev.ok ? '✅' : '❌');
          const line = document.createElement('div');
          line.className = 'step';
          line.innerHTML = `<span class="${ev.ok ? 'ok' : 'fail'}">${icon}</span><span>${esc(ev.name)}${ev.message ? ' — ' + esc(ev.message.slice(0, 80)) : ''}</span>`;
          pb.appendChild(line);
        }
      }
      session.cursor = data.cursor;
      scrollDown();
    }
    if (data.done) { finishTurn(sid); }
  } catch (e) { /* 网络瞬时错误忽略 */ }
}

/* ---------------- 附件上传 ---------------- */
async function uploadImage(file) {
  const fd = new FormData();
  fd.append('file', file);
  return await api('/v2/upload', { method: 'POST', body: fd });
}
$('imgBtn').addEventListener('click', () => $('fileInput').click());
$('fileInput').addEventListener('change', async (e) => {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  try {
    const data = await uploadImage(file);
    session.attached = { path: data.path, url: URL.createObjectURL(file) };
    $('attachImg').src = session.attached.url;
    $('attachName').textContent = file.name + '（将按图片找相似）';
    $('attachPreview').style.display = 'flex';
  } catch (err) { alert(err.message); }
});
$('clearAttach').addEventListener('click', () => {
  if (session.attached && session.attached.url) URL.revokeObjectURL(session.attached.url);
  session.attached = null;
  $('attachPreview').style.display = 'none';
  $('fileInput').value = '';
});

/* ---------------- 事件绑定 ---------------- */
$('sendBtn').addEventListener('click', () => sendMessage());
$('newChatBtn').addEventListener('click', newChat);
$('stopBtn').addEventListener('click', async () => {
  if (session.id) await api(`/v2/chat/${encodeURIComponent(session.id)}/stop`, { method: 'POST' });
  stopPoll();
});
$('msgInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
$('msgInput').addEventListener('input', () => {
  $('msgInput').style.height = 'auto';
  $('msgInput').style.height = Math.min($('msgInput').scrollHeight, 140) + 'px';
});
document.querySelectorAll('#chips .chip').forEach((c) => {
  c.addEventListener('click', () => { $('msgInput').value = c.textContent; $('msgInput').focus(); });
});

/* ---------------- 初始化 ---------------- */
async function loadHealth() {
  try {
    const r = await api('/healthz');
    $('healthText').textContent = r.status === 'ok' ? '服务在线' : '服务异常';
  } catch { $('healthText').textContent = '服务离线'; }
}
loadHealth();
refreshSessions();
$('msgInput').focus();
