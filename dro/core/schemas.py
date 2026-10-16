"""v2 数据模型：工具调用、商品、结果、意图、State 字段。

与旧版散落的 dict 不同，这里用 dataclass + TypedDict 统一契约，
序列化交给 API 层。
"""
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Literal, Optional, TypedDict
import time
import uuid


# ---------------------------------------------------------------------------
# 意图枚举（内部稳定值，与前端中文标签通过映射转换）
# ---------------------------------------------------------------------------
INTENT_PRICE_COMPARE = "price_compare"
INTENT_FIND_SIMILAR = "find_similar"
INTENT_RECOMMEND = "recommend"
INTENT_CHAT = "chat"

VALID_INTENTS = {
    INTENT_PRICE_COMPARE,
    INTENT_FIND_SIMILAR,
    INTENT_RECOMMEND,
    INTENT_CHAT,
}

# 前端 task_type 中文 -> 内部意图
TASK_TYPE_TO_INTENT = {
    "比价": INTENT_PRICE_COMPARE,
    "找相似": INTENT_FIND_SIMILAR,
    "最优方案": INTENT_RECOMMEND,
}

INTENT_TO_TASK_TYPE = {v: k for k, v in TASK_TYPE_TO_INTENT.items()}


# ---------------------------------------------------------------------------
# 工具结果
# ---------------------------------------------------------------------------
@dataclass
class ToolResult:
    """工具统一返回结构。"""

    ok: bool
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    duration: float = 0.0

    def to_str(self) -> str:
        """给 LLM 看的人类可读结果。"""
        head = "OK" if self.ok else "ERROR"
        payload = f" | {self.data}" if self.data else ""
        return f"[{head}] {self.message}{payload}"


@dataclass
class ToolCall:
    """一次工具调用请求（LLM 生成或 planner 生成）。"""

    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 商品 & 结果
# ---------------------------------------------------------------------------
@dataclass
class Product:
    name: str = ""
    price: float = 0.0
    platform: str = ""
    sales: str = ""
    rating: str = ""
    shop_name: str = ""
    link: str = ""
    is_winner: bool = False
    reason: str = ""


@dataclass
class TaskResult:
    ok: bool
    recommendation: str = ""
    products: List[Product] = field(default_factory=list)
    intent: str = ""
    error: str = ""
    steps_used: int = 0

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return data


# ---------------------------------------------------------------------------
# LangGraph State
# ---------------------------------------------------------------------------
class AgentState(TypedDict, total=False):
    session_id: str
    user_input: str
    task_type: str          # 前端原始中文标签（可为空）
    image_path: Optional[str]
    budget_max: Optional[float]  # 从用户输入解析出的价格上限

    messages: list           # 对话历史 [{role, content}]
    intent: str              # 内部意图枚举
    memory_context: str      # 注入的记忆上下文文本
    task_plan: List[str]     # 计划步骤

    tool_calls: List[Dict[str, Any]]   # 待执行工具
    step_count: int
    collected: List[Dict[str, Any]]    # agent 收集的中间信息

    status: str              # running / done / chat / error
    final_reply: str
    products: List[Dict[str, Any]]


def tool_call_dict(name: str, **kwargs) -> Dict[str, Any]:
    return {"name": name, "arguments": kwargs}


# ---------------------------------------------------------------------------
# 任务（API 层使用）
# ---------------------------------------------------------------------------
TaskStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]


@dataclass
class Task:
    task_id: str = field(default_factory=lambda: f"{int(time.time())}-{uuid.uuid4().hex[:8]}")
    thread_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    session_id: str = "default"
    user_message: str = ""
    task_type: str = ""
    image_path: Optional[str] = None
    intent: str = ""
    status: "TaskStatus" = "pending"
    steps: List[Dict[str, Any]] = field(default_factory=list)
    result: Optional[Dict[str, Any]] = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "thread_id": self.thread_id,
            "session_id": self.session_id,
            "user_message": self.user_message,
            "task_type": self.task_type,
            "image_path": self.image_path,
            "intent": self.intent,
            "status": self.status,
            "steps": self.steps,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }