"""v2 统一配置加载。

优先级：环境变量 > .env > 默认值。
所有 keys 集中在这里，避免散落在业务代码中。
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_V2_DIR = Path(__file__).resolve().parent


def _load_env() -> None:
    for candidate in (Path.cwd(), _PROJECT_ROOT, _V2_DIR):
        env_path = candidate / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
            break


def _clean_key(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not value or value.lower() in {"your api-key", "sk-xxxxxxxxxxxxxxxx", "your_api_key"}:
        return None
    return value.strip("\"'“” ")


@dataclass
class Settings:
    """应用配置。"""

    # 模型
    dashscope_api_key: Optional[str] = None
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen3-vl-plus"
    deepseek_model: str = "deepseek-v3.2"
    embedding_model: str = "text-embedding-v3"
    embedding_dim: int = 1024

    # 设备
    device_serial: str = "127.0.0.1:16416"
    adb_binary: str = "adb"

    # 存储
    data_dir: Path = field(default_factory=lambda: _V2_DIR / "data")
    uploads_dir: Path = field(default_factory=lambda: _PROJECT_ROOT / "uploads")
    checkpoints_db: str = "checkpoints.db"
    memory_db: str = "memory.db"
    trajectories_dir: Path = field(default_factory=lambda: _PROJECT_ROOT / "trajectories")

    # LangGraph 执行限制
    max_steps: int = 15
    tool_timeout: float = 10.0
    task_timeout: float = 300.0

    # 对话记忆
    short_term_window: int = 20
    summary_threshold: int = 12

    # 可选外部存储（不配置则自动降级 SQLite）
    qdrant_url: Optional[str] = None
    qdrant_api_key: Optional[str] = None
    qdrant_collection: str = "dro_preferences"
    postgres_dsn: Optional[str] = None

    # 服务
    host: str = "0.0.0.0"
    port: int = 8001

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.trajectories_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)

    @property
    def checkpoint_path(self) -> Path:
        return self.data_dir / self.checkpoints_db

    @property
    def memory_path(self) -> Path:
        return self.data_dir / self.memory_db

    @property
    def history_window(self) -> int:
        # 兼容旧字段名 history_window
        return self.short_term_window

    @property
    def dashscope_base_url(self) -> str:
        # 兼容旧字段名 dashscope_base_url
        return self.llm_base_url


def get_settings() -> Settings:
    _load_env()
    return Settings(
        dashscope_api_key=_clean_key(os.getenv("DASHSCOPE_API_KEY")),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        qwen_model=os.getenv("QWEN_MODEL", "qwen3-vl-plus"),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-v3.2"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-v3"),
        embedding_dim=int(os.getenv("EMBEDDING_DIM", "1024")),
        device_serial=os.getenv("DEVICE_SERIAL", "127.0.0.1:16416"),
        adb_binary=os.getenv("ADB_BINARY", "adb"),
        data_dir=Path(os.getenv("DRO_DATA_DIR", str(_V2_DIR / "data"))),
        trajectories_dir=Path(os.getenv("DRO_TRAJECTORIES_DIR", str(_PROJECT_ROOT / "trajectories"))),
        checkpoints_db=os.getenv("CHECKPOINTS_DB", "checkpoints.db"),
        memory_db=os.getenv("MEMORY_DB", "memory.db"),
        max_steps=int(os.getenv("MAX_STEPS", "15")),
        tool_timeout=float(os.getenv("TOOL_TIMEOUT", "10")),
        task_timeout=float(os.getenv("TASK_TIMEOUT", "300")),
        short_term_window=int(os.getenv("SHORT_TERM_WINDOW", "20")),
        summary_threshold=int(os.getenv("SUMMARY_THRESHOLD", "12")),
        qdrant_url=os.getenv("QDRANT_URL") or None,
        qdrant_api_key=os.getenv("QDRANT_API_KEY") or None,
        qdrant_collection=os.getenv("QDRANT_COLLECTION", "dro_preferences"),
        postgres_dsn=os.getenv("POSTGRES_DSN") or None,
        host=os.getenv("DRO_HOST", "0.0.0.0"),
        port=int(os.getenv("DRO_PORT", "8001")),
    )


settings = get_settings()