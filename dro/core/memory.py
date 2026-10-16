"""四层记忆系统（MemoryManager）。

- 短期记忆：SQLite 存储对话记录 + 滑动窗口
- 会话摘要：长对话自动生成摘要（LLM）
- 长期记忆：购物偏好向量存储（默认 SQLite 兜底，可选 Qdrant）
- 用户画像：结构化偏好存储（默认 SQLite 兜底，可选 PostgreSQL）

设计原则：SQLite 保证零外部依赖可用；Qdrant/PostgreSQL 通过惰性导入 + 配置检测降级。
"""
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import settings
from .llm import LLMClient, extract_json_object
from .metrics import get_logger
from . import prompts

logger = get_logger("dro.memory")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, ts);

CREATE TABLE IF NOT EXISTS summaries (
    session_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS preferences (
    user_id TEXT NOT NULL,
    pref_type TEXT NOT NULL,
    value TEXT NOT NULL,
    ts REAL NOT NULL,
    PRIMARY KEY (user_id, pref_type, value)
);

CREATE TABLE IF NOT EXISTS profiles (
    user_id TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


class SqliteStore:
    """内存数据库底层存储，线程安全（单连接 + 写锁 + WAL）。"""

    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        self._lock = threading.Lock()  # 串行化读写，避免共享连接跨线程竞争

    def execute(self, sql: str, params: tuple = ()) -> bool:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            cur.close()
        return True

    def query(self, sql: str, params: tuple = ()) -> List[tuple]:
        with self._lock:
            cur = self.conn.execute(sql, params)
            rows = cur.fetchall()
            cur.close()
        return rows


class ShortTermMemory:
    """短期记忆：对话记录 + 滑动窗口。"""

    def __init__(self, store: SqliteStore, window: int = None):
        self.store = store
        self.window = window or settings.short_term_window

    def add(self, session_id: str, role: str, content: str):
        self.store.execute(
            "INSERT INTO messages (session_id, role, content, ts) VALUES (?,?,?,?)",
            (session_id, role, content, time.time()),
        )

    def get(self, session_id: str, limit: int = None) -> List[Dict[str, str]]:
        rows = self.store.query(
            "SELECT role, content FROM messages WHERE session_id=? ORDER BY ts DESC LIMIT ?",
            (session_id, limit or self.window),
        )
        return [{"role": r, "content": c} for r, c in reversed(rows)]


class SummaryMemory:
    """会话摘要：消息数超过阈值时用 LLM 压缩为摘要。"""

    def __init__(self, store: SqliteStore, llm: Optional[LLMClient] = None, threshold: int = None):
        self.store = store
        self.threshold = threshold or settings.summary_threshold
        self._llm = llm

    def maybe_summarize(self, session_id: str, messages: List[Dict[str, str]]) -> Optional[str]:
        """消息超过阈值则生成/更新摘要。"""
        if len(messages) < self.threshold:
            return None
        conversation = "\n".join(f"{m['role']}: {m['content']}" for m in messages[-20:])
        try:
            if self._llm is None:
                self._llm = LLMClient()
            summary = self._llm.chat(
                [
                    {"role": "system", "content": prompts.SUMMARY_SYSTEM},
                    {"role": "user", "content": prompts.SUMMARY_HUMAN.format(conversation=conversation)},
                ]
            ).strip()
        except Exception as e:  # noqa: BLE001
            summary = f"[摘要生成失败: {e}]"
        self.store.execute(
            "INSERT INTO summaries (session_id, summary, ts) VALUES (?,?,?)",
            (session_id, summary, time.time()),
        )
        return summary

    def latest(self, session_id: str) -> str:
        rows = self.store.query(
            "SELECT summary FROM summaries WHERE session_id=? ORDER BY ts DESC LIMIT 1",
            (session_id,),
        )
        return rows[0][0] if rows else ""


class LongTermMemory:
    """长期偏好记忆：默认 SQLite；配置了 Qdrant 时使用向量检索。

    - 写入：SQLite 恒写（持久化、零依赖可恢复）；Qdrant 可用时同步 upsert 向量。
    - 检索：向量检索（Qdrant + embedding）优先；不可用/失败回退 SQLite 全量偏好。
    - 向量：DashScope text-embedding（OpenAI 兼容 /embeddings），维度见 settings.embedding_dim。
    """

    def __init__(self, store: SqliteStore, user_id: str):
        self.store = store
        self.user_id = user_id
        self._qdrant = None
        self._qdrant_tried = False
        self._embedder = None  # 惰性构建：仅在需要向量化时初始化

    def _get_qdrant(self):
        """惰性构建 Qdrant 客户端（首次使用时连接，失败降级 SQLite 并缓存结果）。"""
        if self._qdrant_tried:
            return self._qdrant
        self._qdrant_tried = True
        if settings.qdrant_url:
            try:
                from qdrant_client import QdrantClient
                from qdrant_client.models import Distance, VectorParams
                self._qdrant = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
                self._Distance, self._VectorParams = Distance, VectorParams
                self._ensure_collection()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Qdrant 初始化失败，降级为 SQLite: {e}")
                self._qdrant = None
        return self._qdrant

    def _get_embedder(self) -> Optional[LLMClient]:
        """惰性构建向量化器；无 API Key / 失败返回 None。"""
        if self._embedder is None:
            try:
                self._embedder = LLMClient()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"向量化器不可用（长期偏好检索回退 SQLite）: {e}")
                self._embedder = None
        return self._embedder

    def _ensure_collection(self):
        existing = [c.name for c in self._qdrant.get_collections().collections]
        if settings.qdrant_collection not in existing:
            self._qdrant.create_collection(
                collection_name=settings.qdrant_collection,
                vectors_config=self._VectorParams(size=settings.embedding_dim, distance=self._Distance.COSINE),
            )

    @staticmethod
    def _point_id(user_id: str, pref_type: str, value: str) -> str:
        """确定性点 ID（跨进程稳定），保证同一偏好重复写入幂等。"""
        return hashlib.md5(f"{user_id}|{pref_type}|{value}".encode("utf-8")).hexdigest()

    def add(self, pref_type: str, value: str):
        # SQLite 恒写：持久化、零依赖可恢复
        self.store.execute(
            "INSERT OR IGNORE INTO preferences (user_id, pref_type, value, ts) VALUES (?,?,?,?)",
            (self.user_id, pref_type, value, time.time()),
        )
        self._upsert_vector(pref_type, value)

    def _upsert_vector(self, pref_type: str, value: str):
        """Qdrant 向量写入；失败不阻塞主流程（SQLite 已是事实来源）。"""
        qdrant = self._get_qdrant()
        if qdrant is None or self._get_embedder() is None:
            return
        try:
            from qdrant_client.models import PointStruct
            vec = self._embedder.embed_texts([f"{pref_type}: {value}"])[0]
            qdrant.upsert(
                collection_name=settings.qdrant_collection,
                points=[
                    PointStruct(
                        id=self._point_id(self.user_id, pref_type, value),
                        vector=vec,
                        payload={"user_id": self.user_id, "pref_type": pref_type, "value": value},
                    )
                ],
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"偏好向量写入 Qdrant 失败（不影响 SQLite）: {e}")

    def search(self, query: str, top_k: int = 5) -> List[str]:
        """检索相关偏好：向量检索优先，失败/不可用回退 SQLite。"""
        if self._get_qdrant() is not None and self._get_embedder() is not None:
            hits = self._search_vector(query, top_k)
            if hits:
                return hits
        rows = self.store.query(
            "SELECT pref_type, value FROM preferences WHERE user_id=? ORDER BY ts DESC LIMIT ?",
            (self.user_id, top_k * 4),
        )
        return [f"{t}: {v}" for t, v in rows][:top_k]

    def replace(self, pref_type: str, value: str):
        """单值偏好显式更正：删除该类型旧值后写入，覆盖而非追加。"""
        self.store.execute(
            "DELETE FROM preferences WHERE user_id=? AND pref_type=?",
            (self.user_id, pref_type),
        )
        self.add(pref_type, value)

    def _search_vector(self, query: str, top_k: int) -> List[str]:
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            vec = self._embedder.embed_texts([query])[0]
            query_filter = Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=self.user_id))])
            points = self._qdrant_query(vec, top_k, query_filter)
            return [f"{p.payload.get('pref_type')}: {p.payload.get('value')}" for p in points if p.payload]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Qdrant 向量检索失败，回退 SQLite: {e}")
            return []

    def _qdrant_query(self, vec, top_k, query_filter):
        """兼容新旧 qdrant-client：query_points 优先，search 兜底。"""
        if hasattr(self._qdrant, "query_points"):
            return self._qdrant.query_points(
                collection_name=settings.qdrant_collection,
                query=vec,
                limit=top_k,
                query_filter=query_filter,
            ).points
        return self._qdrant.search(
            collection_name=settings.qdrant_collection,
            query_vector=vec,
            limit=top_k,
            query_filter=query_filter,
        )


class ProfileStore:
    """用户画像：默认 SQLite；配置了 PostgreSQL 时使用结构化存储。"""

    def __init__(self, store: SqliteStore, user_id: str):
        self.store = store
        self.user_id = user_id
        self._pg = None
        self._pg_tried = False

    def _get_pg(self):
        """惰性构建 PostgreSQL 连接（首次使用时连接，失败降级 SQLite 并缓存结果）。"""
        if self._pg_tried:
            return self._pg
        self._pg_tried = True
        if settings.postgres_dsn:
            try:
                import psycopg  # type: ignore 惰性导入
                self._pg = psycopg.connect(settings.postgres_dsn)
                self._ensure_pg()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"PostgreSQL 初始化失败，降级为 SQLite: {e}")
                self._pg = None
        return self._pg

    def _ensure_pg(self):
        cur = self._pg.cursor()  # type: ignore
        cur.execute(
            "CREATE TABLE IF NOT EXISTS profiles (user_id TEXT PRIMARY KEY, profile_json TEXT, updated_at TIMESTAMP DEFAULT now())"
        )
        self._pg.commit()
        cur.close()

    def save(self, profile: Dict[str, Any]):
        value = json.dumps(profile, ensure_ascii=False)
        self.store.execute(
            "INSERT OR REPLACE INTO profiles (user_id, profile_json, updated_at) VALUES (?,?,?)",
            (self.user_id, value, time.time()),
        )
        pg = self._get_pg()
        if pg is not None:
            cur = pg.cursor()
            cur.execute(
                "INSERT INTO profiles (user_id, profile_json) VALUES (%s,%s) "
                "ON CONFLICT (user_id) DO UPDATE SET profile_json=EXCLUDED.profile_json",
                (self.user_id, value),
            )
            pg.commit()
            cur.close()

    def load(self) -> Dict[str, Any]:
        rows = self.store.query(
            "SELECT profile_json FROM profiles WHERE user_id=? LIMIT 1", (self.user_id,)
        )
        if not rows:
            return {}
        try:
            return json.loads(rows[0][0])
        except json.JSONDecodeError:
            return {}


class MemoryManager:
    """四层记忆的统一门面。"""

    def __init__(self, user_id: str, store: Optional[SqliteStore] = None):
        self.user_id = user_id
        self.store = store or _default_store()
        self.short_term = ShortTermMemory(self.store)
        self.summary = SummaryMemory(self.store)
        self.long_term = LongTermMemory(self.store, user_id)
        self.profile = ProfileStore(self.store, user_id)
        self._llm: Optional[LLMClient] = None

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------
    def add_user_message(self, session_id: str, content: str):
        self.short_term.add(session_id, "user", content)

    def add_assistant_message(self, session_id: str, content: str):
        self.short_term.add(session_id, "assistant", content)

    def add_message(self, session_id: str, role: str, content: str):
        """统一入口（graph.py 使用）：按 role 写入对话记录。"""
        self.short_term.add(session_id, role, content)

    def get_history(self, session_id: str) -> List[Dict[str, str]]:
        msgs = self.short_term.get(session_id)
        self.summary.maybe_summarize(session_id, msgs)
        return msgs

    # ------------------------------------------------------------------
    # 偏好提取（LLM + 正则兜底）
    # ------------------------------------------------------------------
    def save_preference(self, key: str, value: str):
        """graph.py 使用的偏好写入入口。"""
        if key and value:
            self.long_term.add(key, value)

    def update_profile(self, prefs: Dict[str, Any]):
        """graph.py 使用的画像更新入口。"""
        if not prefs:
            return
        profile = self.profile.load()
        profile.update({k: v for k, v in prefs.items() if v})
        self.profile.save(profile)

    async def maybe_summarize(self, session_id: str, llm=None):
        """异步摘要：消息数超阈值时压缩会话（graph.py 中 await 调用）。

        llm 可为 AsyncLLMClient；无 llm 或失败时降级为规则摘要。
        """
        msgs = self.short_term.get(session_id)
        if len(msgs) < (self.summary.threshold if self.summary else settings.summary_threshold):
            return None
        conversation = "\n".join(f"{m['role']}: {m['content']}" for m in msgs[-20:])
        summary = ""
        try:
            if llm is not None and hasattr(llm, "achat"):
                summary = (await llm.achat(
                    [
                        {"role": "system", "content": prompts.SUMMARY_SYSTEM},
                        {"role": "user", "content": prompts.SUMMARY_HUMAN.format(conversation=conversation)},
                    ]
                )).strip()
            else:
                if self._llm is None:
                    self._llm = LLMClient()
                summary = self._llm.chat(
                    [
                        {"role": "system", "content": prompts.SUMMARY_SYSTEM},
                        {"role": "user", "content": prompts.SUMMARY_HUMAN.format(conversation=conversation)},
                    ]
                ).strip()
        except Exception as e:  # noqa: BLE001
            summary = f"[摘要生成失败: {e}]"
        self.store.execute(
            "INSERT INTO summaries (session_id, summary, ts) VALUES (?,?,?)",
            (session_id, summary, time.time()),
        )
        return summary

    def extract_and_store_preferences(self, text: str):
        profile = self.profile.load()
        # 降频：无偏好信号时跳过 LLM 调用，直接返回已有画像
        if not _has_preference_signal(text):
            return profile
        try:
            if self._llm is None:
                self._llm = LLMClient()
            result = self._llm.chat_json(
                [
                    {"role": "system", "content": prompts.PREFERENCE_SYSTEM},
                    {
                        "role": "user",
                        "content": prompts.PREFERENCE_HUMAN.format(message=text, profile=json.dumps(profile, ensure_ascii=False)),
                    },
                ]
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"偏好提取失败（回退规则解析）: {e}")
            result = _regex_preferences(text)

        # 单值偏好（预算/颜色/尺寸）显式更正：覆盖而非追加；多值（品牌/平台）追加
        for key in ("budget", "color", "size"):
            value = str(result.get(key) or "").strip()
            if value:
                self.long_term.replace(key, value)
                profile[key] = value
        for key in ("brand", "platform", "other"):
            value = str(result.get(key) or "").strip()
            if value:
                self.long_term.add(key, value)
                profile[key] = value
        if profile:
            self.profile.save(profile)
        return profile

    # ------------------------------------------------------------------
    # 上下文注入
    # ------------------------------------------------------------------
    def build_prompt(self, session_id: str) -> str:
        """为节点组装记忆上下文文本（供 system prompt 注入）。"""
        parts: List[str] = []
        summary = self.summary.latest(session_id)
        if summary:
            parts.append(f"[会话摘要] {summary}")
        prefs = self.long_term.search(self.user_id)
        if prefs:
            parts.append("[长期偏好] " + "；".join(prefs))
        profile = self.profile.load()
        if profile:
            subs = {k: v for k, v in profile.items() if v}
            if subs:
                parts.append("[用户画像] " + json.dumps(subs, ensure_ascii=False))
        return "\n".join(parts)


_STORE_SINGLETON: Optional[SqliteStore] = None
_STORE_LOCK = threading.Lock()


def _default_store() -> SqliteStore:
    """进程级 SqliteStore 单例：所有 MemoryManager 复用同一连接，避免每次开连接。"""
    global _STORE_SINGLETON
    if _STORE_SINGLETON is None:
        with _STORE_LOCK:
            if _STORE_SINGLETON is None:
                _STORE_SINGLETON = SqliteStore(settings.data_dir / settings.memory_db)
    return _STORE_SINGLETON


_PREF_SIGNAL_KEYWORDS = (
    "预算", "颜色", "色", "品牌", "尺寸", "码数", "喜欢", "偏好", "想要", "要买",
    "买一个", "买个", "红", "蓝", "黑", "白", "绿", "粉", "小号", "大号", "中号",
)


def _has_preference_signal(text: str) -> bool:
    """判断文本是否含明显偏好信号，用于对偏好提取 LLM 调用降频。"""
    return any(k in (text or "") for k in _PREF_SIGNAL_KEYWORDS)


def _regex_preferences(text: str) -> Dict[str, str]:
    """规则兜底：从文本中尽量提取预算/颜色/品牌。"""
    out: Dict[str, str] = {}
    import re
    m = re.search(r"([1-9]\d{2,4})(?:元|块|以内|左右)?", text)
    if m:
        out["budget"] = f"预算{m.group(1)}元以内"
    for color in ("红色", "蓝色", "黑色", "白色", "黄色", "绿色", "粉色"):
        if color in text:
            out["color"] = color
            break
    return out