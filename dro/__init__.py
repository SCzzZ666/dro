"""dro — LangGraph 电商 AI 助手（v2）。

分层结构：
  core/    — 编排图、LLM 客户端、记忆、配置、提示词、数据模型、预算解析
  device/  — ADB 封装、UI XML 解析、视觉分析
  web/     — FastAPI 应用与内嵌管理页面
  tools/   — 原子手机操作工具（12 个）

设计原则：
1. 每个手机操作是独立 Tool，统一注册、超时、错误返回。
2. LangGraph 状态图编排：意图识别 -> 规划 -> ReAct 工具循环 -> 结果生成。
3. 记忆系统（短期 / 摘要 / 长期 / 画像）在每个节点执行前注入上下文。
4. 默认存储 SQLite（checkpoint + 记忆），Qdrant / PostgreSQL 可选适配。
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