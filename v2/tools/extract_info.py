"""提取商品信息工具：extract_info

截图 -> Qwen-VL 分析当前屏幕，提取商品标题/价格/销量/店铺，返回结构化 JSON 字符串。
"""
import time
from pathlib import Path

from ..adb_client import adb
from ..schemas import ToolResult
from ..vision import vision
from .base import register_tool
from .screenshot import _shot_dir


@register_tool(
    name="extract_info",
    description="对当前手机屏幕截图，并用视觉模型提取屏幕上商品的标题、价格、销量、店铺等信息",
)
def extract_info(keyword: str = "") -> ToolResult:
    if not adb.ensure_online():
        return ToolResult(ok=False, message=f"设备 {getattr(adb, 'serial', '')} 未在线")
    try:
        img_dir = _shot_dir()
        img = img_dir / f"screen_{int(time.time())}.png"
        adb.screencap(img)
        result = vision.extract_product_info(img)
        if result.get("error"):
            return ToolResult(ok=False, message=result["error"])
        return ToolResult(
            ok=True,
            message=f"屏幕分析完成: {keyword or '当前页'}",
            data={"screen": result, "screenshot": str(img)},
        )
    except Exception as e:  # noqa: BLE001
        return ToolResult(ok=False, message=f"提取屏幕信息失败: {e}")