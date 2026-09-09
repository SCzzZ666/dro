"""打开 App 工具：open_app

用 adb shell monkey 启动目标 App，支持 淘宝/京东 包名映射。
"""
import time

from ..adb_client import adb
from ..schemas import ToolResult
from .base import register_tool

APP_PACKAGES = {
    "淘宝": "com.taobao.taobao",
    "京东": "com.jingdong.app.mall",
    "tb": "com.taobao.taobao",
    "jd": "com.jingdong.app.mall",
}


@register_tool(
    name="open_app",
    description="打开安卓 App，支持 淘宝/京东（app_name 传中文名或包名）",
)
def open_app(app_name: str = "淘宝") -> ToolResult:
    package = APP_PACKAGES.get(app_name.strip().lower(), app_name.strip())
    if not adb.ensure_online():
        return ToolResult(ok=False, message=f"设备 {getattr(adb, 'serial', '')} 未在线")
    res = adb.run(
        ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
        timeout=15,
    )
    time.sleep(3)
    if res.ok():
        return ToolResult(ok=True, message=f"已打开 App: {app_name}（{package}）")
    return ToolResult(ok=False, message=f"打开 {app_name} 失败: {res.stderr or res.stdout}")


# 兼容不同导入路径下的旧调用方式
def launch(app_name: str = "淘宝") -> ToolResult:
    return open_app(app_name)