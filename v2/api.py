"""v2 HTTP API。

独立 FastAPI 应用（默认端口 8001，可通过 DRO_PORT 覆盖），与旧版 app.py 隔离。
接口：
- POST /v2/upload      上传图片（找相似用），返回本地路径
- POST /v2/tasks       创建任务（线程内执行，返回 thread_id）
- GET  /v2/tasks       列出任务
- GET  /v2/tasks/{id}  查询任务状态
- POST /v2/tasks/{id}/resume  恢复中断任务
- POST /v2/tasks/{id}/stop    停止任务
- GET  /v2/tools       列出已注册工具
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .config import settings
from .graph import TaskRunner
from .tools import list_tools

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


class TaskRequest(BaseModel):
    user_input: str = Field(..., description="用户输入")
    task_type: str = Field("", description="前端任务类型（比价/找相似/最优方案，可为空）")
    image_path: Optional[str] = Field(None, description="图片路径（找相似用）")
    thread_id: Optional[str] = Field(None, description="复用会话线程（多轮对话）")


class TaskCreateResponse(BaseModel):
    thread_id: str
    status: str


class TaskStatusResponse(BaseModel):
    thread_id: str
    next: List[str] = Field(default_factory=list)
    status: str = ""
    task_type: str = ""
    user_input: str = ""
    final_reply: str = ""
    products: List[Dict[str, Any]] = Field(default_factory=list)
    step_count: int = 0
    choices: List[str] = Field(default_factory=list)
    tool_log: List[Dict[str, Any]] = Field(default_factory=list)
    updated_at: float = 0.0


class ChatRequest(BaseModel):
    message: str = Field(..., description="用户消息")
    session_id: Optional[str] = Field(None, description="会话 id（首次为空则自动创建）")
    image_path: Optional[str] = Field(None, description="参考图路径（找相似用）")
    task_type: str = Field("", description="显式任务类型，可为空走自动识别")


class ChatCreateResponse(BaseModel):
    session_id: str
    thread_id: str
    status: str


TASK_STORE: Dict[str, Dict[str, Any]] = {}
# 会话 -> 当前活跃任务线程（一个会话可有多轮消息，每轮一个任务线程）
SESSION_STORE: Dict[str, Dict[str, Any]] = {}


def _task_summary_from_state(thread_id: str, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    values = state.get("values", {}) if state else {}
    return {
        "thread_id": thread_id,
        "status": values.get("status", TASK_STORE.get(thread_id, {}).get("status", "running")),
        "task_type": TASK_STORE.get(thread_id, {}).get("task_type", ""),
        "user_input": TASK_STORE.get(thread_id, {}).get("user_input", ""),
        "final_reply": values.get("final_reply", "") or TASK_STORE.get(thread_id, {}).get("final_reply", ""),
        "products": values.get("products", []) or TASK_STORE.get(thread_id, {}).get("products", []),
        "step_count": values.get("step_count", 0) or TASK_STORE.get(thread_id, {}).get("step_count", 0),
        "next": list(state.get("next", [])) if state else [],
        "updated_at": time.time(),
    }


def _memory_conn() -> sqlite3.Connection:
    """只读内存库连接（读取 messages 表用）。"""
    conn = sqlite3.connect(str(settings.memory_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _list_sessions(limit: int = 20) -> List[Dict[str, Any]]:
    """从 memory 库列出最近会话（用于聊天首页会话侧栏）。"""
    conn = _memory_conn()
    try:
        rows = conn.execute(
            "SELECT session_id, MAX(ts) AS last_ts FROM messages "
            "WHERE session_id LIKE 'sess_%' GROUP BY session_id ORDER BY last_ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for row in rows:
            last = conn.execute(
                "SELECT role, content FROM messages WHERE session_id=? ORDER BY ts DESC LIMIT 1",
                (row["session_id"],),
            ).fetchone()
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE session_id=?",
                (row["session_id"],),
            ).fetchone()["n"]
            out.append(
                {
                    "session_id": row["session_id"],
                    "last_ts": row["last_ts"],
                    "last_role": last["role"] if last else "",
                    "last_content": (last["content"][:60] if last else ""),
                    "message_count": count,
                }
            )
        return out
    finally:
        conn.close()


def _session_history(session_id: str) -> List[Dict[str, Any]]:
    """读取会话对话记录（时间正序）。"""
    conn = _memory_conn()
    try:
        rows = conn.execute(
            "SELECT role, content, ts FROM messages WHERE session_id=? ORDER BY ts ASC",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _render_chat_page() -> str:
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Dro v2 · 客服助手</title>
  <style>
    :root {
      --bg: #eef0f6;
      --panel: #ffffff;
      --border: #e5e8f0;
      --text: #1f2937;
      --muted: #6b7280;
      --primary: #4f46e5;
      --primary-weak: #eef2ff;
      --assistant: #f6f7fb;
      --success: #059669;
      --danger: #dc2626;
      --radius: 14px;
      --shadow: 0 1px 3px rgba(16,24,40,.06), 0 2px 10px rgba(16,24,40,.05);
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0; font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: var(--bg); color: var(--text); font-size: 14px; line-height: 1.6;
      display: flex; height: 100vh; overflow: hidden;
    }
    .mono { font-family: ui-monospace, Menlo, Consolas, monospace; }

    /* ---------- 会话侧栏 ---------- */
    .sidebar {
      width: 268px; flex-shrink: 0; background: var(--panel);
      border-right: 1px solid var(--border); display: flex; flex-direction: column;
    }
    .side-head { padding: 16px 14px 10px; display: flex; align-items: center; gap: 10px; border-bottom: 1px solid var(--border); }
    .logo {
      width: 34px; height: 34px; border-radius: 10px; color: #fff; font-weight: 800;
      background: linear-gradient(135deg, #4f46e5, #7c3aed); display: flex; align-items: center; justify-content: center;
    }
    .side-head b { font-size: 14px; }
    .side-head small { color: var(--muted); font-weight: 400; }
    .side-body { flex: 1; overflow: auto; padding: 8px 6px; }
    .session-item {
      padding: 10px 12px; border-radius: 10px; cursor: pointer; margin-bottom: 2px;
    }
    .session-item:hover { background: #f3f4f7; }
    .session-item.active { background: var(--primary-weak); }
    .session-item .t { font-weight: 600; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .session-item .s { color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .side-foot { border-top: 1px solid var(--border); padding: 10px 14px; display: flex; justify-content: space-between; align-items: center; color: var(--muted); font-size: 12px; }
    .side-foot a { color: var(--primary); text-decoration: none; }

    /* ---------- 主聊天区 ---------- */
    .main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
    .chat-head {
      background: var(--panel); border-bottom: 1px solid var(--border);
      padding: 12px 24px; display: flex; align-items: center; gap: 14px;
    }
    .status-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--success); box-shadow: 0 0 0 4px #d1fae5; }
    .chat-head .title { font-weight: 700; }
    .chat-head .sub { color: var(--muted); font-size: 12px; }
    .spacer { flex: 1; }
    .btn {
      border: 1px solid var(--border); background: #fff; color: var(--text);
      border-radius: 10px; padding: 7px 14px; font-size: 13px; cursor: pointer;
    }
    .btn:hover { background: #f3f4f7; }
    .btn.primary { background: var(--primary); border-color: var(--primary); color: #fff; }
    .btn.primary:hover { background: #4338ca; }
    .btn.danger { color: var(--danger); border-color: #fecaca; background: #fff; }
    .btn.danger:hover { background: #fef2f2; }
    .btn:disabled { opacity: .5; cursor: not-allowed; }

    .messages { flex: 1; overflow-y: auto; padding: 22px 0 10px; }
    .inner { max-width: 860px; margin: 0 auto; padding: 0 22px; display: flex; flex-direction: column; gap: 14px; }
    .day-sep { text-align: center; color: var(--muted); font-size: 12px; }

    .row { display: flex; gap: 10px; align-items: flex-start; }
    .row.user { flex-direction: row-reverse; }
    .avatar {
      width: 32px; height: 32px; border-radius: 50%; flex-shrink: 0;
      display: flex; align-items: center; justify-content: center; font-size: 15px;
      background: linear-gradient(135deg, #4f46e5, #7c3aed); color: #fff;
    }
    .row.user .avatar { background: #dbeafe; color: #1d4ed8; }
    .bubble-wrap { max-width: 76%; display: flex; flex-direction: column; gap: 6px; }
    .bubble {
      background: var(--assistant); border: 1px solid var(--border); border-radius: 14px;
      padding: 11px 14px; white-space: pre-wrap; word-break: break-word;
      box-shadow: var(--shadow); font-size: 14px;
    }
    .row.user .bubble { background: var(--primary); border-color: var(--primary); color: #fff; border-top-right-radius: 4px; }
    .row.assistant .bubble { border-top-left-radius: 4px; }
    .bubble img.attach { max-width: 220px; border-radius: 8px; display: block; margin-top: 8px; }

    /* 执行过程气泡 */
    .progress-bubble {
      background: #0f172a; color: #cbd5e1; border-radius: 12px; padding: 10px 14px;
      font-size: 12.5px; line-height: 1.8; max-width: 76%;
    }
    .progress-bubble .step { display: flex; gap: 8px; align-items: flex-start; }
    .progress-bubble .ok { color: #34d399; }
    .progress-bubble .fail { color: #f87171; }
    .progress-bubble .waiting { color: #94a3b8; font-style: italic; }

    /* 选项按钮 */
    .choices { display: flex; flex-wrap: wrap; gap: 8px; }
    .choice {
      border: 1px solid #c7d2fe; background: var(--primary-weak); color: var(--primary);
      border-radius: 999px; padding: 7px 16px; font-size: 13px; font-weight: 600; cursor: pointer;
    }
    .choice:hover { background: #e0e7ff; }

    /* 商品卡片 */
    .product-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; margin-top: 4px; }
    .product {
      background: var(--assistant); border: 1px solid var(--border); border-radius: 12px;
      padding: 12px 14px; box-shadow: var(--shadow);
    }
    .product.winner { border-color: #a7f3d0; background: #ecfdf5; }
    .product .name { font-weight: 700; font-size: 13.5px; line-height: 1.4; }
    .product .price { color: var(--primary); font-size: 18px; font-weight: 800; margin: 6px 0; }
    .product .meta { color: var(--muted); font-size: 12px; line-height: 1.8; }
    .product .reason { margin-top: 6px; font-size: 12px; color: #475569; background: #fff; border: 1px dashed #e2e8f0; border-radius: 8px; padding: 6px 8px; }
    .product .reason.over { color: var(--danger); border-color: #fecaca; background: #fff; }

    /* ---------- 输入区 ---------- */
    .composer { border-top: 1px solid var(--border); background: var(--panel); padding: 12px 22px 16px; }
    .composer-inner { max-width: 860px; margin: 0 auto; }
    .attach-preview { display: none; align-items: center; gap: 10px; margin-bottom: 10px; }
    .attach-preview img { width: 56px; height: 56px; object-fit: cover; border-radius: 8px; border: 1px solid var(--border); }
    .attach-preview .x { cursor: pointer; color: var(--muted); }
    .input-row { display: flex; gap: 10px; align-items: flex-end; }
    .input-row textarea {
      flex: 1; border: 1px solid var(--border); border-radius: 14px; resize: none;
      padding: 12px 14px; font: inherit; outline: none; max-height: 140px; min-height: 46px;
      background: #f9fafb;
    }
    .input-row textarea:focus { border-color: var(--primary); background: #fff; box-shadow: 0 0 0 3px rgba(79,70,229,.1); }
    .icon-btn {
      width: 46px; height: 46px; border-radius: 14px; border: 1px solid var(--border);
      background: #fff; cursor: pointer; font-size: 19px; display: flex; align-items: center; justify-content: center;
    }
    .icon-btn:hover { background: #f3f4f7; }
    .chips { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
    .chip {
      border: 1px solid var(--border); background: #fff; border-radius: 999px;
      padding: 5px 12px; font-size: 12px; color: var(--muted); cursor: pointer;
    }
    .chip:hover { border-color: var(--primary); color: var(--primary); }
    .empty-tip { text-align: center; color: var(--muted); padding: 60px 20px; }
    .empty-tip .big { font-size: 42px; margin-bottom: 10px; }
  </style>
</head>
<body>
  <!-- 会话侧栏 -->
  <aside class="sidebar">
    <div class="side-head">
      <div class="logo">Dro</div>
      <div><b>客服助手</b><br /><small>购物 · 比价 · 找相似</small></div>
    </div>
    <div class="side-body" id="sessionList">
      <div class="empty-tip" style="padding:24px 10px;">加载会话…</div>
    </div>
    <div class="side-foot">
      <span><span class="status-dot" id="healthDot" style="display:inline-block;width:8px;height:8px;"></span> <span id="healthText">检测中</span></span>
      <a href="/" target="_blank" rel="noreferrer">控制台</a>
    </div>
  </aside>

  <!-- 主聊天区 -->
  <section class="main">
    <header class="chat-head">
      <div>
        <div class="title" id="chatTitle">新的会话</div>
        <div class="sub" id="chatSub">向助手描述你的购物需求</div>
      </div>
      <div class="spacer"></div>
      <button class="btn" id="newChatBtn">＋ 新会话</button>
      <button class="btn danger" id="stopBtn" disabled>停止</button>
    </header>

    <div class="messages" id="msgScroll">
      <div class="inner" id="msgList">
        <div class="empty-tip" id="welcome">
          <div class="big">🛍️</div>
          <div>你好，我是你的购物客服助手。<br/>可以帮你比价、找相似款、按预算推荐商品。</div>
        </div>
      </div>
    </div>

    <div class="composer">
      <div class="composer-inner">
        <div class="attach-preview" id="attachPreview">
          <img id="attachImg" alt="参考图" />
          <span style="font-size:13px;color:var(--muted)" id="attachName"></span>
          <span class="x" id="clearAttach" title="移除">✕ 移除</span>
        </div>
        <div class="input-row">
          <textarea id="msgInput" rows="1" placeholder="说说你想买什么，例如：帮我找一个100以内的拼豆玩具…"></textarea>
          <button class="icon-btn" id="imgBtn" title="上传参考图片（找相似）">📎</button>
          <input type="file" id="fileInput" accept="image/*" style="display:none" />
          <button class="btn primary" id="sendBtn" style="height:46px;border-radius:14px;">发送</button>
        </div>
        <div class="chips" id="chips">
          <button class="chip">帮我比价蓝牙耳机，预算200以内</button>
          <button class="chip">找一个100以内的拼豆玩具</button>
          <button class="chip">推荐500元以内的机械键盘</button>
        </div>
      </div>
    </div>
  </section>

  <script>
    const $ = (id) => document.getElementById(id);
    const session = {
      id: null,           // 当前会话
      pollTimer: null,
      cursor: 0,
      activeTurn: false,
      attached: null,     // {path, url}
      progressOpen: false, // 是否已有过程气泡
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

    function renderAssistantTurn(turn) {
      // turn: {text, choices, products, status, image_url?}
      const texts = (turn.text || '').split('\\n');
      let html = '';
      if (turn.text) {
        html += esc(turn.text).replace(/\\n/g, '<br/>');
      } else if (turn.status === 'failed') {
        html += '<span style="color:#dc2626">任务执行出错，请稍后重试。</span>';
      }
      if (html) addRow('assistant', html);

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
          card.className = 'product' + (p.is_winner ? ' winner' : '');
          const reason = (p.reason || '').trim();
          const over = reason.includes('超出预算');
          card.innerHTML =
            `<div class="name">${esc(p.name || '未命名')}</div>
             <div class="price">${esc(priceText(p.price))}</div>
             <div class="meta">
               平台：${esc(p.platform || '—')}
               ${p.shop_name ? ' · 店铺：' + esc(p.shop_name) : ''}<br/>
               销量：${esc(p.sales || '—')} · 评分：${esc(p.rating || '—')}
             </div>
             ${reason ? `<div class="reason ${over ? 'over' : ''}">${esc(reason)}</div>` : ''}`;
          grid.appendChild(card);
        }
        bw.appendChild(grid);
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
      try {
        await loadHistory(sid);
      } catch { /* 空会话 */ }
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

    /* ---------------- 发送 & 轮询 ---------------- */
    async function sendMessage(textOverride) {
      const raw = (textOverride !== undefined ? textOverride : $('msgInput').value).trim();
      if (!raw) return;
      const hasImg = !!session.attached;
      $('msgInput').value = '';
      if (session.activeTurn) return; // 上一条还没结束

      // 本地先插入用户气泡
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
        startPoll(sid);
        refreshSessions();
      } catch (e) {
        addRow('assistant', '<span style="color:#dc2626">发送失败：' + esc(e.message) + '</span>');
      }
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
      session.activeTurn = false;
      session.progressOpen = false;
      $('sendBtn').disabled = false;
      $('stopBtn').disabled = true;
    }

    async function pollOnce(sid) {
      try {
        const data = await api(`/v2/chat/${encodeURIComponent(sid)}/events?cursor=${session.cursor}`);
        if (data.events && data.events.length) {
          if (!session.progressOpen) {
            session.progressBubble = addProgressBubble();
            session.progressOpen = true;
          }
          for (const ev of data.events) {
            const pb = session.progressBubble;
            if (ev.type === 'progress' && pb) {
              const icon = ev.name === 'finish' ? '✅' : (ev.ok ? '✅' : '❌');
              const line = document.createElement('div');
              line.className = 'step';
              line.innerHTML = `<span class="${ev.ok ? 'ok' : 'fail'}">${icon}</span>
                <span>${esc(ev.name)}${ev.message ? ' — ' + esc(ev.message.slice(0, 80)) : ''}</span>`;
              pb.appendChild(line);
            }
          }
          session.cursor = data.cursor;
          scrollDown();
        }
        if (data.done) {
          stopPoll();
          // 加载终态（最终回复 / 选项 / 商品）
          const state = await api(`/v2/chat/${encodeURIComponent(sid)}/state`);
          renderAssistantTurn({ text: state.final_reply, choices: state.choices, products: state.products, status: state.status });
          // 保留过程气泡中的等待行 -> 更新为完成
          if (session.progressBubble) {
            session.progressBubble.innerHTML = session.progressBubble.innerHTML.replace(
              '⏳ 正在准备执行…', '🛠 执行结束'
            );
          }
          session.progressOpen = false;
          refreshSessions();
        }
      } catch (e) {
        // 网络瞬时错误忽略，继续轮询
      }
    }

    /* ---------------- 附件上传 ---------------- */
    async function uploadImage(file) {
      const fd = new FormData();
      fd.append('file', file);
      const data = await api('/v2/upload', { method: 'POST', body: fd });
      return data;
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
  </script>
</body>
</html>"""


def _render_home_page() -> str:
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Dro v2 · 任务编排中心</title>
  <style>
    :root {
      --bg: #f3f5f9;
      --card: #ffffff;
      --border: #e6e9f0;
      --text: #1f2937;
      --muted: #6b7280;
      --primary: #4f46e5;
      --primary-hover: #4338ca;
      --primary-weak: #eef2ff;
      --success: #059669;
      --success-weak: #ecfdf5;
      --danger: #dc2626;
      --danger-weak: #fef2f2;
      --warn: #d97706;
      --warn-weak: #fffbeb;
      --radius: 14px;
      --shadow: 0 1px 3px rgba(16, 24, 40, .06), 0 1px 2px rgba(16, 24, 40, .04);
      --shadow-md: 0 4px 16px rgba(16, 24, 40, .08);
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      color: var(--text);
      background: var(--bg);
      font-size: 14px;
      line-height: 1.6;
    }
    code, pre, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .shell { max-width: 1440px; margin: 0 auto; padding: 20px 22px 48px; }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }

    /* ---------- 顶部 ---------- */
    .hero {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 20px;
      padding: 18px 24px;
      margin-bottom: 20px;
    }
    .brand { display: flex; align-items: center; gap: 14px; }
    .logo {
      width: 44px; height: 44px; border-radius: 12px;
      background: linear-gradient(135deg, #4f46e5, #7c3aed);
      color: #fff; display: flex; align-items: center; justify-content: center;
      font-size: 22px; font-weight: 800; box-shadow: var(--shadow-md);
    }
    .brand h1 { margin: 0; font-size: 18px; letter-spacing: .02em; }
    .brand p { margin: 2px 0 0; color: var(--muted); font-size: 12px; }
    .status-area { display: flex; align-items: center; gap: 14px; }
    .status-dot {
      width: 10px; height: 10px; border-radius: 50%; background: var(--muted); display: inline-block;
    }
    .status-dot.on { background: var(--success); box-shadow: 0 0 0 4px var(--success-weak); }
    .status-dot.off { background: var(--danger); box-shadow: 0 0 0 4px var(--danger-weak); }
    .status-text { font-size: 13px; font-weight: 600; }

    /* ---------- 三栏布局 ---------- */
    .layout {
      display: grid;
      grid-template-columns: 300px 1fr 340px;
      gap: 20px;
      align-items: start;
    }
    .col { display: flex; flex-direction: column; gap: 20px; min-width: 0; }

    .panel-head {
      display: flex; justify-content: space-between; align-items: center;
      padding: 16px 20px; border-bottom: 1px solid var(--border);
    }
    .panel-head h2 { margin: 0; font-size: 15px; }
    .panel-head .hint { color: var(--muted); font-size: 12px; }
    .panel-body { padding: 20px; }
    .panel-body.flush { padding: 0; }

    /* ---------- 表单 ---------- */
    label { display: block; margin: 0 0 8px; font-size: 13px; font-weight: 600; color: #374151; }
    .field { margin-bottom: 16px; }
    textarea, input, select {
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: #fff;
      color: var(--text);
      padding: 11px 13px;
      font: inherit;
      outline: none;
      transition: border-color .15s ease, box-shadow .15s ease;
    }
    textarea { min-height: 96px; resize: vertical; }
    textarea:focus, input:focus, select:focus {
      border-color: var(--primary);
      box-shadow: 0 0 0 3px rgba(79, 70, 229, .12);
    }
    select { cursor: pointer; }

    .upload-zone {
      border: 1.5px dashed #c7cdda;
      border-radius: 10px;
      padding: 14px;
      text-align: center;
      cursor: pointer;
      color: var(--muted);
      font-size: 13px;
      transition: all .15s ease;
      position: relative;
    }
    .upload-zone:hover, .upload-zone.drag { border-color: var(--primary); background: var(--primary-weak); }
    .upload-zone input { display: none; }
    .upload-zone .up-ico { font-size: 20px; margin-bottom: 4px; }
    .upload-preview {
      display: none; position: relative; margin-top: 10px;
    }
    .upload-preview img {
      width: 100%; max-height: 160px; object-fit: cover;
      border-radius: 10px; border: 1px solid var(--border);
    }
    .upload-preview .clear {
      position: absolute; top: 8px; right: 8px;
      width: 24px; height: 24px; border-radius: 50%;
      background: rgba(15, 23, 42, .65); color: #fff; border: 0;
      font-size: 13px; line-height: 1; cursor: pointer;
    }
    .up-tip { color: var(--muted); font-size: 12px; margin-top: 8px; }

    .actions { display: flex; gap: 10px; }
    button {
      appearance: none; border: 0; border-radius: 10px; padding: 11px 16px;
      font-size: 14px; font-weight: 600; cursor: pointer;
      transition: transform .12s ease, background .15s ease, box-shadow .15s ease;
    }
    button:active { transform: translateY(1px); }
    .btn-primary {
      flex: 1; color: #fff; background: var(--primary);
    }
    .btn-primary:hover { background: var(--primary-hover); box-shadow: var(--shadow-md); }
    .btn-ghost { color: #374151; background: #f3f4f6; }
    .btn-ghost:hover { background: #e5e7eb; }
    .btn-danger { color: var(--danger); background: var(--danger-weak); }
    .btn-danger:hover { background: #fee2e2; }

    /* ---------- 任务历史 ---------- */
    .history-list { max-height: 420px; overflow: auto; }
    .history-item {
      padding: 13px 20px; border-bottom: 1px solid var(--border); cursor: pointer;
      transition: background .12s ease;
    }
    .history-item:last-child { border-bottom: 0; }
    .history-item:hover { background: #f9fafb; }
    .history-item.active { background: var(--primary-weak); }
    .history-top { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
    .history-title { font-weight: 600; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .history-sub { color: var(--muted); font-size: 12px; margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .badge {
      display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0;
      padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 600;
    }
    .badge.neutral { background: #f3f4f6; color: #4b5563; }
    .badge.ok { background: var(--success-weak); color: var(--success); }
    .badge.err { background: var(--danger-weak); color: var(--danger); }
    .badge.warn { background: var(--warn-weak); color: var(--warn); }
    .badge.run { background: var(--primary-weak); color: var(--primary); }

    /* ---------- 统计卡片 ---------- */
    .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
    .stat {
      padding: 16px 18px; display: flex; align-items: center; gap: 14px;
    }
    .stat-ico {
      width: 40px; height: 40px; border-radius: 10px; display: flex;
      align-items: center; justify-content: center; font-size: 18px;
    }
    .stat .k { color: var(--muted); font-size: 12px; }
    .stat .v { font-size: 22px; font-weight: 700; line-height: 1.2; }

    /* ---------- 详情 ---------- */
    .meta-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 14px; }
    .meta-item { background: #f9fafb; border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; }
    .meta-item .k { color: var(--muted); font-size: 11px; }
    .meta-item .v { font-size: 13px; font-weight: 600; margin-top: 3px; word-break: break-all; }
    .log {
      background: #0f172a; color: #cbd5e1; border-radius: 10px; padding: 14px;
      font-size: 12px; line-height: 1.7; min-height: 220px; max-height: 380px;
      overflow: auto; white-space: pre-wrap; word-break: break-word;
    }
    .status-bar {
      display: flex; align-items: center; gap: 10px; margin-bottom: 12px;
      padding: 10px 14px; border-radius: 10px; font-size: 13px; font-weight: 600;
    }
    .status-bar.neutral { background: #f3f4f6; color: #4b5563; }
    .status-bar.ok { background: var(--success-weak); color: var(--success); }
    .status-bar.err { background: var(--danger-weak); color: var(--danger); }
    .status-bar.warn { background: var(--warn-weak); color: var(--warn); }
    .status-bar.run { background: var(--primary-weak); color: var(--primary); }

    /* ---------- 结果 ---------- */
    .result-reply {
      background: var(--primary-weak); border: 1px solid #e0e7ff;
      border-radius: 10px; padding: 14px; font-size: 13px; white-space: pre-wrap;
    }
    .product {
      border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px;
      margin-top: 10px;
    }
    .product-top { display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; }
    .product-name { font-weight: 600; font-size: 13px; line-height: 1.45; }
    .product-price { font-size: 16px; font-weight: 700; color: var(--primary); white-space: nowrap; }
    .product-meta { color: var(--muted); font-size: 12px; margin-top: 8px; line-height: 1.7; }

    /* ---------- 工具 ---------- */
    .tool-list { max-height: 360px; overflow: auto; }
    .tool-item { padding: 12px 20px; border-bottom: 1px solid var(--border); }
    .tool-item:last-child { border-bottom: 0; }
    .tool-item h4 { margin: 0 0 4px; font-size: 13px; }
    .tool-item p { margin: 0; color: var(--muted); font-size: 12px; }

    .empty { color: var(--muted); font-size: 13px; padding: 20px; text-align: center; }
    .footer {
      margin-top: 24px; display: flex; justify-content: space-between;
      color: var(--muted); font-size: 12px; flex-wrap: wrap; gap: 10px;
    }
    .footer a { color: var(--primary); text-decoration: none; font-weight: 600; }

    @media (max-width: 1240px) {
      .layout { grid-template-columns: 1fr 1fr; }
      .col.right { grid-column: 1 / -1; display: grid; grid-template-columns: 1fr 1fr; }
    }
    @media (max-width: 860px) {
      .layout { grid-template-columns: 1fr; }
      .col.right { display: flex; }
      .stats { grid-template-columns: 1fr; }
      .hero { flex-direction: column; align-items: flex-start; }
    }
  </style>
</head>
<body>
  <div class="shell">

    <!-- 顶部 -->
    <header class="hero card">
      <div class="brand">
        <div class="logo">Dro</div>
        <div>
          <h1>购物任务编排中心</h1>
          <p>LangGraph + 工具化 ADB 执行引擎 · 比价 / 找相似 / 最优方案</p>
        </div>
      </div>
      <div class="status-area">
        <span class="status-dot" id="healthDot"></span>
        <span class="status-text" id="healthText">检测中…</span>
      </div>
    </header>

    <!-- 三栏主体 -->
    <main class="layout">

      <!-- 左栏：创建任务 + 历史 -->
      <div class="col">
        <section class="card">
          <div class="panel-head"><h2>创建任务</h2><span class="hint">描述任务需求</span></div>
          <div class="panel-body">
            <div class="field">
              <label for="userInput">任务需求</label>
              <textarea id="userInput" placeholder="例如：帮我比价蓝牙耳机，预算 200 以内"></textarea>
            </div>

            <div class="field">
              <label for="taskType">任务类型</label>
              <select id="taskType">
                <option value="">自动识别</option>
                <option value="比价">比价</option>
                <option value="找相似">找相似</option>
                <option value="最优方案">最优方案</option>
              </select>
            </div>

            <div class="field">
              <label>参考图片（可选，找相似时上传）</label>
              <div class="upload-zone" id="uploadZone">
                <input type="file" id="fileInput" accept="image/*" />
                <div class="up-ico">🖼️</div>
                <div id="uploadText">点击或拖拽上传图片</div>
                <div class="up-tip">支持 JPG / PNG / WebP</div>
              </div>
              <div class="upload-preview" id="uploadPreview">
                <img id="previewImg" alt="预览" />
                <button class="clear" id="clearImg" title="移除图片">✕</button>
              </div>
            </div>

            <div class="actions">
              <button class="btn-primary" id="createBtn">创建任务</button>
              <button class="btn-ghost" id="refreshBtn" title="刷新当前任务">刷新</button>
              <button class="btn-danger" id="stopBtn" title="停止当前任务">停止</button>
            </div>
          </div>
        </section>

        <section class="card">
          <div class="panel-head"><h2>任务历史</h2><span class="hint" id="historyCount">0 条</span></div>
          <div class="history-list" id="historyList">
            <div class="empty">暂无任务记录</div>
          </div>
        </section>
      </div>

      <!-- 中栏：统计 + 详情 + 执行状态 -->
      <div class="col">
        <section class="stats">
          <div class="stat card">
            <div class="stat-ico" style="background:var(--primary-weak);color:var(--primary);">📋</div>
            <div><div class="k">任务总数</div><div class="v" id="statTotal">0</div></div>
          </div>
          <div class="stat card">
            <div class="stat-ico" style="background:var(--warn-weak);color:var(--warn);">⏳</div>
            <div><div class="k">执行中</div><div class="v" id="statRunning">0</div></div>
          </div>
          <div class="stat card">
            <div class="stat-ico" style="background:var(--success-weak);color:var(--success);">✅</div>
            <div><div class="k">已完成</div><div class="v" id="statDone">0</div></div>
          </div>
        </section>

        <section class="card">
          <div class="panel-head"><h2>任务详情</h2><span class="hint" id="detailThread">—</span></div>
          <div class="panel-body">
            <div class="status-bar neutral" id="statusBar">
              <span id="statusText">等待创建任务</span>
            </div>
            <div class="meta-grid">
              <div class="meta-item"><div class="k">任务类型</div><div class="v" id="metaType">—</div></div>
              <div class="meta-item"><div class="k">执行步数</div><div class="v" id="metaStep">0</div></div>
              <div class="meta-item"><div class="k">商品数</div><div class="v" id="metaProducts">0</div></div>
              <div class="meta-item"><div class="k">更新时间</div><div class="v" id="metaTime">—</div></div>
            </div>
            <div class="log" id="taskLog">暂无执行日志…</div>
          </div>
        </section>
      </div>

      <!-- 右栏：结果 + 工具 -->
      <div class="col right">
        <section class="card">
          <div class="panel-head"><h2>结果消息</h2><span class="hint" id="resultStatus">—</span></div>
          <div class="panel-body">
            <div id="resultSummary"><div class="empty">暂无结果</div></div>
            <div id="resultGrid"></div>
          </div>
        </section>

        <section class="card">
          <div class="panel-head"><h2>已注册工具</h2><span class="hint" id="toolCount">0 个</span></div>
          <div class="tool-list" id="toolList">
            <div class="empty">加载中…</div>
          </div>
        </section>
      </div>
    </main>

    <footer class="footer">
      <div>Dro v2 · 服务端口 8001 · <code>/v2/tasks</code></div>
      <div><a href="/chat">切换客服助手 /chat</a> · <a href="/docs" target="_blank" rel="noreferrer">打开接口文档 /docs</a></div>
    </footer>
  </div>

  <script>
    const $ = (id) => document.getElementById(id);
    const state = { threadId: null, timer: null, uploadedPath: null, history: [] };

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

    /* ---------- 图片上传 ---------- */
    async function uploadImage(file) {
      const fd = new FormData();
      fd.append('file', file);
      const res = await fetch('/v2/upload', { method: 'POST', body: fd });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || '上传失败');
      }
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
      startPoll();
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
        if (['done', 'failed', 'cancelled'].includes((task.status || '').toLowerCase())) {
          clearInterval(state.timer);
          state.timer = null;
        }
      } catch (e) {
        $('taskLog').textContent = `轮询失败：${e}`;
      }
    }

    function startPoll() {
      if (state.timer) clearInterval(state.timer);
      state.timer = setInterval(pollTask, 1500);
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
          startPoll();
          pollTask();
        });
        box.appendChild(el);
      });
    }

    /* ---------- 统计 ---------- */
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

    $('createBtn').addEventListener('click', createTask);
    $('refreshBtn').addEventListener('click', pollTask);
    $('stopBtn').addEventListener('click', stopTask);
    $('createBtn').addEventListener('click', () => renderStats());

    loadHealth();
    loadTools();
  </script>
</body>
</html>"""


def create_app() -> FastAPI:
    app = FastAPI(title="Dro v2", version="2.0.0", description="LangGraph + 工具化 ADB 执行引擎")

    runner = TaskRunner()
    running: Dict[str, asyncio.Task] = {}

    @app.get("/", response_class=HTMLResponse)
    async def root():
        return _render_home_page()

    @app.get("/chat", response_class=HTMLResponse)
    async def chat_page():
        return _render_chat_page()

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "version": "2.0.0"}

    @app.post("/v2/upload")
    async def upload_image(file: UploadFile = File(...)):
        filename = (file.filename or "").lower()
        ext = Path(filename).suffix
        if ext not in ALLOWED_IMAGE_EXTS:
            raise HTTPException(status_code=400, detail="仅支持 JPG / PNG / WebP / GIF / BMP 图片")
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="文件内容为空")
        name = f"{int(time.time() * 1000)}_{uuid4().hex[:8]}{ext}"
        dest = settings.uploads_dir / name
        dest.write_bytes(content)
        return {"path": str(dest.resolve()), "name": name, "size": len(content)}

    @app.get("/v2/tools", response_model=dict)
    async def tools():
        tools_map = list_tools()
        return {name: {"description": t.description, "parameters": t.parameters} for name, t in tools_map.items()}

    @app.get("/v2/tasks", response_model=dict)
    async def list_tasks():
        items = sorted(TASK_STORE.values(), key=lambda x: x.get("updated_at", 0), reverse=True)
        return {"tasks": items}

    @app.post("/v2/tasks", response_model=TaskCreateResponse)
    async def create_task(req: TaskRequest):
        thread_id = req.thread_id or runner.new_thread_id()
        TASK_STORE[thread_id] = {
            "thread_id": thread_id,
            "status": "queued",
            "task_type": req.task_type,
            "user_input": req.user_input,
            "updated_at": time.time(),
            "final_reply": "",
            "products": [],
            "step_count": 0,
        }

        async def _run():
            TASK_STORE[thread_id]["status"] = "running"
            TASK_STORE[thread_id]["updated_at"] = time.time()
            try:
                out = await runner.ainvoke(
                    user_input=req.user_input,
                    task_type=req.task_type,
                    image_path=req.image_path,
                    thread_id=thread_id,
                )
                TASK_STORE[thread_id].update(_task_summary_from_state(thread_id, {"values": out}))
                TASK_STORE[thread_id]["status"] = out.get("status", "done")
            except asyncio.CancelledError:
                TASK_STORE[thread_id]["status"] = "cancelled"
                TASK_STORE[thread_id]["updated_at"] = time.time()
                raise
            except Exception as exc:  # noqa: BLE001
                TASK_STORE[thread_id]["status"] = "failed"
                TASK_STORE[thread_id]["final_reply"] = str(exc)
                TASK_STORE[thread_id]["updated_at"] = time.time()
            finally:
                running.pop(thread_id, None)

        task = asyncio.create_task(_run())
        running[thread_id] = task
        return TaskCreateResponse(thread_id=thread_id, status="created")

    @app.get("/v2/tasks/{thread_id}", response_model=TaskStatusResponse)
    async def task_status(thread_id: str):
        state = await runner.aget_state(thread_id)
        if state is None and thread_id not in running and thread_id not in TASK_STORE:
            raise HTTPException(status_code=404, detail="任务不存在")
        values: Dict[str, Any] = state.get("values", {}) if state else {}
        record = TASK_STORE.get(thread_id, {})
        return TaskStatusResponse(
            thread_id=thread_id,
            next=list(state.get("next", [])) if state else [],
            status=values.get("status", record.get("status", "running")),
            task_type=values.get("task_type") or record.get("task_type", ""),
            user_input=values.get("user_input") or record.get("user_input", ""),
            final_reply=values.get("final_reply", record.get("final_reply", "")),
            products=values.get("products", record.get("products", [])),
            step_count=values.get("step_count", record.get("step_count", 0)),
            choices=list(values.get("choices") or []),
            tool_log=list(values.get("tool_log") or []),
            updated_at=record.get("updated_at", 0.0),
        )

    @app.post("/v2/tasks/{thread_id}/resume")
    async def resume_task(thread_id: str):
        task = running.get(thread_id)
        if task and not task.done():
            raise HTTPException(status_code=409, detail="任务仍在执行中")
        result = await runner.aresume(thread_id)
        TASK_STORE.setdefault(thread_id, {})["updated_at"] = time.time()
        return result

    @app.post("/v2/tasks/{thread_id}/stop")
    async def stop_task(thread_id: str):
        task = running.pop(thread_id, None)
        if task:
            task.cancel()
            TASK_STORE.setdefault(thread_id, {})["status"] = "cancelled"
            TASK_STORE.setdefault(thread_id, {})["updated_at"] = time.time()
            return {"thread_id": thread_id, "stopped": True}
        TASK_STORE.setdefault(thread_id, {})["status"] = "cancelled"
        TASK_STORE.setdefault(thread_id, {})["updated_at"] = time.time()
        return {"thread_id": thread_id, "stopped": False}

    # ------------------------------------------------------------------
    # 会话式客服聊天（/chat 页面配套）
    # ------------------------------------------------------------------
    def _executor(session_id: str, thread_id: str):
        """启动后台执行并维护 SESSION_STORE/TASK_STORE 状态。"""

        async def _run():
            rec = SESSION_STORE.get(session_id, {})
            message = rec.get("user_input", "")
            task_type = rec.get("task_type", "")
            image_path = rec.get("image_path")
            TASK_STORE[thread_id]["status"] = "running"
            SESSION_STORE[session_id]["status"] = "running"
            try:
                out = await runner.ainvoke(
                    user_input=message,
                    task_type=task_type,
                    image_path=image_path,
                    thread_id=thread_id,
                    session_id=session_id,
                )
                values = out or {}
                TASK_STORE[thread_id].update(
                    {
                        "status": values.get("status", "done"),
                        "task_type": values.get("task_type") or task_type,
                        "final_reply": values.get("final_reply", ""),
                        "products": values.get("products", []),
                        "step_count": values.get("step_count", 0),
                        "updated_at": time.time(),
                    }
                )
                SESSION_STORE[session_id].update(
                    {
                        "status": values.get("status", "done"),
                        "task_type": values.get("task_type") or task_type,
                        "final_reply": values.get("final_reply", ""),
                        "choices": list(values.get("choices") or []),
                        "updated_at": time.time(),
                    }
                )
            except asyncio.CancelledError:
                TASK_STORE[thread_id]["status"] = "cancelled"
                SESSION_STORE[session_id]["status"] = "cancelled"
                SESSION_STORE[session_id]["updated_at"] = time.time()
                raise
            except Exception as exc:  # noqa: BLE001
                TASK_STORE[thread_id]["status"] = "failed"
                TASK_STORE[thread_id]["final_reply"] = str(exc)
                SESSION_STORE[session_id]["status"] = "failed"
                SESSION_STORE[session_id]["final_reply"] = f"出错了：{exc}"
                SESSION_STORE[session_id]["updated_at"] = time.time()
            finally:
                running.pop(thread_id, None)

        task = asyncio.create_task(_run())
        running[thread_id] = task
        return task

    @app.get("/v2/chat/sessions")
    async def chat_sessions(limit: int = 20):
        return {"sessions": _list_sessions(limit)}

    @app.post("/v2/chat", response_model=ChatCreateResponse)
    async def chat_send(req: ChatRequest):
        session_id = req.session_id or f"sess_{int(time.time() * 1000)}"
        thread_id = runner.new_thread_id()
        prev = SESSION_STORE.get(session_id, {})
        # 上一轮仍在执行：取消旧任务，让新消息成为当前焦点
        if prev.get("thread_id"):
            old = running.pop(prev["thread_id"], None)
            if old:
                old.cancel()
        TASK_STORE[thread_id] = {
            "thread_id": thread_id,
            "session_id": session_id,
            "status": "queued",
            "task_type": req.task_type or (prev.get("task_type", "") if prev.get("status") == "needs_input" else ""),
            "user_input": req.message,
            "image_path": req.image_path,
            "updated_at": time.time(),
            "final_reply": "",
            "products": [],
            "step_count": 0,
        }
        SESSION_STORE[session_id] = {
            "thread_id": thread_id,
            "status": "queued",
            "task_type": TASK_STORE[thread_id]["task_type"],
            "user_input": req.message,
            "image_path": req.image_path,
            "updated_at": time.time(),
        }
        _executor(session_id, thread_id)
        return ChatCreateResponse(session_id=session_id, thread_id=thread_id, status="created")

    @app.get("/v2/chat/{session_id}/history")
    async def chat_history(session_id: str):
        return {"session_id": session_id, "messages": _session_history(session_id)}

    @app.get("/v2/chat/{session_id}/events")
    async def chat_events(session_id: str, cursor: int = 0):
        """轮询式进度事件：增量返回工具执行步骤与终态标记。"""
        rec = SESSION_STORE.get(session_id)
        if not rec:
            return {"status": "unknown", "events": [], "cursor": 0, "done": True}
        thread_id = rec["thread_id"]
        if thread_id in running:
            status = "running"
        else:
            status = rec.get("status", "queued")
        state = await runner.aget_state(thread_id)
        values: Dict[str, Any] = state.get("values", {}) if state else {}
        tool_log = list(values.get("tool_log") or [])
        if state:
            status = values.get("status", status)
        events = []
        for entry in tool_log[cursor:]:
            events.append(
                {
                    "type": "progress",
                    "name": entry.get("name", ""),
                    "ok": entry.get("ok", True),
                    "message": (entry.get("message") or "")[:160],
                    "data": entry.get("data") or {},
                }
            )
        new_cursor = max(cursor, len(tool_log))
        done = status in ("done", "needs_input", "failed", "cancelled")
        return {
            "status": status,
            "events": events,
            "cursor": new_cursor,
            "done": done,
            "thread_id": thread_id,
        }

    @app.get("/v2/chat/{session_id}/state")
    async def chat_state(session_id: str):
        """会话终态详情：最终回复/商品/选项（前端渲染完成消息）。"""
        rec = SESSION_STORE.get(session_id)
        if not rec:
            raise HTTPException(status_code=404, detail="会话不存在或已被清理")
        thread_id = rec["thread_id"]
        state = await runner.aget_state(thread_id)
        values: Dict[str, Any] = state.get("values", {}) if state else {}
        record = TASK_STORE.get(thread_id, {})
        return {
            "session_id": session_id,
            "thread_id": thread_id,
            "status": values.get("status", rec.get("status", "running")),
            "task_type": values.get("task_type") or record.get("task_type", ""),
            "final_reply": values.get("final_reply") or rec.get("final_reply", ""),
            "products": values.get("products", record.get("products", [])),
            "step_count": values.get("step_count", record.get("step_count", 0)),
            "choices": list(values.get("choices") or []),
            "tool_log": list(values.get("tool_log") or []),
            "updated_at": rec.get("updated_at", time.time()),
        }

    @app.post("/v2/chat/{session_id}/stop")
    async def chat_stop(session_id: str):
        rec = SESSION_STORE.get(session_id)
        if not rec:
            return {"session_id": session_id, "stopped": False}
        thread_id = rec["thread_id"]
        task = running.pop(thread_id, None)
        if task:
            task.cancel()
            rec["status"] = "cancelled"
            rec["updated_at"] = time.time()
            return {"session_id": session_id, "stopped": True}
        return {"session_id": session_id, "stopped": False}

    return app


app = create_app()
