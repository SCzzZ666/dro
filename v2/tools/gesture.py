"""屏幕手势工具：swipe_up / swipe_down / swipe_left / swipe_right

解决“只能看第一屏”的问题：搜索结果列表需要上滑加载更多、下滑回看，
图片/详情轮播需要左右滑动切换。
"""

import time

from ..adb_client import adb
from ..schemas import ToolResult
from .base import register_tool


def _swipe(direction: str, times: int, tag: str) -> ToolResult:
    if not adb.ensure_online():
        return ToolResult(ok=False, message=f"设备 {getattr(adb, 'serial', '')} 未在线")
    times = max(1, int(times or 1))
    for _ in range(times):
        res = adb.swipe(direction, duration=400)
        if not res.ok():
            return ToolResult(ok=False, message=f"{tag}失败: {res.stderr or res.stdout}")
        time.sleep(1.0)
    return ToolResult(ok=True, message=f"已{tag} {times} 次", data={"direction": direction, "times": times})


@register_tool(
    name="swipe_up",
    description="上滑屏幕（浏览商品列表下方内容/加载更多），times 为滑动次数，默认 1",
)
def swipe_up(times: int = 1) -> ToolResult:
    return _swipe("up", times, "上滑")


@register_tool(
    name="swipe_down",
    description="下滑屏幕（回到列表上方），times 为滑动次数，默认 1",
)
def swipe_down(times: int = 1) -> ToolResult:
    return _swipe("down", times, "下滑")


@register_tool(
    name="swipe_left",
    description="向左滑动屏幕（切换图片/横向轮播），times 为滑动次数，默认 1",
)
def swipe_left(times: int = 1) -> ToolResult:
    return _swipe("left", times, "左滑")


@register_tool(
    name="swipe_right",
    description="向右滑动屏幕（切换图片/返回上一张），times 为滑动次数，默认 1",
)
def swipe_right(times: int = 1) -> ToolResult:
    return _swipe("right", times, "右滑")
