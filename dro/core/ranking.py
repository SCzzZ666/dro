"""商品最优方案确定性评分（rank_products）。

把「最便宜 / 最优」从 LLM 文本判断改为可复现、可解释、可测试的确定性打分：

    score = w_price * N(1/price) + w_sales * N(sales) + w_rating * N(rating)
          - w_shipping * 是否有运费 + w_insurance * 是否有运费险

先算分、再交给 LLM 生成解释文字（见 result_node）。权重默认「价格占比最高」，
与需求文档一致，可通过 weights 覆盖。
"""
import re
from typing import Any, Dict, List, Optional

# 权重（需求文档：价格 0.5 / 销量 0.2 / 好评 0.15 / 运费 0.1 / 运费险 0.05）
DEFAULT_WEIGHTS = {
    "price": 0.5,
    "sales": 0.2,
    "rating": 0.15,
    "shipping": 0.1,
    "insurance": 0.05,
}


def _to_float(value) -> Optional[float]:
    """尽力把字段解析为 float（容忍「¥199.0」「4.8 分」等）。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"(\d+(?:\.\d+)?)", str(value))
    return float(m.group(1)) if m else None


def _parse_sales(value) -> Optional[float]:
    """销量：支持「已售 1.2 万+」「3000 人付款」等。"""
    if value is None:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(万)?", str(value))
    if not m:
        return None
    n = float(m.group(1))
    if m.group(2) == "万":
        n *= 10000
    return n


def _parse_rating(value) -> Optional[float]:
    """评分：归一化到 0-5 分（好评率 98% -> 4.9）。"""
    n = _to_float(value)
    if n is None:
        return None
    if n > 10:  # 百分比好评率 -> 0-5 分
        n = n / 20.0
    return min(n, 5.0)


def _normalize(values: List[Optional[float]]) -> List[Optional[float]]:
    """min-max 归一化到 [0,1]；None 保持 None；全 None 返回全 None。"""
    vals = [v for v in values if v is not None]
    if not vals:
        return [None] * len(values)
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return [1.0 if v is not None else None for v in values]
    return [(v - lo) / (hi - lo) if v is not None else None for v in values]


def _has_insurance(item: Dict[str, Any]) -> bool:
    """是否含运费险（字段缺失/不可解析视为 False，即无加分）。"""
    for k in ("has_return_insurance", "insurance", "运费险"):
        v = item.get(k)
        if v is None:
            continue
        if any(w in str(v) for w in ("是", "有", "true", "1", "含", "运费险")):
            return True
    return False


def _has_shipping_fee(item: Dict[str, Any]) -> bool:
    """是否收运费（「包邮/免运费」视为免运费；缺失/不可解析视为免运费）。"""
    for k in ("has_shipping_fee", "shipping_fee", "shipping", "运费", "包邮"):
        v = item.get(k)
        if v is None:
            continue
        s = str(v)
        if any(w in s for w in ("包邮", "免运费", "免邮", "0", "false", "无")):
            return False
        if _to_float(v) is not None:
            return True  # 有具体运费金额
    return False


def rank_products(
    products: List[Dict[str, Any]],
    weights: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """对商品列表做确定性打分排序，返回带 score/rank/is_winner 的新列表。"""
    if not products:
        return []
    w = {**DEFAULT_WEIGHTS, **(weights or {})}

    items = [dict(p) for p in products]

    prices: List[Optional[float]] = []
    for p in items:
        pr = _to_float(p.get("price"))
        prices.append(1.0 / pr if pr is not None and pr > 0 else None)
    sales = [_parse_sales(p.get("sales")) for p in items]
    ratings = [_parse_rating(p.get("rating")) for p in items]

    p_n = _normalize(prices)
    s_n = _normalize(sales)
    r_n = _normalize(ratings)

    for i, p in enumerate(items):
        score = 0.0
        if p_n[i] is not None:
            score += w["price"] * p_n[i]
        if s_n[i] is not None:
            score += w["sales"] * s_n[i]
        if r_n[i] is not None:
            score += w["rating"] * r_n[i]
        score -= w["shipping"] * (1.0 if _has_shipping_fee(p) else 0.0)
        score += w["insurance"] * (1.0 if _has_insurance(p) else 0.0)
        p["score"] = round(score, 4)

    items.sort(key=lambda x: x["score"], reverse=True)
    for i, p in enumerate(items):
        p["rank"] = i + 1
        p["is_winner"] = i == 0
    return items
