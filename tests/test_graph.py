"""LangGraph 状态图单元测试：意图路由、计划执行、chat 短路、checkpoint 恢复。

通过 mock LLM 与无设备降级路径验证图编排逻辑，不需要真手机/真 API Key。
"""
import sys
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from v2.graph import build_graph


class FakeLLM:
    """同步 LLM 桩：chat_json 返回预先配置的结果。"""

    def __init__(self, plan=None, intent=None):
        self._intent = intent
        self._plan = plan

    def chat_json(self, messages, **kwargs):
        content = messages[-1]["content"]
        # 意图识别阶段
        if "用户输入" in content or "用户输入：" in content or "INTENT" in content.upper() or "意图" in str(messages[0]):
            if self._intent:
                return {"intent": self._intent, "search_term": "蓝牙耳机", "reply": "", "choices": []}
        # 规划阶段
        return {"steps": self._plan or []}


def _run(graph, message, task_type="", thread=None):
    # 每个用例使用唯一 thread_id，避免 checkpoint 复用导致从断点续跑
    return graph.invoke(
        {
            "user_message": message,
            "task_type": task_type,
            "image_path": None,
            "session_id": "test-user",
        },
        config={"configurable": {"thread_id": thread or f"t_{uuid.uuid4().hex[:12]}"}},
    )


def _patch_fast_adb(online=False):
    """把各工具模块的 adb 换成快速失败的桩（避免真实 ADB 重连超时拖慢测试）。"""
    try:
        from tests.test_tools import FakeAdb
    except ImportError:  # unittest discover 下 tests 可能是顶层模块
        from test_tools import FakeAdb

    from unittest.mock import patch

    fake = FakeAdb(online=online)
    patchers = [
        patch("v2.tools.open_app.adb", fake),
        patch("v2.tools.search_product.adb", fake),
        patch("v2.tools.click_product.adb", fake),
        patch("v2.tools.extract_info.adb", fake),
        patch("v2.tools.go_back.adb", fake),
        patch("v2.tools.screenshot.adb", fake),
        patch("v2.tools.close_popup.adb", fake),
    ]
    for p in patchers:
        p.start()
    return patchers


def _stop_patches(patchers):
    for p in patchers:
        p.stop()


class GraphFlowTest(unittest.TestCase):
    def setUp(self):
        self._patchers = _patch_fast_adb(online=False)

    def tearDown(self):
        _stop_patches(self._patchers)

    def test_chat_intent_skips_tools(self):
        """chat 意图应直达 result_node，不执行任何工具。"""
        graph = build_graph(checkpointer=None)
        with patch("v2.graph._get_runtime_llm", return_value=FakeLLM(intent="chat")):
            out = _run(graph, "你好呀")
        self.assertEqual(out.get("intent"), "chat")
        self.assertEqual(out.get("status"), "done")
        self.assertEqual(len(out.get("tool_log") or []), 0)
        # chat 时应该有 reply（即使 LLM 未给澄清也走兜底）
        self.assertTrue(out.get("final_reply"))

    def test_price_compare_runs_plan(self):
        """价格对比：应生成计划并执行工具（无设备时失败但不崩溃）。"""
        graph = build_graph(checkpointer=None)
        plan = [
            {"name": "open_app", "args": {"app_name": "京东"}},
            {"name": "search_product", "args": {"keyword": "蓝牙耳机"}},
        ]
        with patch("v2.graph._get_runtime_llm", return_value=FakeLLM(intent="price_compare", plan=plan)):
            out = _run(graph, "京东淘宝哪个便宜")
        self.assertEqual(out.get("intent"), "price_compare")
        self.assertEqual(out.get("status"), "done")
        logs = out.get("tool_log") or []
        self.assertTrue(logs, "应执行至少一个工具")
        # 工具失败因无设备，但状态应为 done 且不崩溃

    def test_heuristic_fallback_without_llm(self):
        """无 API Key 时：启发式意图 + 默认计划仍能走通。"""
        graph = build_graph(checkpointer=None)
        with patch("v2.graph._get_runtime_llm", return_value=None):
            out = _run(graph, "帮我比价耳机")
        self.assertEqual(out.get("intent"), "price_compare")
        self.assertEqual(out.get("status"), "done")
        self.assertGreaterEqual(len(out.get("tool_log") or []), 1)

    def test_max_steps_limit(self):
        """超过最大步数强制结束（构造超长计划）。"""
        from v2.config import settings

        graph = build_graph(checkpointer=None)
        plan = [{"name": "go_back", "args": {}}] * (settings.max_steps + 10)
        with patch("v2.graph._get_runtime_llm", return_value=FakeLLM(intent="recommend", plan=plan)):
            out = _run(graph, "推荐一个耳机")
        self.assertLessEqual(len(out.get("tool_log") or []), settings.max_steps)
        self.assertEqual(out.get("status"), "done")


class CheckpointTest(unittest.TestCase):
    def test_checkpoint_and_state_query(self):
        """SqliteSaver checkpoint：执行后能通过 get_state 读到状态。"""
        patchers = _patch_fast_adb(online=False)
        try:
            from v2.graph import TaskRunner

            runner = TaskRunner()
            thread = f"ckpt_test_{uuid.uuid4().hex[:8]}"
            with patch("v2.graph._get_runtime_llm", return_value=None):
                runner.invoke("比价耳机", task_type="比价", thread_id=thread)
            state = runner.get_state(thread)
            self.assertIsNotNone(state)
            values = state["values"]
            self.assertEqual(values.get("intent"), "price_compare")
        finally:
            _stop_patches(patchers)


if __name__ == "__main__":
    unittest.main(verbosity=2)