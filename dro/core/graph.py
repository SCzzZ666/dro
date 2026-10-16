"""v2 核心：LangGraph 状态图（意图 -> 规划 -> ReAct 工具循环 -> 结果生成）。

节点：
- intent_node   意图识别（含记忆注入、需求澄清）
- planner_node  生成工具调用计划
- agent_node    ReAct 循环：每轮执行计划中的一个工具，支持弹窗自愈/重试
- result_node   汇总工具结果，生成最终自然语言回复

checkpointer：默认 SqliteSaver（持久化、可跨进程恢复），失败降级 MemorySaver。

设计说明：节点采用同步实现，与同步 SqliteSaver 天然匹配；
手机操作工具自带线程池超时保护，LLM 调用走同步 httpx；
在 FastAPI 中通过线程池（asyncio.to_thread）调用，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import operator
import re
import threading
import time
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from .budget import extract_budget_max, format_budget
from .config import settings
from .llm import LLMClient, LLMError, get_llm, resolve_model
from .memory import MemoryManager
from .ranking import rank_products
from .metrics import get_logger, metrics
from .prompts import (
    INTENT_CLASSIFY_HUMAN,
    INTENT_CLASSIFY_SYSTEM,
    PLANNER_HUMAN,
    PLANNER_SYSTEM,
    RESULT_HUMAN,
    RESULT_SYSTEM,
    render,
)
from .schemas import VALID_INTENTS, TASK_TYPE_TO_INTENT
from ..tools import execute_tool, list_tools

logger = get_logger("dro.graph")


class GraphState(TypedDict, total=False):
    # 输入
    user_message: str
    task_type: str  # 前端中文标签：比价/找相似/最优方案
    image_path: Optional[str]
    budget_max: Optional[float]
    session_id: str
    model: str  # 前端选择的模型（空/auto 用默认；deepseek/qwen 映射具体名）

    # 过程中
    intent: str
    search_term: str
    reply: str  # 闲聊/澄清回复
    choices: Annotated[List[str], operator.add]
    awaiting_input: bool  # 需求含糊，等待用户澄清回答
    pending_goal: str  # 澄清前记住的原始目标（回答后据此续跑）
    plan: Annotated[List[Dict[str, Any]], operator.add]  # [{"name":..., "args":{...}}]
    step_index: int
    steps_done: int
    messages: Annotated[List[Dict[str, str]], operator.add]  # 对话记录
    collected: Annotated[List[Dict[str, Any]], operator.add]  # extract_info 采集结果
    tool_log: Annotated[List[Dict[str, Any]], operator.add]  # 执行轨迹

    # 输出
    products: List[Dict[str, Any]]
    recommendation: str
    final_reply: str
    step_count: int
    show_checkout_option: bool
    error: str
    status: str  # done / needs_input / running / failed


def _get_runtime_llm() -> Optional[LLMClient]:
    """返回 LLM 单例；无 API Key 时返回 None（调用方需兜底）。"""
    return get_llm()


def _heuristic_intent(user_message: str) -> str:
    """无 LLM 时的确定性意图判断（保底）。"""
    if any(k in user_message for k in ("比价", "对比", "谁便宜", "最低价", "差价", "哪个便宜")):
        return "price_compare"
    if any(k in user_message for k in ("同款", "相似", "类似", "一样的", "同类型")):
        return "find_similar"
    if any(k in user_message for k in ("推荐", "买个", "买一个", "想买", "帮我挑", "预算")):
        return "recommend"
    return "chat"


_BUDGET_REMOVE_RE = re.compile(
    r"(?:预算|价格|价位|不超过|最多|最高|低于|以内|以下|封顶|左右|大概|控制在)[^0-9]{0,6}?"
    r"(?:\d{1,6}(?:\.\d{1,2})?\s*(?:元|块|rmb|块钱)?"
    r"(?:\s*(?:~|-|—|～|到|至)\s*\d{1,6}(?:\.\d{1,2})?\s*(?:元|块|rmb|块钱)?)?)"
    r"(?:\s*以内|\s*以下)?",
    re.IGNORECASE,
)


def extract_budget(text: str) -> Optional[float]:
    """从用户输入提取价格上限（元）。

    委托 budget.extract_budget_max：支持 预算200 / 200以内 / 不超过300 /
    200-300 区间（取上界）等表述。单一实现避免两套正则漂移。
    """
    return extract_budget_max(text)


def clean_search_term(text: str) -> str:
    """去掉搜索词里的预算短语/口语句式，只保留商品核心词。

    如『帮我找一个杯子的同款』 -> 『杯子』；『帮我比价蓝牙耳机，预算200以内』 -> 『蓝牙耳机』。
    """
    cleaned = _BUDGET_REMOVE_RE.sub(" ", text or "")
    # 裸数字区间（无前缀词）：200-300元 / 100到200块 / 100~200
    cleaned = re.sub(
        r"\d{1,6}(?:\.\d{1,2})?\s*(?:元|块|rmb|块钱)?\s*(?:~|-|—|～|到|至)\s*"
        r"\d{1,6}(?:\.\d{1,2})?\s*(?:元|块|rmb|块钱)?",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    for token in (
        "帮我", "我想", "我要", "请帮我", "请", "麻烦",
        "推荐", "比价", "对比", "哪个便宜", "谁便宜", "最低价",
        "找一个", "找这个", "找那个", "找一款", "找",
        "买一个", "买个", "买一款", "买",
        "这个", "那个", "一个", "一款", "一只", "一台",
        "同款", "相似", "类似", "一样的", "同类型", "的同款",
        "预算", "左右", "大概",
    ):
        cleaned = cleaned.replace(token, " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" 　,，。、：:;；!！?？的")
    # 兜底：残余句式如『...的...』取主干（去所有格修饰）
    if "的" in cleaned:
        parts = [p for p in cleaned.split("的") if p.strip()]
        cleaned = parts[0] if parts else cleaned
    return cleaned


# ---------------------------------------------------------------------------
# 节点实现（同步）
# ---------------------------------------------------------------------------
def intent_node(state: GraphState) -> Dict[str, Any]:
    """意图识别：结合记忆上下文，把用户输入归类为 4 种意图之一。"""
    user_message = (state.get("user_message") or "").strip()
    task_type = state.get("task_type") or ""
    image_path = state.get("image_path")
    session_id = state.get("session_id", "default")

    budget_max = state.get("budget_max")
    if budget_max is None:
        budget_max = extract_budget(user_message)

    memory = MemoryManager(user_id=session_id)
    memory_ctx = memory.build_prompt(session_id)
    llm = _get_runtime_llm()

    base_updates: Dict[str, Any] = {
        "budget_max": budget_max,
        "task_type": task_type,
        "messages": [{"role": "user", "content": user_message}],
    }

    # 有图 -> 强制找相似
    if image_path:
        return {
            **base_updates,
            "intent": "find_similar",
            "search_term": clean_search_term(user_message) or "图片中的商品",
            "reply": "",
        }

    # 前端显式任务类型优先（避免 LLM 误判）
    explicit = TASK_TYPE_TO_INTENT.get(task_type)
    if explicit:
        return {
            **base_updates,
            "intent": explicit,
            "search_term": clean_search_term(user_message) or user_message,
            "reply": "",
        }

    # 无显式类型：走 LLM；LLM 不可用时用启发式
    if llm is None:
        return {
            **base_updates,
            "intent": _heuristic_intent(user_message),
            "reply": "",
            "search_term": clean_search_term(user_message) or user_message,
        }

    system = render(INTENT_CLASSIFY_SYSTEM)
    if memory_ctx:
        system += f"\n\n以下为用户记忆上下文（判断时可参考）：\n{memory_ctx}"
    recent = memory.get_history(session_id)[-6:]
    if recent:
        system += "\n\n最近对话（判断时可参考，可能包含上一轮澄清问答）：\n" + "\n".join(
            f"{m['role']}: {m['content']}" for m in recent
        )
    data: Dict[str, Any] = {}
    try:
        data = llm.chat_json(
            [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": render(INTENT_CLASSIFY_HUMAN, user_input=user_message, context=memory_ctx or "无"),
                },
            ],
            temperature=0.1,
            model=resolve_model(state.get("model") or ""),
        ) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"意图识别 LLM 失败，使用启发式: {e}")

    intent = data.get("intent") or _heuristic_intent(user_message)
    if intent not in VALID_INTENTS:
        intent = "chat"

    reply = data.get("clarify_question") or data.get("reply", "")
    need_clarify = bool(data.get("need_clarify")) or bool(data.get("choices"))

    updates: Dict[str, Any] = {
        **base_updates,
        "intent": intent,
        "reply": reply,
        "search_term": clean_search_term(data.get("search_term") or user_message) or user_message,
        "choices": data.get("choices") or [],
    }

    # 需求含糊（有澄清问题/选项）且不是闲聊：进入等待澄清状态，先不执行工具
    if need_clarify and intent != "chat":
        updates.update(
            {
                "awaiting_input": True,
                "pending_goal": user_message,
                "status": "needs_input",
            }
        )

    # 记忆写入：记录用户消息 + 提取偏好
    memory.add_user_message(session_id, user_message)
    memory.extract_and_store_preferences(user_message)

    return updates


def planner_node(state: GraphState) -> Dict[str, Any]:
    """根据意图生成工具调用计划。"""
    intent = state.get("intent") or "recommend"
    search_term = (state.get("search_term") or state.get("user_message") or "").strip() or "商品"
    budget_max = state.get("budget_max")
    budget_note = f"，价格不超过 ¥{budget_max:g}" if budget_max else ""
    llm = _get_runtime_llm()
    memory = MemoryManager(user_id=state.get("session_id", "default"))

    intent_desc = {
        "price_compare": f"跨平台比价：{search_term}{budget_note}，在京东和淘宝各找一个最低价",
        "find_similar": f"找同款：{search_term}{budget_note}",
        "recommend": f"最优推荐：{search_term}{budget_note}",
    }.get(intent, f"推荐：{search_term}{budget_note}")

    steps: List[Dict[str, Any]] = []
    if llm is not None:
        try:
            data = llm.chat_json(
                [
                    {"role": "system", "content": render(PLANNER_SYSTEM, max_steps=settings.max_steps)},
                    {
                        "role": "user",
                        "content": render(
                            PLANNER_HUMAN,
                            goal=intent_desc,
                            memory=memory.build_prompt(state.get("session_id", "default")),
                        ),
                    },
                ],
                model=resolve_model(state.get("model") or ""),
            ) or {}
            steps = data.get("steps") or []
        except Exception as e:  # noqa: BLE001
            logger.warning(f"规划 LLM 失败，使用兜底计划: {e}")

    # 兜底计划：保证有可执行的步骤
    if not steps:
        steps = _default_plan(intent, search_term)

    # 校验工具名（LLM 可能用 tool 字段而非 name）；finish 为伪步骤，允许进入计划
    avail = set(list_tools().keys())
    plan: List[Dict[str, Any]] = []
    for s in steps[:10]:
        name = s.get("name") or s.get("tool")
        if name in avail or name == "finish":
            plan.append({"name": name, "args": s.get("args") or s.get("arguments") or {}})

    plan = _inject_args(plan, search_term)
    return {"plan": plan, "step_index": 0}


def _inject_args(plan: List[Dict[str, Any]], search_term: str) -> List[Dict[str, Any]]:
    out = []
    for step in plan:
        args = dict(step.get("args") or {})
        if step["name"] == "search_product" and not args.get("keyword"):
            args["keyword"] = search_term
        out.append({"name": step["name"], "args": args})
    return out


def _default_plan(intent: str, search_term: str) -> List[Dict[str, Any]]:
    """确定性兜底计划（LLM 失败时使用）。"""
    if intent == "price_compare":
        return [
            {"name": "open_app", "args": {"app_name": "京东"}},
            {"name": "search_product", "args": {"keyword": search_term}},
            {"name": "extract_info", "args": {}},
            {"name": "go_back", "args": {}},
            {"name": "open_app", "args": {"app_name": "淘宝"}},
            {"name": "search_product", "args": {"keyword": search_term}},
            {"name": "extract_info", "args": {}},
        ]
    if intent == "find_similar":
        return [
            {"name": "open_app", "args": {"app_name": "淘宝"}},
            {"name": "search_product", "args": {"keyword": search_term}},
            {"name": "extract_info", "args": {}},
        ]
    # recommend / 其它
    return [
        {"name": "open_app", "args": {"app_name": "京东"}},
        {"name": "search_product", "args": {"keyword": search_term}},
        {"name": "extract_info", "args": {}},
        {"name": "open_app", "args": {"app_name": "淘宝"}},
        {"name": "search_product", "args": {"keyword": search_term}},
        {"name": "extract_info", "args": {}},
    ]


def _finish_by_rules(state: GraphState, collected: List[Dict[str, Any]]) -> bool:
    """确定性 finish 判定（规则优先，不调 LLM）。

    - 有预算上限且已采到预算内商品 -> finish
    - 比价任务：两个平台都采到 -> finish（单平台采再多也不提前结束）
    - 其它意图：已采到 >= 3 个商品 -> finish
    """
    intent = state.get("intent") or ""
    budget_max = state.get("budget_max")
    items = [p for p in collected if (p.get("title") or p.get("name"))]
    if not items:
        return False

    def _price(p) -> Optional[float]:
        v = p.get("price")
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    if budget_max is not None:
        if any((pr is not None and 0 < pr <= budget_max) for pr in (_price(p) for p in items)):
            return True

    if intent == "price_compare":
        platforms = {p.get("platform") or p.get("shop_name") or "" for p in items}
        return len({pf for pf in platforms if pf}) >= 2

    return len(items) >= 3


def _decide_finish(
    state: GraphState,
    observation: str,
    step_collected: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """ReAct 提前结束判定：确定性规则优先，规则不命中且步数足够才降级 LLM。"""
    collected = list(state.get("collected") or [])
    if step_collected:
        collected = collected + list(step_collected)

    # 1) 规则优先：命中即结束，零 LLM 开销
    if _finish_by_rules(state, collected):
        return True

    # 2) 步级节流：前几步信息通常不足，不调 LLM
    if (state.get("steps_done") or 0) < 3:
        return False

    llm = _get_runtime_llm()
    if llm is None:
        return False
    plan = list(state.get("plan") or [])
    idx = state.get("step_index") or 0
    remaining = plan[idx + 1 :] if idx + 1 <= len(plan) else []
    remaining_text = (
        "\n".join(f"- {s.get('name')}({s.get('args', {})})" for s in remaining)
        if remaining
        else "（计划已无剩余步骤）"
    )
    collected_text = (
        "\n".join(
            f"- {p.get('title') or p.get('name')} ¥{p.get('price')} {p.get('platform') or ''}"
            for p in collected[-8:]
        )
        if collected
        else "（尚未采集到商品）"
    )
    decision_prompt = (
        "你是手机自动化执行 Agent。判断是否已经收集到足够信息完成用户任务。\n"
        "只输出 JSON：{\"tool\": \"finish\"}（信息足够，可结束）或 {\"tool\": \"continue\"}（还需执行计划）。\n"
        "规则：有预算上限时，只要已找到至少一个预算内的商品，即可 finish；"
        "比价任务两个平台都采集到价格后即可 finish，无需翻页全部商品。\n"
        "当前计划剩余步骤：\n{plan}\n"
        "已收集信息：\n{collected}\n"
        "最近操作反馈：\n{observation}"
    )
    try:
        data = llm.chat_json(
            [
                {"role": "system", "content": "你是谨慎的任务执行决策器，只输出合法 JSON。"},
                {
                    "role": "user",
                    "content": render(
                        decision_prompt,
                        plan=remaining_text,
                        collected=collected_text,
                        observation=observation,
                    ),
                },
            ],
            temperature=0.1,
            model=resolve_model(state.get("model") or ""),
            timeout=20.0,
        ) or {}
        return str(data.get("tool") or "").lower() == "finish"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"ReAct finish 决策失败（继续执行计划）: {e}")
        return False


def agent_node(state: GraphState) -> Dict[str, Any]:
    """ReAct 工具循环：每轮执行计划中的一个工具，带弹窗自愈与重试。

    - 支持 finish 伪步骤（planner 明示结束）与 LLM 提前 finish 决策。
    - 注意：tool_log/collected 使用 operator.add reducer，因此这里**只返回本次新增**的元素，
      否则会把累积列表再次拼接导致指数级重复。
    """
    plan = list(state.get("plan") or [])
    idx = state.get("step_index") or 0
    done = state.get("steps_done") or 0

    # 达到最大步数 -> 结束
    if done >= settings.max_steps or idx >= len(plan):
        return {"status": "done"}

    step = plan[idx]
    name = step["name"]
    args = step.get("args") or {}

    # finish 伪步骤：不执行工具，直接结束，进入结果节点
    if name == "finish":
        return {
            "step_index": idx + 1,
            "tool_log": [{"name": "finish", "args": {}, "ok": True, "message": "计划完成，进入结果生成"}],
            "status": "done",
        }

    result = execute_tool(name, settings.tool_timeout, **args)

    new_entries = [
        {
            "name": name,
            "args": args,
            "ok": result.ok,
            "message": result.message,
            "data": result.data,
        }
    ]

    # 弹窗自愈：extract_info 检测到 popup / 任何工具失败且消息含弹窗关键词
    if not result.ok:
        msg = result.message or ""
        data = result.data or {}
        is_popup = bool(data.get("popup")) or any(kw in msg for kw in ("弹窗", "popup", "关闭"))
        if is_popup:
            popup_result = execute_tool("close_popup", settings.tool_timeout)
            new_entries.append(
                {
                    "name": "close_popup",
                    "args": {},
                    "ok": popup_result.ok,
                    "message": popup_result.message,
                    "data": popup_result.data,
                }
            )
            # 自愈后重试当前步骤一次
            if done < settings.max_steps - 1:
                retry = execute_tool(name, settings.tool_timeout, **args)
                new_entries.append(
                    {
                        "name": name,
                        "args": args,
                        "ok": retry.ok,
                        "message": retry.message,
                        "data": retry.data,
                    }
                )
                result = retry

    # 采集数据：extract_info 返回 {products: [...]}（新工具 data.screen.products 兼容）
    new_collected = []
    if result.ok:
        data = result.data
        screen_products = None
        if data.get("products"):
            screen_products = data["products"]
        elif data.get("screen", {}).get("products"):
            screen_products = data["screen"]["products"]
        if screen_products:
            for p in screen_products:
                new_collected.append(dict(p))

    # ReAct 提前结束判定：本步有产出且计划还有剩余步骤时，让 LLM 判断是否可 finish
    early_done = False
    if result.ok and idx + 1 < len(plan):
        obs = result.message or ""
        if new_collected:
            detail = "\n".join(
                f"- {p.get('title') or p.get('name')} ¥{p.get('price')} {p.get('platform') or ''}"
                for p in new_collected[:6]
            )
            obs = f"{obs}\n本步采集：\n{detail}"
        early_done = _decide_finish(state, obs, new_collected)
        if early_done:
            new_entries.append(
                {"name": "finish", "args": {}, "ok": True, "message": "ReAct 判定信息足够，提前结束"}
            )

    return {
        "step_index": idx + 1,
        "steps_done": done + 1,
        "collected": new_collected,
        "tool_log": new_entries,
        "status": "done" if early_done else "running",
    }


def annotate_budget(
    products: List[Dict[str, Any]],
    budget_max: Optional[float],
    drop_over: bool = False,
) -> List[Dict[str, Any]]:
    """把预算约束**标注**到商品上，而不是把预算当作写死的过滤开关。

    - 超预算商品：默认保留，reason 标注『超出预算 ¥X』；drop_over=True 时才剔除
    - 价格异常（≤0/缺失）：保留并标注『价格未知/异常，请人工核验』
    - 预算内最低价：标注 is_winner=True（推荐标记，最终由结果/LLM 裁决）
    """
    if budget_max is None:
        return products
    kept: List[Dict[str, Any]] = []
    for p in products:
        item = dict(p)
        item.setdefault("is_winner", False)
        item.setdefault("reason", "")
        price = item.get("price")
        try:
            price_f = float(price) if price not in (None, "") else None
        except (TypeError, ValueError):
            price_f = None
        if price_f is not None and price_f > 0:
            if price_f > budget_max:
                base = (item["reason"] or "").strip()
                item["reason"] = (f"{base} " if base else "") + f"超出预算 ¥{budget_max:g}（实价 ¥{price_f:g}）"
                if drop_over:
                    continue
            else:
                base = (item["reason"] or "").strip()
                item["reason"] = (f"{base} " if base else "") + f"符合预算 ¥{budget_max:g}"
        elif price_f is not None and price_f <= 0:
            base = (item["reason"] or "").strip()
            item["reason"] = (f"{base} " if base else "") + "价格显示异常（≤0），请人工核验"
        kept.append(item)

    valid = [
        p
        for p in kept
        if isinstance(p.get("price"), (int, float)) and p["price"] > 0 and p["price"] <= budget_max
    ]
    if valid:
        best = min(valid, key=lambda p: float(p["price"]))
        best["is_winner"] = True
    return kept


def result_node(state: GraphState) -> Dict[str, Any]:
    """结束节点：整理采集结果，生成最终自然语言回复（无 LLM 时兜底拼接）。"""
    # 澄清等待态：直接给出反问/选项，不执行工具也不调结果 LLM
    if state.get("awaiting_input"):
        question = state.get("reply") or "请补充一下您的需求，我才能继续帮您。"
        memory_early = MemoryManager(user_id=state.get("session_id", "default"))
        memory_early.add_assistant_message(state.get("session_id", "default"), question)
        return {
            "products": [],
            "recommendation": question,
            "final_reply": question,
            "show_checkout_option": False,
            # choices 已由 intent_node 通过 reducer 写入 state，这里不再重复返回
            "choices": [],
            "step_count": state.get("steps_done") or 0,
            "task_type": state.get("task_type", ""),
            "intent": state.get("intent", ""),
            "status": "needs_input",
        }

    collected = list(state.get("collected") or [])
    tool_log = list(state.get("tool_log") or [])
    session_id = state.get("session_id", "default")
    budget_max = state.get("budget_max")
    memory = MemoryManager(user_id=session_id)

    # 已采集商品：去重并归一化
    products: List[Dict[str, Any]] = []
    seen = set()
    for p in collected:
        title = (p.get("title") or p.get("name") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        price = p.get("price")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        products.append(
            {
                "name": title,
                "price": price,
                "platform": p.get("platform") or p.get("shop_name") or "",
                "sales": p.get("sales", ""),
                "rating": p.get("rating", ""),
                "shop_name": p.get("shop_name") or p.get("shop", ""),
                "link": p.get("link", ""),
                "is_winner": False,
                "reason": p.get("reason", ""),
            }
        )

    # 预算作为需求约束：标注到每个商品上（不做死数据剔除，交给执行/结果动态运用）
    products = annotate_budget(products, budget_max)
    # 确定性评分排序（价格占比最高）：先算分，再交 LLM 生成解释文字
    products = rank_products(products)

    has_llm = False
    recommendation = ""
    show_checkout = bool(products)
    try:
        llm = _get_runtime_llm()
        if llm is not None:
            budget_hint = f"，预算上限 ¥{budget_max:g}" if budget_max else ""
            data = llm.chat_json(
                [
                    {"role": "system", "content": render(RESULT_SYSTEM)},
                    {
                        "role": "user",
                        "content": render(
                            RESULT_HUMAN,
                            goal=f"{state.get('user_message', '')}{budget_hint}",
                            intent=state.get("intent", ""),
                            collected=(
                                "无"
                                if not products
                                else "\n".join(
                                    f"- {p['name']} ¥{p['price']} {p['platform']}"
                                    f"{('：' + p['reason']) if p.get('reason') else ''}"
                                    for p in products
                                )
                            ),
                        ),
                    },
                ],
                model=resolve_model(state.get("model") or ""),
                timeout=30.0,
            )
            if isinstance(data, dict) and data.get("recommendation"):
                has_llm = True
                recommendation = data["recommendation"]
                if data.get("products"):
                    normalized = []
                    for p in data["products"]:
                        if isinstance(p, dict):
                            pd = dict(p)
                            pd["name"] = pd.get("name") or pd.get("title", "")
                            normalized.append(pd)
                    products = normalized
                show_checkout = bool(products)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"结果生成 LLM 失败，使用兜底: {e}")

    # 对 LLM 输出再标注一次约束（保留数据，仅补充标注）
    products = annotate_budget(products, budget_max)

    if not has_llm:
        # 兜底推荐：只把预算内商品写进推荐正文，超预算/异常价的保留在列表里但明确说明
        def _price_ok(p) -> bool:
            try:
                price = float(p.get("price"))
                return price > 0 and (budget_max is None or price <= budget_max)
            except (TypeError, ValueError):
                return True

        in_budget = [p for p in products if _price_ok(p)]
        over_budget = [p for p in products if not _price_ok(p)]
        if products:
            if in_budget:
                lines = ["已为你找到以下符合预算的商品："]
                for i, p in enumerate(in_budget, 1):
                    lines.append(f"{i}. {p['name']} - ¥{p.get('price', '?')} ({p.get('platform', '')})")
            else:
                lines = ["已为你找到以下商品，但暂未确认价格是否符合预算："]
                for i, p in enumerate(products, 1):
                    lines.append(f"{i}. {p['name']} - ¥{p.get('price', '?')} ({p.get('platform', '')})")
            if over_budget:
                lines.append(f"另有 {len(over_budget)} 件超出预算或价格待确认（已在列表标注）。")
            recommendation = "\n".join(lines)
        else:
            recommendation = "任务执行完成，但没有提取到有效的商品数据。请确认设备连接和 App 状态。"

    reply = state.get("reply") or ""
    if reply:
        recommendation = reply if not products else f"{reply}\n\n{recommendation}"

    memory.add_assistant_message(session_id, recommendation)

    return {
        "products": products,
        "recommendation": recommendation,
        "final_reply": recommendation,
        "show_checkout_option": show_checkout,
        "choices": list(state.get("choices") or []),
        "step_count": state.get("steps_done") or len(state.get("tool_log") or []),
        "task_type": state.get("task_type", ""),
        "intent": state.get("intent", ""),
        "status": "done",
    }


# ---------------------------------------------------------------------------
# 边路由
# ---------------------------------------------------------------------------
def route_after_intent(state: GraphState) -> str:
    intent = state.get("intent") or "chat"
    if state.get("awaiting_input") or state.get("status") == "needs_input":
        return "result"  # 澄清/闲聊：先回复用户，不执行工具
    if intent == "chat":
        return "result"  # 闲聊/澄清直达结果，不执行工具
    return "planner"


def route_after_agent(state: GraphState) -> str:
    plan = list(state.get("plan") or [])
    idx = state.get("step_index") or 0
    done = state.get("steps_done") or 0
    if state.get("status") == "done" or idx >= len(plan) or done >= settings.max_steps:
        return "result"
    return "agent"


# ---------------------------------------------------------------------------
# 图构建
# ---------------------------------------------------------------------------
def build_graph(checkpointer=None):
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(GraphState)

    graph.add_node("intent_node", intent_node)
    graph.add_node("planner_node", planner_node)
    graph.add_node("agent_node", agent_node)
    graph.add_node("result_node", result_node)

    graph.add_edge(START, "intent_node")
    graph.add_conditional_edges(
        "intent_node",
        route_after_intent,
        {"result": "result_node", "planner": "planner_node"},
    )
    graph.add_edge("planner_node", "agent_node")
    graph.add_conditional_edges(
        "agent_node",
        route_after_agent,
        {"agent": "agent_node", "result": "result_node"},
    )
    graph.add_edge("result_node", END)

    if checkpointer is None:
        checkpointer = _default_checkpointer()
    return graph.compile(checkpointer=checkpointer)


_CHECKPOINTER_CACHE = None
_CHECKPOINTER_CONN = None


def _default_checkpointer():
    """默认 SqliteSaver（持久化 + 可跨进程恢复），失败降级 MemorySaver。"""
    global _CHECKPOINTER_CACHE, _CHECKPOINTER_CONN
    if _CHECKPOINTER_CACHE is not None:
        return _CHECKPOINTER_CACHE
    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        _CHECKPOINTER_CONN = sqlite3.connect(str(settings.checkpoint_path), check_same_thread=False)
        _CHECKPOINTER_CACHE = SqliteSaver(_CHECKPOINTER_CONN)
        return _CHECKPOINTER_CACHE
    except Exception as e:  # noqa: BLE001
        logger.warning(f"SqliteSaver 不可用，降级 MemorySaver: {e}")
        from langgraph.checkpoint.memory import MemorySaver

        _CHECKPOINTER_CACHE = MemorySaver()
        return _CHECKPOINTER_CACHE


def run_task(
    user_message: str,
    task_type: str = "",
    image_path: Optional[str] = None,
    session_id: str = "default",
    thread_id: Optional[str] = None,
    graph=None,
    model: str = "",
) -> Dict[str, Any]:
    """同步入口（CLI/单测/线程池）返回最终 state。

    checkpointer 为同步 SqliteSaver；langgraph 的 invoke 会自行处理 async 节点。
    """
    g = graph or build_graph()
    thread = thread_id or f"{session_id}_{int(time.time() * 1000)}"
    initial: Dict[str, Any] = {
        "user_message": user_message,
        "task_type": task_type,
        "image_path": image_path,
        "session_id": session_id,
        "model": model,
    }
    return g.invoke(initial, config={"configurable": {"thread_id": thread}})


async def a_run_task(
    user_message: str,
    task_type: str = "",
    image_path: Optional[str] = None,
    session_id: str = "default",
    thread_id: Optional[str] = None,
    graph=None,
    model: str = "",
):
    """异步入口：在后台线程执行同步 invoke，避免阻塞事件循环。"""
    thread = thread_id or f"{session_id}_{int(time.time() * 1000)}"
    out = await asyncio.to_thread(
        run_task,
        user_message,
        task_type,
        image_path,
        session_id,
        thread,
        graph,
        model,
    )
    return out, thread


def resume_task(thread_id: str, graph=None):
    """同步恢复任务：同一 thread_id 从 checkpoint 继续执行。"""
    g = graph or build_graph()
    return g.invoke(None, {"configurable": {"thread_id": thread_id}})


async def a_resume_task(thread_id: str, graph=None):
    """异步恢复任务：在后台线程执行，避免阻塞事件循环。"""
    return await asyncio.to_thread(resume_task, thread_id, graph)


# 设备互斥锁：设备是单例资源，同一时间只允许一个任务驱动，避免多用户「抢设备」乱序点击。
# 串行化后，checkpoint 的 SQLite 写入也天然不再并发竞争。
_DEVICE_LOCK = threading.Lock()


class TaskRunner:
    """图任务执行器：异步方法供 FastAPI 使用，同步方法供 CLI/测试使用。

    checkpointer 为同步 SqliteSaver（跨线程安全，langgraph 内部带锁），
    异步入口通过 asyncio.to_thread 转入工作线程执行，不阻塞事件循环。
    """

    def __init__(self, graph=None):
        self._graph = graph or build_graph()

    def new_thread_id(self) -> str:
        return f"task_{int(time.time() * 1000)}"

    # ------------------------------------------------------------------
    # 异步（FastAPI 事件循环内使用）
    # ------------------------------------------------------------------
    async def ainvoke(
        self,
        user_input: str,
        task_type: str = "",
        image_path: Optional[str] = None,
        thread_id: Optional[str] = None,
        session_id: Optional[str] = None,
        model: str = "",
    ) -> Dict[str, Any]:
        """异步执行一次完整任务，返回最终 state。"""
        thread = thread_id or self.new_thread_id()
        out = await asyncio.to_thread(
            self.invoke, user_input, task_type, image_path, thread, session_id, model
        )
        return out

    async def aresume(self, thread_id: str) -> Dict[str, Any]:
        """异步恢复任务（FastAPI）。"""
        return await asyncio.to_thread(self.resume, thread_id)

    async def aget_state(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """异步读取 checkpoint 状态（FastAPI）。"""
        return await asyncio.to_thread(self.get_state, thread_id)

    # ------------------------------------------------------------------
    # 同步（CLI/测试/工作线程）
    # ------------------------------------------------------------------
    def invoke(
        self,
        user_input: str,
        task_type: str = "",
        image_path: Optional[str] = None,
        thread_id: Optional[str] = None,
        session_id: Optional[str] = None,
        model: str = "",
    ) -> Dict[str, Any]:
        """同步执行一次完整任务，返回最终 state。

        thread_id：任务/checkpoint 维度；session_id：对话记忆维度（缺省同 thread_id）。
        """
        thread = thread_id or self.new_thread_id()
        with _DEVICE_LOCK:
            result = run_task(
                user_message=user_input,
                task_type=task_type,
                image_path=image_path,
                session_id=session_id or thread,
                thread_id=thread,
                graph=self._graph,
                model=model,
            )
        steps = result.get("step_count") or len(result.get("tool_log") or [])
        metrics.record_task(result.get("status", "unknown"), steps)
        return result

    def get_state(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """同步读取 checkpoint 状态。"""
        try:
            snapshot = self._graph.get_state({"configurable": {"thread_id": thread_id}})
            if snapshot is None or not getattr(snapshot, "values", None):
                return None
            return {"values": dict(snapshot.values), "next": list(snapshot.next)}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"get_state 失败: {e}")
            return None

    def resume(self, thread_id: str) -> Dict[str, Any]:
        """同步恢复任务。"""
        with _DEVICE_LOCK:
            return resume_task(thread_id, self._graph)