"""代下单 / 加购 / 商品链接 工具集。

安全护栏（重要）：
- add_to_cart / view_cart 只做「加购 / 看购物车」，不涉及资金。
- confirm_order 绝不自动点击「提交订单 / 立即支付」；仅当 pay_auth 命中会话级白名单
  才允许填地址，且仍然只停留在确认页、不点支付。真实支付必须由用户在前端二次确认后人工完成。
- get_product_deeplink 只读，抓取当前商品页的 deeplink / 商品 id。
"""

import re
from typing import List, Optional

from ..device.adb_client import adb, wait_for_ui_change
from ..core.schemas import ToolResult
from ..device.ui import center_of, find_any_text, parse_nodes
from .base import register_tool
from .click_product import click_product
from .open_app import open_app

# 会话级下单白名单：仅当 pay_auth 命中才允许「加购后自动走结算」，但绝不自动点「支付」。
_PAY_AUTH_WHITELIST: set = set()


def authorize_checkout(token: str) -> None:
    """把前端二次确认产生的 token 加入白名单（供 confirm_order 校验）。"""
    if token:
        _PAY_AUTH_WHITELIST.add(token)


def _nodes() -> List[dict]:
    xml = adb.dump_ui()
    return parse_nodes(xml) if xml else []


def _tap_button(keywords: List[str], fallback: Optional[tuple]) -> bool:
    """按关键词定位按钮并点击；找不到则用兜底坐标。返回是否执行了点击。"""
    node = find_any_text(_nodes(), keywords)
    if node:
        pos = center_of(node)
        if pos:
            adb.tap(*pos)
            return True
    if fallback:
        adb.tap(*fallback)
        return True
    return False


@register_tool(
    name="add_to_cart",
    description="点击第 N 个商品进入详情页，并点击「加入购物车」按钮（index 从 1 开始）",
)
def add_to_cart(index: int = 1) -> ToolResult:
    if not adb.ensure_online():
        return ToolResult(ok=False, message=f"设备 {getattr(adb, 'serial', '')} 未在线")
    if index < 1:
        return ToolResult(ok=False, message=f"index 必须 >= 1，收到: {index}")

    entered = click_product(index)
    if not entered.ok:
        return ToolResult(ok=False, message=f"进入商品详情页失败: {entered.message}")

    before = adb.dump_ui()
    clicked = _tap_button(
        ["加入购物车", "加入购物袋", "加购", "加入购物"],
        fallback=None,  # 加购按钮位置因 App 而异，宁可失败也不乱点
    )
    wait_for_ui_change(before, timeout=2.5, interval=0.4)
    if clicked:
        return ToolResult(ok=True, message=f"已点击第 {index} 个商品的「加入购物车」", data={"index": index})
    return ToolResult(ok=False, message="未定位到「加入购物车」按钮，可能非详情页或按钮文案不同")


@register_tool(
    name="view_cart",
    description="打开购物车页面（点击购物车入口）",
)
def view_cart() -> ToolResult:
    if not adb.ensure_online():
        return ToolResult(ok=False, message=f"设备 {getattr(adb, 'serial', '')} 未在线")
    before = adb.dump_ui()
    clicked = _tap_button(
        ["购物车", "购物袋", "去购物车"],
        fallback=None,
    )
    if not clicked:
        w, h = adb._screen_size()
        adb.tap(int(w * 0.9), int(h * 0.06))  # 顶部右上角兜底
    wait_for_ui_change(before, timeout=3.0, interval=0.4)
    return ToolResult(ok=True, message="已尝试打开购物车", data={"opened": True})


@register_tool(
    name="confirm_order",
    description="代下单确认（安全护栏：不自动点击「支付/提交订单」，仅填地址并停在确认页）",
)
def confirm_order(address: str = "", pay_auth: str = "") -> ToolResult:
    if not adb.ensure_online():
        return ToolResult(ok=False, message=f"设备 {getattr(adb, 'serial', '')} 未在线")

    # 安全护栏：未授权绝不执行任何下单动作
    if not pay_auth or pay_auth not in _PAY_AUTH_WHITELIST:
        return ToolResult(
            ok=False,
            message="代下单需二次确认：未提供有效授权 token，已拒绝自动下单，不会点击任何支付按钮。",
            data={"payment_pending": True, "authorized": False},
        )

    # 授权命中：只填地址（若当前焦点在地址框），绝不点「提交订单 / 立即支付」
    address_filled = False
    if address:
        res = adb.type_text(address)
        address_filled = res.ok()

    return ToolResult(
        ok=True,
        message=(
            "已在订单确认页填入地址（如焦点在地址框），请人工核对并完成支付；"
            "系统不会自动点击「提交订单/立即支付」。"
        ),
        data={"payment_pending": True, "authorized": True, "address_filled": address_filled},
    )


@register_tool(
    name="get_product_deeplink",
    description="在商品详情页抓取商品 deeplink / 商品 id（只读，供前端「打开链接」）",
)
def get_product_deeplink() -> ToolResult:
    if not adb.ensure_online():
        return ToolResult(ok=False, message=f"设备 {getattr(adb, 'serial', '')} 未在线")

    link = ""
    product_id = ""
    try:
        focus = adb.current_focus()
        m = re.search(r"(taobao://[^\s}\]]+|item\.m\.jd\.com[^\s}\]]+|item\.jd\.com[^\s}\]]+)", focus)
        if m:
            link = m.group(1)

        if not link:
            # 从 UI XML 找长数字商品 id 节点
            for n in _nodes():
                hay = f"{n.get('text')} {n.get('desc')} {n.get('resource_id')}"
                mid = re.search(r"\b\d{8,}\b", hay)
                if mid:
                    product_id = mid.group(0)
                    break
    except Exception as e:  # noqa: BLE001
        return ToolResult(ok=False, message=f"抓取商品链接失败: {e}")

    if not link and not product_id:
        return ToolResult(ok=False, message="未能在详情页定位到商品链接/ID，可回退为关键词搜索链接")

    return ToolResult(
        ok=True,
        message=f"已提取商品链接/ID",
        data={"link": link, "product_id": product_id, "focus": ""},
    )
