"""关闭弹窗工具：close_popup

截屏 -> 视觉模型判断是否有弹窗并给出关闭按钮大致位置 -> 尝试点击关闭。
"""
import time

from ..device.adb_client import adb
from ..core.schemas import ToolResult
from ..device.vision import vision
from .base import register_tool


@register_tool(
    name="close_popup",
    description="检测并关闭屏幕上的广告/优惠券/升级弹窗",
)
def close_popup() -> ToolResult:
    if not adb.ensure_online():
        return ToolResult(ok=False, message=f"设备 {getattr(adb, 'serial', '')} 未在线")
    try:
        from .screenshot import _shot_dir

        img = _shot_dir() / f"popup_{int(time.time())}.png"
        adb.screencap(img)
        det = vision.detect_popup(img)
        if not det.get("has_popup"):
            return ToolResult(ok=True, message="未检测到弹窗", data={"detected": False})
        pos = det.get("close_button", {}).get("estimate_position", {})
        x_p, y_p = pos.get("x"), pos.get("y")
        if not x_p or not y_p:
            # 兜底：点击屏幕右上角（常见的关闭按钮位置）
            x_p, y_p = 90, 10
        w, h = adb._screen_size()
        adb.tap(int(w * x_p / 100), int(h * y_p / 100))
        time.sleep(1)
        return ToolResult(
            ok=True,
            message=f"已尝试点击关闭按钮（{x_p}%,{y_p}%）",
            data={"popup_type": det.get("popup_type", ""), "x_p": x_p, "y_p": y_p},
        )
    except Exception as e:  # noqa: BLE001
        return ToolResult(ok=False, message=f"关闭弹窗失败: {e}")