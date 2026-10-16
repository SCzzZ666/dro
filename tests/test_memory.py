"""记忆系统单元测试：SQLite 短期/摘要/长期/画像四层。

需要临时数据库，不污染真实数据。
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dro.core.memory import MemoryManager, SqliteStore


class MemoryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp(prefix="dro_mem_test_")
        self.store = SqliteStore(Path(tmp) / "memory.db")
        self.mem = MemoryManager(user_id="u1", store=self.store)

    def tearDown(self):
        try:
            self.store.conn.close()
        except Exception:
            pass

    def test_short_term_roundtrip(self):
        self.mem.add_user_message("s1", "你好")
        self.mem.add_assistant_message("s1", "有什么可以帮你")
        history = self.mem.get_history("s1")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")

    def test_short_term_window(self):
        for i in range(30):
            self.mem.add_user_message("s2", f"msg-{i}")
        window = self.mem.short_term.window
        history = self.mem.short_term.get("s2", limit=window)
        self.assertLessEqual(len(history), window)
        self.assertEqual(history[-1]["content"], "msg-29")

    def test_long_term_preferences(self):
        self.mem.long_term.add("budget", "200")
        self.mem.long_term.add("color", "红色")
        self.mem.long_term.add("budget", "200")  # 重复应被忽略
        rows = self.store.query(
            "SELECT pref_type, value FROM preferences WHERE user_id=?", ("u1",)
        )
        self.assertIn(("budget", "200"), rows)
        self.assertIn(("color", "红色"), rows)

    def test_profile_roundtrip(self):
        self.mem.profile.save({"budget": "200", "color": "红色"})
        loaded = self.mem.profile.load()
        self.assertEqual(loaded["budget"], "200")

    def test_build_prompt_includes_preferences(self):
        self.mem.long_term.add("budget", "200")
        ctx = self.mem.build_prompt("s3")
        self.assertIn("200", ctx)

    def test_extract_preferences_regex_fallback(self):
        from dro.core.memory import _regex_preferences

        out = _regex_preferences("预算200以内，红色")
        self.assertEqual(out["budget"], "预算200元以内")
        self.assertEqual(out["color"], "红色")

    def test_long_term_search_sqlite_fallback(self):
        """未配置 Qdrant / embedding 时，search 回退 SQLite 全量偏好。"""
        self.mem.long_term.add("budget", "200")
        self.mem.long_term.add("color", "红色")
        result = self.mem.long_term.search("我想买个耳机", top_k=5)
        self.assertTrue(any("budget" in r for r in result))
        self.assertTrue(any("color" in r for r in result))

    @unittest.skipUnless(
        importlib.util.find_spec("qdrant_client") is not None,
        "qdrant-client 未安装，跳过向量检索测试",
    )
    def test_long_term_vector_search(self):
        """配置 Qdrant + embedding 时走向量检索（mock 掉客户端与 embedding）。"""
        from unittest.mock import MagicMock

        fake_embedder = MagicMock()
        fake_embedder.embed_texts.return_value = [[0.1, 0.2, 0.3]]
        self.mem.long_term._qdrant = MagicMock()
        self.mem.long_term._embedder = fake_embedder

        point = MagicMock()
        point.payload = {"pref_type": "budget", "value": "200"}
        self.mem.long_term._qdrant.query_points.return_value.points = [point]

        hits = self.mem.long_term._search_vector("预算", top_k=3)
        self.assertEqual(hits, ["budget: 200"])
        self.mem.long_term._qdrant.query_points.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)