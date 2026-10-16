"""dro 回归测试（离线，不依赖设备/真实 LLM）。

运行：
    python -m unittest discover -s tests -v
"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

_FIXTURES = Path(__file__).parent / "fixtures" / "eval_samples.json"


def _load_samples():
    return json.loads(_FIXTURES.read_text(encoding="utf-8"))


class BudgetParsingTests(unittest.TestCase):
    def test_budget_extraction(self):
        from dro.core.graph import extract_budget

        cases = [
            ("帮我比价蓝牙耳机，预算200以内", 200.0),
            ("预算 200 元以内的耳机", 200.0),
            ("不超过300的降噪耳机", 300.0),
            ("低于150块买杯子", 150.0),
            ("最多 1000 元预算", 1000.0),
            ("200-300元的耳机", 300.0),   # 区间取上界
            ("100到200块买个耳机", 200.0),
            ("随便推荐一个好用的", None),
            ("", None),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(extract_budget(text), expected)

    def test_search_term_cleaning(self):
        from dro.core.graph import clean_search_term

        cases = [
            ("帮我比价蓝牙耳机，预算200以内", "蓝牙耳机"),
            ("不超过300推荐降噪耳机", "降噪耳机"),
            ("帮我找一个杯子的同款", "杯子"),
            ("200-300元的耳机", "耳机"),
            ("100到200块", ""),
            ("蓝牙耳机", "蓝牙耳机"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(clean_search_term(text), expected)

    def test_heuristic_intent(self):
        from dro.core.graph import _heuristic_intent

        cases = [
            ("京东淘宝哪个便宜", "price_compare"),
            ("帮我找同款", "find_similar"),
            ("推荐一个降噪耳机", "recommend"),
            ("今天天气怎么样", "chat"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(_heuristic_intent(text), expected)


class BudgetFilterTests(unittest.TestCase):
    def test_annotate_budget_keeps_data(self):
        """预算约束是标注而非死数据：超预算商品保留但注明，默认不剔除。"""
        from dro.core.graph import annotate_budget

        products = [
            {"name": "WASSUP KO联名蓝牙耳机", "price": 109.0, "platform": "天猫"},
            {"name": "同款达期蓝牙耳机", "price": 264.0, "platform": "京东"},
            {"name": "海真旗舰店商品", "price": 0.0, "platform": "天猫"},
        ]
        out = annotate_budget(products, 200.0)
        names = {p["name"] for p in out}
        # 数据全部保留（不做死剔除）
        self.assertEqual(names, {"WASSUP KO联名蓝牙耳机", "同款达期蓝牙耳机", "海真旗舰店商品"})
        # 但约束被标注出来
        over = next(p for p in out if p["name"] == "同款达期蓝牙耳机")
        bad_price = next(p for p in out if p["name"] == "海真旗舰店商品")
        self.assertIn("超出预算", over["reason"])
        self.assertIn("价格显示异常", bad_price["reason"])
        # 预算内最低价标记为 winner
        winner = [p["name"] for p in out if p["is_winner"]]
        self.assertEqual(winner, ["WASSUP KO联名蓝牙耳机"])

    def test_annotate_budget_drop_over(self):
        """仅当显式要求 drop_over=True 时才剔除超预算项。"""
        from dro.core.graph import annotate_budget

        products = [{"name": "A", "price": 99.0}, {"name": "B", "price": 300.0}]
        out = annotate_budget(products, 200.0, drop_over=True)
        self.assertEqual([p["name"] for p in out], ["A"])

    def test_no_budget_no_filter(self):
        from dro.core.graph import annotate_budget

        products = [{"name": "A", "price": 999.0}]
        out = annotate_budget(products, None)
        self.assertEqual(len(out), 1)

    def test_eval_samples_budget(self):
        for sample in _load_samples():
            if sample["id"] not in {"eval_001", "eval_002", "eval_004"}:
                continue
            with self.subTest(sample=sample["id"]):
                from dro.core.graph import extract_budget

                got = extract_budget(sample["user_input"])
                self.assertEqual(got, sample["expect"]["budget_max"])


class UiParsingTests(unittest.TestCase):
    SAMPLE_XML = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<hierarchy rotation="0">'
        '<node index="0" text="" resource-id="com.taobao.taobao:id/searchEditText" '
        'class="android.widget.EditText" hint="搜索商品" clickable="false" '
        'bounds="[90,130][990,240]"/>'
        '<node index="1" text="爆款蓝牙耳机" resource-id="" '
        'class="android.widget.FrameLayout" hint="" clickable="true" '
        'bounds="[30,600][520,1200]"/>'
        '<node index="2" text="另一款耳机" resource-id="" '
        'class="android.widget.FrameLayout" hint="" clickable="true" '
        'bounds="[560,600][1050,1200]"/>'
        '<node index="3" text="首页" resource-id="" '
        'class="android.widget.TextView" hint="" clickable="true" '
        'bounds="[30,2300][240,2400]"/>'
        "</hierarchy>"
    )

    def test_parse_nodes_attrs(self):
        from dro.device.ui import parse_nodes

        nodes = parse_nodes(self.SAMPLE_XML)
        self.assertEqual(len(nodes), 4)
        edit = nodes[0]
        self.assertEqual(edit["class"], "android.widget.EditText")
        self.assertEqual(edit["hint"], "搜索商品")
        self.assertEqual(edit["resource_id"], "com.taobao.taobao:id/searchEditText")

    def test_find_edit_box(self):
        from dro.device.ui import find_edit_box, parse_nodes

        box = find_edit_box(parse_nodes(self.SAMPLE_XML))
        self.assertIsNotNone(box)
        self.assertIn("search", box["resource_id"])

    def test_find_by_bounds_region_clickable(self):
        from dro.device.ui import center_of, find_by_bounds_region, parse_nodes

        nodes = parse_nodes(self.SAMPLE_XML)
        # 商品列表区域：y 500-2200（不含底部导航）
        item = find_by_bounds_region(nodes, 0, 500, 1080, 2200, index=2)
        self.assertIsNotNone(item)
        self.assertEqual(item["text"], "另一款耳机")
        self.assertEqual(center_of(item), (805, 900))


class ToolRegistryTests(unittest.TestCase):
    def test_new_tools_registered(self):
        from dro.tools import list_tools

        names = set(list_tools().keys())
        for tool in ("swipe_up", "swipe_down", "swipe_left", "swipe_right", "paste_text"):
            self.assertIn(tool, names)


class ReActFinishTests(unittest.TestCase):
    def test_finish_step_short_circuits(self):
        """plan 中显式 finish 步骤：不执行任何工具，直接 done。"""
        import dro.core.graph as graph

        state = {
            "plan": [{"name": "finish", "args": {}}],
            "step_index": 0,
            "steps_done": 0,
        }
        with patch.object(graph, "execute_tool", side_effect=AssertionError("finish 不应执行工具")) as m:
            out = graph.agent_node(state)
        m.assert_not_called()
        self.assertEqual(out["status"], "done")
        names = [e["name"] for e in out["tool_log"]]
        self.assertIn("finish", names)

    def test_route_after_agent_done(self):
        import dro.core.graph as graph

        self.assertEqual(graph.route_after_agent({"status": "done"}), "result")
        self.assertEqual(
            graph.route_after_agent({"status": "running", "plan": [{"name": "x"}], "step_index": 0, "steps_done": 0}),
            "agent",
        )


class ApiContractTests(unittest.TestCase):
    def test_status_response_fields(self):
        """前端渲染依赖 task_type / updated_at / user_input 字段。"""
        from dro.web.api import TaskStatusResponse

        resp = TaskStatusResponse(
            thread_id="t1",
            status="done",
            task_type="比价",
            user_input="预算200以内",
            final_reply="ok",
            products=[],
            step_count=3,
            updated_at=123.0,
        )
        d = resp.model_dump()
        self.assertEqual(d["task_type"], "比价")
        self.assertEqual(d["user_input"], "预算200以内")
        self.assertGreater(d["updated_at"], 0)


class ClarifyFlowTests(unittest.TestCase):
    """澄清状态机：需求含糊先反问、不执行工具；回答后能续跑。"""

    def test_route_after_intent_awaiting(self):
        import dro.core.graph as graph

        self.assertEqual(graph.route_after_intent({"awaiting_input": True, "intent": "recommend"}), "result")
        self.assertEqual(graph.route_after_intent({"status": "needs_input", "intent": "recommend"}), "result")

    def test_result_node_awaiting_short_circuit(self):
        """澄清等待态：不执行工具、不调 LLM，直接 needs_input + 问题。"""
        import dro.core.graph as graph
        from unittest.mock import patch

        state = {
            "session_id": "sess_test",
            "awaiting_input": True,
            "reply": "儿童入门款还是成人款？",
            "choices": ["儿童入门款", "成人款"],
            "task_type": "",
            "intent": "recommend",
            "steps_done": 0,
        }
        with patch.object(graph, "_get_runtime_llm", side_effect=AssertionError("澄清态不应调 LLM")):
            out = graph.result_node(state)
        self.assertEqual(out["status"], "needs_input")
        self.assertEqual(out["final_reply"], "儿童入门款还是成人款？")
        self.assertEqual(out["products"], [])
        # choices 由 intent_node 经 reducer 写入 state，早退不再重复返回
        self.assertEqual(out["choices"], [])

    def test_task_runner_session_memory_separation(self):
        """TaskRunner：thread（checkpoint）与 session（记忆）解耦，invoke 可传 session_id。"""
        from dro.core.graph import TaskRunner

        runner = TaskRunner()
        # 不实际跑图，仅验证签名与默认回退逻辑
        import inspect

        params = inspect.signature(runner.invoke).parameters
        self.assertIn("session_id", params)

    def test_agent_node_finish_entry(self):
        """plan 中显式 finish：不执行工具直接结束（澄清/计划收尾路径保障）。"""
        import dro.core.graph as graph

        state = {"plan": [{"name": "finish", "args": {}}], "step_index": 0, "steps_done": 0}
        out = graph.agent_node(state)
        self.assertEqual(out["status"], "done")
        self.assertEqual([e["name"] for e in out["tool_log"]], ["finish"])


class RankingTests(unittest.TestCase):
    def test_rank_products_cheapest_wins(self):
        from dro.core.ranking import rank_products

        ranked = rank_products([
            {"name": "贵", "price": 300.0, "sales": "1000", "rating": "4.9"},
            {"name": "便宜", "price": 99.0, "sales": "500", "rating": "4.7"},
        ])
        self.assertEqual(ranked[0]["name"], "便宜")
        self.assertTrue(ranked[0]["is_winner"])
        self.assertEqual(ranked[0]["rank"], 1)
        self.assertIn("score", ranked[0])

    def test_rank_products_handles_missing_fields(self):
        from dro.core.ranking import rank_products

        ranked = rank_products([
            {"name": "A", "price": 0.0, "sales": "", "rating": ""},
            {"name": "B", "price": 100.0},
        ])
        # 价格异常无价格分，B 有价格分 -> B 胜
        self.assertEqual(ranked[0]["name"], "B")


class FinishRuleTests(unittest.TestCase):
    def test_finish_by_rules_budget(self):
        from dro.core.graph import _finish_by_rules

        state = {"intent": "price_compare", "budget_max": 200.0}
        collected = [{"title": "耳机", "price": 150.0, "platform": "京东"}]
        self.assertTrue(_finish_by_rules(state, collected))

    def test_finish_by_rules_price_compare_needs_two_platforms(self):
        from dro.core.graph import _finish_by_rules

        state = {"intent": "price_compare", "budget_max": None}
        collected = [
            {"title": "a", "price": 100, "platform": "京东"},
            {"title": "b", "price": 90, "platform": "京东"},
            {"title": "c", "price": 80, "platform": "京东"},
        ]
        # 单平台采 3 个也不提前结束（比价需两平台）
        self.assertFalse(_finish_by_rules(state, collected))
        collected.append({"title": "d", "price": 110, "platform": "淘宝"})
        self.assertTrue(_finish_by_rules(state, collected))

    def test_finish_by_rules_recommend_threshold(self):
        from dro.core.graph import _finish_by_rules

        state = {"intent": "recommend", "budget_max": None}
        collected = [
            {"title": "a", "price": 100, "platform": "京东"},
            {"title": "b", "price": 90, "platform": "京东"},
            {"title": "c", "price": 80, "platform": "京东"},
        ]
        self.assertTrue(_finish_by_rules(state, collected))


class ApiUtilsTests(unittest.TestCase):
    def test_is_valid_image_magic(self):
        from dro.web.api import _is_valid_image

        self.assertTrue(_is_valid_image(b"\x89PNG\r\n\x1a\n", ".png"))
        self.assertTrue(_is_valid_image(b"\xff\xd8\xff", ".jpg"))
        self.assertFalse(_is_valid_image(b"not-an-image", ".png"))

    def test_state_persistence_roundtrip(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        import dro.web.api as api

        tmp = Path(tempfile.mkdtemp()) / "state.db"
        with patch.object(api, "_STATE_DB", tmp):
            api._persist_state("tasks", "t1", {"status": "done", "final_reply": "ok"})
            loaded = api._load_state("tasks")
        self.assertEqual(loaded["t1"]["status"], "done")
        self.assertEqual(loaded["t1"]["final_reply"], "ok")


class MetricsTests(unittest.TestCase):
    def test_metrics_record_and_render(self):
        from dro.core.metrics import Metrics

        m = Metrics()
        m.record_llm(True, 100.0, tokens=300)
        m.record_tool("search_product", True, 500.0)
        m.record_task("done", steps=5)
        out = m.render()
        self.assertIn("dro_llm_requests_total 1.0", out)
        self.assertIn("dro_llm_tokens_total 300.0", out)
        self.assertIn("dro_tool_requests_total 1.0", out)
        self.assertIn("dro_task_done 1.0", out)
        self.assertIn("dro_llm_latency_ms_sum 100.0", out)
        self.assertIn("dro_task_steps_count 1", out)


if __name__ == "__main__":
    unittest.main()
