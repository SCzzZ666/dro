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
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .pages import render_chat_page, render_home_page

from ..core.config import settings
from ..core.graph import TaskRunner
from ..core.metrics import metrics, setup_logging
from ..tools import list_tools

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def _is_valid_image(content: bytes, ext: str) -> bool:
    """魔数校验：判断文件内容与扩展名是否匹配。"""
    if ext in (".jpg", ".jpeg"):
        return content.startswith(b"\xff\xd8\xff")
    if ext == ".png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == ".gif":
        return content.startswith((b"GIF87a", b"GIF89a"))
    if ext == ".webp":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    if ext == ".bmp":
        return content.startswith(b"BM")
    return False


class TaskRequest(BaseModel):
    user_input: str = Field(..., description="用户输入")
    task_type: str = Field("", description="前端任务类型（比价/找相似/最优方案，可为空）")
    image_path: Optional[str] = Field(None, description="图片路径（找相似用）")
    thread_id: Optional[str] = Field(None, description="复用会话线程（多轮对话）")
    model: str = Field("", description="模型选择（空/auto 用默认；deepseek/qwen 映射具体名）")


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
    model: str = Field("", description="模型选择（空/auto 用默认；deepseek/qwen 映射具体名）")


class ChatCreateResponse(BaseModel):
    session_id: str
    thread_id: str
    status: str


TASK_STORE: Dict[str, Dict[str, Any]] = {}
# 会话 -> 当前活跃任务线程（一个会话可有多轮消息，每轮一个任务线程）
SESSION_STORE: Dict[str, Dict[str, Any]] = {}

# ---- 任务/会话元数据持久化（2.6）：重启后任务列表仍可见 ----
_STATE_DB = settings.data_dir / "state.db"
_STATE_LOCK = threading.Lock()

_STATIC_DIR = Path(__file__).resolve().parent / "static"

_MAX_UPLOAD_SIZE = 8 * 1024 * 1024  # 8MB


def _state_table_sql(table: str) -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {table} "
        "(id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at REAL NOT NULL)"
    )


def _load_state(table: str) -> Dict[str, Dict[str, Any]]:
    """启动时从 SQLite 恢复任务/会话元数据。"""
    conn = sqlite3.connect(str(_STATE_DB), check_same_thread=False)
    try:
        conn.execute(_state_table_sql(table))
        conn.commit()
        rows = conn.execute(f"SELECT id, data FROM {table}").fetchall()
        out: Dict[str, Dict[str, Any]] = {}
        for key, data in rows:
            try:
                out[key] = json.loads(data)
            except json.JSONDecodeError:
                continue
        return out
    finally:
        conn.close()


def _persist_state(table: str, key: str, data: Dict[str, Any]) -> None:
    """把单个任务/会话元数据写入 SQLite（创建/完成时调用）。"""
    if not key:
        return
    payload = dict(data)
    payload.setdefault("updated_at", time.time())
    with _STATE_LOCK:
        conn = sqlite3.connect(str(_STATE_DB), check_same_thread=False)
        try:
            conn.execute(_state_table_sql(table))
            conn.execute(
                f"INSERT OR REPLACE INTO {table} (id, data, updated_at) VALUES (?,?,?)",
                (key, json.dumps(payload, ensure_ascii=False), payload.get("updated_at", time.time())),
            )
            conn.commit()
        finally:
            conn.close()


TASK_STORE.update(_load_state("tasks"))
SESSION_STORE.update(_load_state("sessions"))


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
    """从 memory 库列出最近会话（单条 SQL，消除 N+1）。"""
    conn = _memory_conn()
    try:
        rows = conn.execute(
            """
            WITH ranked AS (
                SELECT session_id, role, content, ts,
                       ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY ts DESC) AS rn,
                       COUNT(*) OVER (PARTITION BY session_id) AS cnt,
                       MAX(ts) OVER (PARTITION BY session_id) AS last_ts
                FROM messages
                WHERE session_id LIKE 'sess_%'
            )
            SELECT session_id, last_ts, role AS last_role, content AS last_content, cnt AS message_count
            FROM ranked
            WHERE rn = 1
            ORDER BY last_ts DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            {
                "session_id": r["session_id"],
                "last_ts": r["last_ts"],
                "last_role": r["last_role"] or "",
                "last_content": (r["last_content"] or "")[:60],
                "message_count": r["message_count"],
            }
            for r in rows
        ]
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


def create_app() -> FastAPI:
    app = FastAPI(title="Dro v2", version="2.0.0", description="LangGraph + 工具化 ADB 执行引擎")

    runner = TaskRunner()
    running: Dict[str, asyncio.Task] = {}
    setup_logging()

    @app.middleware("http")
    async def _auth(request, call_next):
        """可选鉴权：配置 DRO_API_TOKEN 后，/v2/* 需 Bearer 校验（缺省关闭）。"""
        token = settings.dro_api_token
        if token and request.url.path.startswith("/v2/"):
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {token}":
                return JSONResponse(status_code=401, content={"detail": "未授权：缺少或错误的 DRO_API_TOKEN"})
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    async def root():
        return render_home_page()

    @app.get("/chat", response_class=HTMLResponse)
    async def chat_page():
        return render_chat_page()

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "version": "2.0.0"}

    @app.get("/v2/metrics")
    async def metrics_endpoint():
        """Prometheus 文本格式指标：请求数 / 成功率 / 时延 / token 消耗。"""
        return Response(content=metrics.render(), media_type="text/plain; version=0.0.4")

    @app.post("/v2/upload")
    async def upload_image(file: UploadFile = File(...)):
        filename = (file.filename or "").lower()
        ext = Path(filename).suffix
        if ext not in ALLOWED_IMAGE_EXTS:
            raise HTTPException(status_code=400, detail="仅支持 JPG / PNG / WebP / GIF / BMP 图片")
        content = await file.read(_MAX_UPLOAD_SIZE + 1)
        if not content:
            raise HTTPException(status_code=400, detail="文件内容为空")
        if len(content) > _MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=413, detail=f"文件超过 {_MAX_UPLOAD_SIZE // (1024 * 1024)}MB 上限")
        if not _is_valid_image(content, ext):
            raise HTTPException(status_code=400, detail="文件内容与扩展名不匹配（魔数校验失败）")
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
        _persist_state("tasks", thread_id, TASK_STORE[thread_id])

        async def _run():
            TASK_STORE[thread_id]["status"] = "running"
            TASK_STORE[thread_id]["updated_at"] = time.time()
            try:
                out = await runner.ainvoke(
                    user_input=req.user_input,
                    task_type=req.task_type,
                    image_path=req.image_path,
                    thread_id=thread_id,
                    model=req.model,
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
                _persist_state("tasks", thread_id, TASK_STORE.get(thread_id, {}))

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
            model = rec.get("model", "")
            TASK_STORE[thread_id]["status"] = "running"
            SESSION_STORE[session_id]["status"] = "running"
            try:
                out = await runner.ainvoke(
                    user_input=message,
                    task_type=task_type,
                    image_path=image_path,
                    thread_id=thread_id,
                    session_id=session_id,
                    model=model,
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
                _persist_state("tasks", thread_id, TASK_STORE.get(thread_id, {}))
                _persist_state("sessions", session_id, SESSION_STORE.get(session_id, {}))

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
            "model": req.model,
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
            "model": req.model,
            "updated_at": time.time(),
        }
        _persist_state("tasks", thread_id, TASK_STORE[thread_id])
        _persist_state("sessions", session_id, SESSION_STORE[session_id])
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

    # ------------------------------------------------------------------
    # SSE 实时推送（3.2）：替代 900ms / 1500ms 轮询，前端一次长连接 + 事件推送
    # ------------------------------------------------------------------
    _TERMINAL = ("done", "needs_input", "failed", "cancelled")

    @app.get("/v2/chat/{session_id}/stream")
    async def chat_stream(session_id: str):
        async def gen():
            rec = SESSION_STORE.get(session_id)
            if not rec:
                yield f"event: done\ndata: {json.dumps({'status': 'unknown'}, ensure_ascii=False)}\n\n"
                return
            thread_id = rec["thread_id"]
            cursor = 0
            while True:
                if thread_id in running:
                    status = "running"
                else:
                    status = rec.get("status", "queued")
                state = await runner.aget_state(thread_id)
                values = state.get("values", {}) if state else {}
                tool_log = list(values.get("tool_log") or [])
                if state:
                    status = values.get("status", status)
                for entry in tool_log[cursor:]:
                    yield (
                        "event: progress\n"
                        f"data: {json.dumps({'name': entry.get('name', ''), 'ok': entry.get('ok', True), 'message': (entry.get('message') or '')[:160]}, ensure_ascii=False)}\n\n"
                    )
                cursor = max(cursor, len(tool_log))
                if status in _TERMINAL and thread_id not in running:
                    yield f"event: done\ndata: {json.dumps({'status': status}, ensure_ascii=False)}\n\n"
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/v2/tasks/{thread_id}/events")
    async def task_events(thread_id: str):
        async def gen():
            while True:
                state = await runner.aget_state(thread_id)
                values = state.get("values", {}) if state else {}
                record = TASK_STORE.get(thread_id, {})
                status = values.get("status", record.get("status", "running"))
                task = {
                    "thread_id": thread_id,
                    "status": status,
                    "task_type": values.get("task_type") or record.get("task_type", ""),
                    "user_input": values.get("user_input") or record.get("user_input", ""),
                    "final_reply": values.get("final_reply", record.get("final_reply", "")),
                    "products": values.get("products", record.get("products", [])),
                    "step_count": values.get("step_count", record.get("step_count", 0)),
                    "next": list(state.get("next", [])) if state else [],
                    "updated_at": record.get("updated_at", time.time()),
                }
                yield f"event: update\ndata: {json.dumps(task, ensure_ascii=False)}\n\n"
                if status in _TERMINAL and thread_id not in running:
                    yield "event: done\ndata: {}\n\n"
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(gen(), media_type="text/event-stream")

    # 静态资源（CSS / JS）
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    return app


app = create_app()
