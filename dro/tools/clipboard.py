"""剪贴板输入工具：paste_text

adb input text 无法输入中文，此工具把文本写入系统剪贴板后触发粘贴。
适用场景：中文搜索词、地址、备注等任意文本输入。
"""

import time

from ..device.adb_client import adb
from ..core.schemas import ToolResult
from .base import register_tool


@register_tool(
    name="paste_text",
    description="把文本写入系统剪贴板并粘贴到当前聚焦的输入框（支持中文，需先点中搜索框/输入框）",
)
def paste_text(text: str) -> ToolResult:
    if not text:
        return ToolResult(ok=False, message="paste_text 缺少 text 参数")
    if not adb.ensure_online():
        return ToolResult(ok=False, message=f"设备 {getattr(adb, 'serial', '')} 未在线")
    res = adb.set_clipboard(text)
    if not res.ok():
        return ToolResult(
            ok=False,
            message=f"写入剪贴板失败（系统可能低于 Android 10）: {res.stderr or res.stdout}",
        )
    time.sleep(0.4)
    paste = adb.paste()
    if not paste.ok():
        return ToolResult(ok=False, message=f"触发粘贴失败: {paste.stderr or paste.stdout}")
    time.sleep(0.5)
    return ToolResult(ok=True, message=f"已通过剪贴板粘贴: {text[:30]}{'…' if len(text) > 30 else ''}")
