"""Dro v2 — LangGraph 版本电商 AI 助手。

旧版 (app.py / agent_service.py) 保留为 deprecated，本目录为独立重构。

设计目标：
1. 告别“LLM 生成 Python 代码本地执行”的不可控模式。
2. 每个手机操作（open_app / search_product / click_product / extract_info ...）是
   一个独立 Tool，统一注册、统一超时、统一错误返回。
3. 用 LangGraph 状态图编排：意图识别 -> 规划 -> ReAct 工具循环 -> 结果生成。
4. 记忆系统（短期 / 摘要 / 长期 / 画像）在每个节点执行前注入上下文。
5. 默认存储使用 SQLite（checkpoint + 记忆），Qdrant / PostgreSQL 作为可选适配器。
"""
import sys


def _reconfigure_std():
    """Windows GBK 控制台打印 emoji/中文容易崩，重配为 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 —— 重配失败不阻塞启动
            pass


_reconfigure_std()
del _reconfigure_std

__version__ = "2.0.0"