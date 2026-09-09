"""预算解析：从用户输入中提取"价格上限"。

设计目标：让"预算 200 以内 / 200 元以下 / 不超过 300"这类表述稳定解析成
一个数字上限 budget_max，供 planner / result 做约束过滤。
"""

from __future__ import annotations

import re
from typing import Optional

# 区间：100-200 / 100~200 / 100到200 / 100至200（取上界）
_RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:元|块)?\s*(?:~|-|—|～|到|至)\s*(\d+(?:\.\d+)?)\s*(?:元|块)?"
)

# 按优先级排列的单值预算表述
_BUDGET_PATTERNS = [
    # 预算 200 / 预算200以内 / 预算在200左右
    re.compile(r"预算\s*(?:在|是)?\s*(\d+(?:\.\d+)?)\s*(?:元|块钱|元以内|以内|内|以下|左右)?"),
    # 不超过/不高于/低于/少于/最多 200
    re.compile(r"(?:不超过|不高于|低于|少于|最多|最多出|小于|控制在)\s*(\d+(?:\.\d+)?)\s*(?:元|块钱)?"),
    # 200 以内 / 200元以下 / 200之内
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块钱)?\s*(?:以内|以内吧|以下|之内|内)"),
    # 200-300 元这类区间（兜底）
    re.compile(r"(?:¥|￥)\s*(\d+(?:\.\d+)?)"),
]


def extract_budget_max(text: Optional[str]) -> Optional[float]:
    """从用户输入提取价格上限；没有明确预算返回 None。

    同一句里出现多个候选时取最大值（上界最保守，避免把超预算商品推给用户）。
    """
    if not text:
        return None
    # 1. 显式区间：取上界
    m = _RANGE_RE.search(text)
    if m:
        return max(float(m.group(1)), float(m.group(2)))
    # 2. 单值候选：全部命中后取最大值
    candidates: list[float] = []
    for pattern in _BUDGET_PATTERNS:
        for m in pattern.finditer(text):
            candidates.append(float(m.group(1)))
    return max(candidates) if candidates else None


def format_budget(budget_max: Optional[float]) -> str:
    """把预算上限格式化为提示词可读文本。"""
    if budget_max is None:
        return ""
    if float(budget_max).is_integer():
        return f"预算上限 ¥{int(budget_max)}"
    return f"预算上限 ¥{budget_max:g}"
