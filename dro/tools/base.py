"""工具注册表与基类。

- AgentTool 基类：name/description/parameters + run 方法
- register_tool 装饰器：自动注册可被 LangGraph/LLM 调用的工具
- execute_tool(name, **kwargs)：统一调度入口（带超时与错误兜底）
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
import inspect
import time
from typing import Any, Callable, Dict

from ..core.schemas import ToolResult
from ..core.metrics import metrics


@dataclass
class AgentTool:
    name: str
    description: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    implementation: Callable = None

    def run(self, **kwargs) -> Any:
        if self.implementation is None:
            return ToolResult(ok=False, message=f"工具 {self.name} 未实现")
        try:
            # 只传递函数签名接受的参数
            sig = inspect.signature(self.implementation)
            accept_kwargs = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
            if accept_kwargs:
                args = kwargs
            else:
                args = {k: v for k, v in kwargs.items() if k in sig.parameters}
            value = self.implementation(**args)
            if isinstance(value, ToolResult):
                return value
            return ToolResult(ok=True, message=str(value), data={"output": value})
        except Exception as e:  # noqa: BLE001 - 工具必须兜底返回错误
            return ToolResult(ok=False, message=f"工具 {self.name} 执行失败: {e}")


REGISTRY: Dict[str, AgentTool] = {}


def register_tool(name: str, description: str = "", parameters: Dict[str, Any] = None):
    """把函数注册为可被 Agent 调用的工具。"""

    def decorator(fn: Callable):
        tool = AgentTool(
            name=name,
            description=description,
            parameters=parameters or _infer_parameters(fn),
            implementation=fn,
        )
        REGISTRY[name] = tool
        return fn

    return decorator


def _infer_parameters(fn: Callable) -> Dict[str, Any]:
    """根据函数签名推断 JSON Schema parameters。"""
    sig = inspect.signature(fn)
    properties: Dict[str, Any] = {}
    required: list = []
    for name, param in sig.parameters.items():
        default = param.default
        param_type = "string"
        if default is not None and default is not inspect.Parameter.empty:
            param_type = "number" if isinstance(default, (int, float)) else "string"
        elif param.annotation is not inspect.Parameter.empty:
            ann = param.annotation
            if ann in (int, float):
                param_type = "number"
            elif ann is bool:
                param_type = "boolean"
        properties[name] = {"type": param_type, "description": name}
        if default is inspect.Parameter.empty:
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}


def get_tool(name: str) -> AgentTool:
    return REGISTRY[name]


def list_tools() -> Dict[str, AgentTool]:
    return dict(REGISTRY)


# 模块级共享线程池：工具为同步函数，用线程池实现真实超时；复用避免每步创建/销毁线程池。
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dro-tool")


def execute_tool(name: str, timeout: float = 10.0, **kwargs) -> ToolResult:
    """统一执行入口：线程池超时保护 + 异常兜底。

    手机操作工具为同步函数，用共享线程池包装以实现真实超时（超过 timeout 秒未返回即视为超时）。
    """
    tool = REGISTRY.get(name)
    if tool is None:
        return ToolResult(ok=False, message=f"未知工具: {name}")
    started = time.monotonic()
    if timeout <= 0:
        timeout = 10.0
    future = _EXECUTOR.submit(tool.run, **kwargs)
    try:
        result = future.result(timeout=timeout)
    except FutureTimeout:
        future.cancel()
        result = ToolResult(
            ok=False,
            message=f"工具 {name} 执行超时（>{timeout}s），已中止并交由 LLM 决策重试/跳过",
        )
    except Exception as e:  # noqa: BLE001
        result = ToolResult(ok=False, message=f"工具 {name} 抛异常: {e}")
    result.duration = time.monotonic() - started
    metrics.record_tool(name, result.ok, result.duration * 1000)
    return result