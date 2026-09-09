"""截图工具：screenshot

截取当前手机屏幕保存到 trajectories/<thread>/ 目录，返回图片路径。
"""
import time
from pathlib import Path

from ..adb_client import adb
from ..schemas import ToolResult
from .base import register_tool

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TRAJECTORIES_DIR = _PROJECT_ROOT / "trajectories"


def _shot_dir() -> Path:
    d = TRAJECTORIES_DIR / time.strftime("%Y%m%d")
    d.mkdir(parents=True, exist_ok=True)
    return d


@register_tool(
    name="screenshot",
    description="截取当前手机屏幕，保存到 trajectories 目录，返回截图路径",
)
def screenshot() -> ToolResult:
    if not adb.ensure_online():
        return ToolResult(ok=False, message=f"设备 {getattr(adb, 'serial', '')} 未在线")
    try:
        img = _shot_dir() / f"step_{int(time.time() * 1000)}.png"
        adb.screencap(img)
        return ToolResult(ok=True, message=f"截图已保存: {img}", data={"path": str(img)})
    except Exception as e:  # noqa: BLE001
        return ToolResult(ok=False, message=f"截图失败: {e}")