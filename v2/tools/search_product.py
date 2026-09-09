"""搜索商品工具：search_product

流程：
1. 用 uiautomator dump + ui.py 定位搜索框（EditText），找不到回退点击顶部固定区域。
2. 输入关键词：中文/特殊字符自动走系统剪贴板粘贴，纯 ASCII 走 input text。
3. 回车触发搜索。
"""

import time

from ..adb_client import adb
from ..schemas import ToolResult
from ..ui import find_edit_box, parse_nodes
from .base import register_tool


def _find_search_box() -> tuple:
    """尝试定位搜索框，返回 (x, y) 或 (None, None)。"""
    try:
        xml = adb.dump_ui()
        if xml:
            box = find_edit_box(parse_nodes(xml))
            if box:
                from ..ui import center_of

                pos = center_of(box)
                if pos:
                    return pos
    except Exception:  # noqa: BLE001 —— 定位失败不阻塞，走固定坐标回退
        pass
    return None, None


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
    else:
        adb.tap(x, y)
    time.sleep(1.2)

    # 2. 输入关键词（中文自动剪贴板粘贴）
    res = adb.type_text(keyword)
    if not res.ok():
        return ToolResult(ok=False, message=f"输入关键词失败: {res.stderr or res.stdout}")

    # 3. 回车搜索（部分 App 需要点击“搜索”按钮，回车不生效时由后续工具/重试兜底）
    time.sleep(0.6)
    adb.keyevent(adb.KEY_ENTER)
    time.sleep(2)
    return ToolResult(ok=True, message=f"已搜索关键词: {keyword}", data={"keyword": keyword})
