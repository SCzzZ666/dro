"""搜索商品工具：search_product

流程：
1. 用 uiautomator dump + ui.py 定位搜索框（EditText），找不到回退点击顶部固定区域。
2. 输入关键词：中文/特殊字符自动走系统剪贴板粘贴，纯 ASCII 走 input text。
3. 回车触发搜索，轮询等待结果页加载完成（替代固定 time.sleep）。
"""

from ..device.adb_client import adb, wait_for, wait_for_ui_change
from ..core.schemas import ToolResult
from ..device.ui import find_edit_box, parse_nodes
from .base import register_tool


def _find_search_box() -> tuple:
    """尝试定位搜索框，返回 (x, y) 或 (None, None)。"""
    try:
        xml = adb.dump_ui()
        if xml:
            box = find_edit_box(parse_nodes(xml))
            if box:
                from ..device.ui import center_of

                pos = center_of(box)
                if pos:
                    return pos
    except Exception:  # noqa: BLE001 —— 定位失败不阻塞，走固定坐标回退
        pass
    return None, None


def _search_box_focused() -> bool:
    """搜索框是否已获得焦点（等待输入就绪的信号）。"""
    try:
        xml = adb.dump_ui()
        if not xml:
            return False
        return any(
            n["focused"] == "true" and "EditText" in n["class"] for n in parse_nodes(xml)
        )
    except Exception:  # noqa: BLE001
        return False


@register_tool(
    name="search_product",
    description="在已打开的电商 App 的搜索框中输入关键词并搜索（中文会自动走剪贴板输入）",
)
def search_product(keyword: str) -> ToolResult:
    if not adb.ensure_online():
        return ToolResult(ok=False, message=f"设备 {getattr(adb, 'serial', '')} 未在线")

    # 1. 定位搜索框：UI 元素优先，固定坐标兜底
    x, y = _find_search_box()
    if x is None:
        _, h = adb._screen_size()
        x, y = int(1080 * 0.5), int(h * 0.08)
    adb.tap(x, y)
    wait_for(_search_box_focused, timeout=3.0, interval=0.3, description="等待搜索框聚焦")

    # 2. 输入关键词（中文自动剪贴板粘贴）
    res = adb.type_text(keyword)
    if not res.ok():
        return ToolResult(ok=False, message=f"输入关键词失败: {res.stderr or res.stdout}")

    # 3. 回车搜索，轮询结果页加载完成（相对回车前界面发生变化）
    before = adb.dump_ui()
    adb.keyevent(adb.KEY_ENTER)
    wait_for_ui_change(before, timeout=4.0, interval=0.4)
    return ToolResult(ok=True, message=f"已搜索关键词: {keyword}", data={"keyword": keyword})
