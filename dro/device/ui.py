"""uiautomator dump XML 解析：根据文本/资源 id 定位可点击元素坐标。

解决“固定坐标点不准”的核心：优先用 UI 元素定位，找不到再回退固定坐标。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple


def _bounds_to_center(bounds: str) -> Optional[Tuple[int, int]]:
    """把 bounds="[x1,y1][x2,y2]" 转为中心点坐标。"""
    m = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    x1, y1, x2, y2 = (int(g) for g in m.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def parse_nodes(xml: str) -> List[Dict[str, str]]:
    """从 dump XML 提取所有 node 的常用属性。"""
    nodes: List[Dict[str, str]] = []
    for m in re.finditer(r"<node[^>]*>", xml):
        attrs = m.group(0)
        node = {
            "text": _attr(attrs, "text"),
            "desc": _attr(attrs, "content-desc"),
            "resource_id": _attr(attrs, "resource-id"),
            "class": _attr(attrs, "class"),
            "hint": _attr(attrs, "hint"),
            "clickable": _attr(attrs, "clickable", "false"),
            "enabled": _attr(attrs, "enabled", "true"),
            "focused": _attr(attrs, "focused", "false"),
            "bounds": _attr(attrs, "bounds"),
        }
        if node["bounds"]:
            nodes.append(node)
    return nodes


def _attr(attrs: str, name: str, default: str = "") -> str:
    m = re.search(rf'{name}="([^"]*)"', attrs)
    return m.group(1) if m else default


def find_edit_box(nodes: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """定位可输入的搜索/输入框（class=EditText）。

    优先级：resource-id 含 search/keyword > hint 含 搜索/输入 > 任意 EditText。
    """
    edits = [n for n in nodes if "EditText" in n["class"] and n["enabled"] != "false"]
    if not edits:
        return None
    for n in edits:
        rid = f"{n['resource_id']} {n['hint']} {n['desc']} {n['text']}"
        if any(k in rid for k in ("search", "keyword", "查询", "搜索")):
            return n
    # 兜底：屏幕上最靠上的可输入框
    return min(edits, key=lambda n: (_bounds_to_center(n["bounds"]) or (0, 0))[1])


def find_by_bounds_region(
    nodes: List[Dict[str, str]],
    x1: int, y1: int, x2: int, y2: int,
    index: int = 1,
    only_clickable: bool = True,
) -> Optional[Dict[str, str]]:
    """在给定像素矩形区域内，按 y 从上到下取第 index 个节点（默认仅可点击）。

    用于商品列表：排除顶部搜索栏、底部导航后，区域内节点即商品卡片。
    """
    candidates: List[Tuple[int, Dict[str, str]]] = []
    for n in nodes:
        if only_clickable and n["clickable"] != "true":
            continue
        center = _bounds_to_center(n["bounds"])
        if not center:
            continue
        cx, cy = center
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            candidates.append((cy, n))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    idx = max(0, int(index) - 1)
    if idx >= len(candidates):
        idx = len(candidates) - 1
    return candidates[idx][1]


def find_by_text(nodes: List[Dict[str, str]], keyword: str, clickable: bool = True) -> Optional[Dict[str, str]]:
    """按文本/desc 模糊匹配节点（优先 clickable）。"""
    for node in nodes:
        haystack = f"{node['text']} {node['desc']}"
        if keyword in haystack:
            if clickable and node["clickable"] == "true":
                return node
            if not clickable:
                return node
    # 回退：任意匹配
    for node in nodes:
        haystack = f"{node['text']} {node['desc']}"
        if keyword in haystack:
            return node
    return None


def find_any_text(
    nodes: List[Dict[str, str]],
    keywords: List[str],
    clickable: bool = True,
) -> Optional[Dict[str, str]]:
    """按多个关键词依次匹配节点（任一命中即返回，如「加入购物车/加购/购物车」）。"""
    for kw in keywords:
        node = find_by_text(nodes, kw, clickable=clickable)
        if node:
            return node
    return None


def find_clickable_ancestor(nodes: List[Dict[str, str]], node: Dict[str, str]) -> Optional[Dict[str, str]]:
    """寻找包含目标 node 的最近可点击节点（列表项通常是一个大 clickable 卡片）。"""
    x, y = _bounds_to_center(node["bounds"]) or (0, 0)
    best: Optional[Dict[str, str]] = None
    best_area = float("inf")
    for n in nodes:
        if n["clickable"] != "true":
            continue
        m = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n["bounds"])
        if not m:
            continue
        x1, y1, x2, y2 = (int(g) for g in m.groups())
        if x1 <= x <= x2 and y1 <= y <= y2:
            area = (x2 - x1) * (y2 - y1)
            if area < best_area:
                best, best_area = n, area
    return best


def center_of(node: Dict[str, str]) -> Optional[Tuple[int, int]]:
    return _bounds_to_center(node["bounds"])