"""可观测性：结构化日志 + Prometheus 风格指标（内存态，供 /v2/metrics 暴露）。

- 日志：统一走标准库 logging，替代散落的 print()
- 指标：计数器 + 时延 summary（sum/count/max），线程安全
"""
import logging
import threading
from typing import Dict

# 指标元数据：name -> (type, help)
_METRIC_META = {
    "dro_llm_requests_total": ("counter", "LLM 请求总数"),
    "dro_llm_requests_ok": ("counter", "LLM 请求成功数"),
    "dro_llm_tokens_total": ("counter", "LLM 消耗 token 总数"),
    "dro_llm_latency_ms": ("summary", "LLM 请求时延（毫秒）"),
    "dro_tool_requests_total": ("counter", "工具执行总数"),
    "dro_tool_requests_ok": ("counter", "工具执行成功数"),
    "dro_tool_latency_ms": ("summary", "工具执行时延（毫秒）"),
    "dro_task_total": ("counter", "任务总数"),
    "dro_task_done": ("counter", "任务成功完成数"),
    "dro_task_failed": ("counter", "任务失败/取消数"),
    "dro_task_steps": ("summary", "每任务执行步数"),
}


def setup_logging(level: int = logging.INFO) -> None:
    """配置根 logger（幂等），并静默 httpx/httpcore 每请求的 INFO 噪声。"""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


class Metrics:
    """线程安全的内存指标：计数器 + summary（sum/count/max）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._counters: Dict[str, float] = {}
        self._summaries: Dict[str, Dict[str, float]] = {}

    def incr(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0.0) + value

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            s = self._summaries.setdefault(name, {"sum": 0.0, "count": 0.0, "max": 0.0})
            s["sum"] += value
            s["count"] += 1.0
            s["max"] = max(s["max"], value)

    def record_llm(self, ok: bool, latency_ms: float, tokens: int = 0, model: str = "") -> None:
        self.incr("dro_llm_requests_total")
        if ok:
            self.incr("dro_llm_requests_ok")
        self.observe("dro_llm_latency_ms", latency_ms)
        if tokens:
            self.incr("dro_llm_tokens_total", float(tokens))

    def record_tool(self, name: str, ok: bool, latency_ms: float) -> None:
        self.incr("dro_tool_requests_total")
        if ok:
            self.incr("dro_tool_requests_ok")
        self.observe("dro_tool_latency_ms", latency_ms)

    def record_task(self, status: str, steps: int = 0) -> None:
        self.incr("dro_task_total")
        if status == "done":
            self.incr("dro_task_done")
        elif status in ("failed", "cancelled"):
            self.incr("dro_task_failed")
        if steps:
            self.observe("dro_task_steps", float(steps))

    def render(self) -> str:
        """输出 Prometheus 文本格式。"""
        with self._lock:
            lines: list = []
            for name in sorted(self._counters):
                mtype, help_text = _METRIC_META.get(name, ("counter", ""))
                lines.append(f"# HELP {name} {help_text}".rstrip())
                lines.append(f"# TYPE {name} {mtype}")
                lines.append(f"{name} {self._counters[name]}")
            for name in sorted(self._summaries):
                mtype, help_text = _METRIC_META.get(name, ("summary", ""))
                lines.append(f"# HELP {name} {help_text}".rstrip())
                lines.append(f"# TYPE {name} {mtype}")
                s = self._summaries[name]
                lines.append(f"{name}_sum {s['sum']}")
                lines.append(f"{name}_count {int(s['count'])}")
                lines.append(f"{name}_max {s['max']}")
            lines.append("")
        return "\n".join(lines)


metrics = Metrics()
