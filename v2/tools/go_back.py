"""返回上一页工具：go_back

adb input keyevent 4（BACK）。
"""
import time

from ..adb_client import adb
from ..schemas import ToolResult
from .base import register_tool


@register_tool(
    name="go_back",
    description="返回上一页（触发系统返回键）",
)
def go_back() -> ToolResult:
    if not adb.ensure_online():
        serial = getattr(adb, "serial", "")
        return ToolResult(ok=False, message=f"设备 {serial} 未在线")
    res = adb.go_back()
    time.sleep(1)
    if res.ok():
        return ToolResult(ok=True, message="已返回上一页")
    return ToolResult(ok=False, message=f"返回失败: {res.stderr or res.stdout}")