"""点击商品工具：click_product

优先策略：uiautomator dump -> 商品列表区域内的真实可点击节点 -> 点其中心坐标。
兜底策略：定位失败时按双列瀑布流坐标估算（不再依赖死坐标）。

避免误点：排除顶部搜索/筛选栏（约 0-18% 高）与底部导航栏（约 90-100% 高）。
点击后轮询详情页跳转完成（替代固定 sleep 2s），复用了定位阶段已抓取的 XML。
"""

from typing import Optional, Tuple

from ..device.adb_client import adb, wait_for_ui_change
from ..core.schemas import ToolResult
from ..device.ui import center_of, find_by_bounds_region, parse_nodes
from .base import register_tool


def _real_position(index: int, xml: str = "") -> Optional[Tuple[int, int]]:
    """通过 UI XML 定位第 index 个商品卡片中心，失败返回 None。"""
    try:
        if not xml:
            xml = adb.dump_ui()
        if not xml:
            return None
        nodes = parse_nodes(xml)
        w, h = adb._screen_size()
        region = find_by_bounds_region(
            nodes,
            x1=0,
            y1=int(h * 0.16),      # 顶部搜索/筛选栏之下
            y2=int(h * 0.92),      # 底部导航栏之上
            x2=w,
            index=index,
            only_clickable=True,
        )
        if region:
            return center_of(region)
    except Exception:  # noqa: BLE001 —— 定位失败交给兜底
        pass
    return None


def _fallback_position(index: int, w: int, h: int, xml: str = "") -> Tuple[int, int]:
    """坐标估算兜底：兼容单列（信息流）与双列（瀑布流）列表。"""
    # 尝试从 UI XML 判断列数：统计列表区域内可点击节点 x 中心分布
    try:
        if not xml:
            xml = adb.dump_ui()
        nodes = parse_nodes(xml) if xml else []
        centers = [
            center_of(n)
            for n in nodes
            if n["clickable"] == "true" and center_of(n)
        ]
        distinct_x = {c[0] for c in centers if c[1] > h * 0.16}
        if len(distinct_x) >= 2:
            cols = 2
        else:
            cols = 1
    except Exception:  # noqa: BLE001
        cols = 2

    if cols == 2:
        col_size = w // 2
        col = (index - 1) % 2
        row = (index - 1) // 2
        x = int(col_size * 0.5 + col * col_size)
        y = int(h * 0.24 + row * h * 0.3)
    else:
        x = w // 2
        y = int(h * 0.22 + (index - 1) * h * 0.24)
    return x, y


@register_tool(
    name="click_product",
    description="点击搜索结果列表中的第 N 个商品（index 从 1 开始；优先 UI 元素定位，失败回退坐标估算）",
)
def click_product(index: int = 1) -> ToolResult:
    if not adb.ensure_online():
        return ToolResult(ok=False, message=f"设备 {getattr(adb, 'serial', '')} 未在线")
    if index < 1:
        return ToolResult(ok=False, message=f"index 必须 >= 1，收到: {index}")

    w, h = adb._screen_size()
    before = adb.dump_ui()  # 点击前快照：既用于定位，也用于轮询跳转完成
    pos = _real_position(index, before)
    method = "ui_element"
    if pos is None:
        pos = _fallback_position(index, w, h, before)
        method = "fallback_coords"

    x, y = pos
    res = adb.tap(x, y)
    wait_for_ui_change(before, timeout=3.0, interval=0.4)
    if res.ok():
        return ToolResult(
            ok=True,
            message=f"已点击第 {index} 个商品（{method} 定位 {x},{y}）",
            data={"x": x, "y": y, "method": method},
        )
    return ToolResult(ok=False, message=f"点击失败: {res.stderr or res.stdout}")
