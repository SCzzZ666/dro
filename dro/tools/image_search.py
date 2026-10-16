"""以图搜图工具：image_search

流程（尽力而为）：
1. 打开淘宝，定位「扫一扫 / 相机 / 拍照 / 识图」入口并点击。
2. 把本地图片推到设备 sdcard，供系统图库/相册上传选择（真正上传需设备侧完成）。
3. 等待识图结果页加载，复用 extract_info 采集。

说明：识图入口与上传交互因 App 版本而异，本工具保证「入口已调起 + 图片已就位」，
后续上传选择/识图结果由后续工具或人工兜底，不依赖绝对死坐标。
"""

from pathlib import Path

from ..device.adb_client import adb, wait_for_ui_change
from ..core.schemas import ToolResult
from ..device.ui import center_of, find_any_text, parse_nodes
from .base import register_tool
from .open_app import open_app


@register_tool(
    name="image_search",
    description="以图搜图：打开淘宝识图/扫一扫入口并准备图片上传（尽力而为）",
)
def image_search(image_path: str) -> ToolResult:
    if not adb.ensure_online():
        return ToolResult(ok=False, message=f"设备 {getattr(adb, 'serial', '')} 未在线")
    if not image_path or not Path(image_path).exists():
        return ToolResult(ok=False, message=f"图片不存在: {image_path}")

    # 1. 打开淘宝
    opened = open_app("淘宝")
    if not opened.ok:
        return ToolResult(ok=False, message=f"打开淘宝失败: {opened.message}")

    # 2. 定位识图/扫一扫入口
    before = adb.dump_ui()
    xml = before or ""
    node = find_any_text(
        parse_nodes(xml) if xml else [],
        ["扫一扫", "拍照", "相机", "识图", "扫图"],
    )
    entry = "ui_element"
    if node:
        pos = center_of(node)
        if pos:
            adb.tap(*pos)
        else:
            entry = "fallback"
            w, h = adb._screen_size()
            adb.tap(int(w * 0.9), int(h * 0.08))
    else:
        entry = "fallback"
        w, h = adb._screen_size()
        adb.tap(int(w * 0.9), int(h * 0.08))  # 顶部右上角常见入口

    wait_for_ui_change(before, timeout=4.0, interval=0.4)

    # 3. 把图片推到设备 sdcard，供相册上传选择（真正选择/上传需设备侧完成）
    pushed = False
    try:
        device_path = "/sdcard/dro_search_image.jpg"
        res = adb.run(["push", str(Path(image_path)), device_path], timeout=20)
        pushed = res.ok()
    except Exception:  # noqa: BLE001
        pushed = False

    return ToolResult(
        ok=True,
        message=(
            f"已调起识图/扫一扫入口（{entry}），图片"
            f"{'已推送到设备 ' + device_path if pushed else '未推送成功，请检查图片'}"
        ),
        data={"entry": entry, "image_pushed": pushed, "device_path": device_path if pushed else ""},
    )
