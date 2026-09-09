"""工具注册与调度单元测试（不依赖真手机）。

用临时的 AdbClient 桩验证：注册、参数过滤、超时保护、异常兜底、失败不抛异常。
"""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from v2.schemas import ToolResult
from v2.tools.base import REGISTRY, register_tool


class FakeAdb:
    """模拟在线设备：所有命令快速失败/成功，可配置。"""

    def __init__(self, online=True, fail_with=None):
        self.online = online
        self.fail_with = fail_with
        self.calls = []

    def ensure_online(self, auto_reconnect=True):
        return self.online

    def run(self, args, timeout=None):
        self.calls.append(("run", args))
        if self.fail_with:
            raise self.fail_with
        return _FakeResult(0, "ok", "")

    def tap(self, x, y):
        return _FakeResult(0, "ok", "")

    def type_text(self, text):
        return _FakeResult(0, "ok", "")

    def keyevent(self, code):
        return _FakeResult(0, "ok", "")

    def go_back(self):
        return _FakeResult(0, "ok", "")

    def screencap(self, path):
        Path(path).write_bytes(b"fake-png")
        return _FakeResult(0, "ok", "")

    def _screen_size(self):
        return (1080, 2400)


class _FakeResult:
    def __init__(self, code, stdout, stderr):
        self.returncode = code
        self.stdout = stdout
        self.stderr = stderr

    def ok(self):
        return self.returncode == 0


class ToolRegistryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from v2.tools import (  # noqa: F401 —— 触发注册
            open_app, search_product, click_product, extract_info, go_back, screenshot, close_popup
        )

    def test_all_tools_registered(self):
        for name in ("open_app", "search_product", "click_product", "extract_info", "go_back", "screenshot", "close_popup"):
            self.assertIn(name, REGISTRY, f"工具 {name} 缺失")

    def test_tool_names_are_lowercase_snake(self):
        for name in REGISTRY:
            self.assertEqual(name, name.lower())
            self.assertNotIn(" ", name)

    def test_execute_unknown_tool_returns_failure(self):
        from v2.tools.base import execute_tool

        result = execute_tool("no_such_tool", 5)
        self.assertFalse(result.ok)

    def test_timeout_detection(self):
        @register_tool("_test_timeout", "测试超时")
        def slow():
            time.sleep(2)
            return ToolResult(ok=True)

        from v2.tools.base import execute_tool

        result = execute_tool("_test_timeout", timeout=0.2)
        self.assertFalse(result.ok)
        self.assertIn("超时", result.message)

    def test_exception_never_escapes(self):
        @register_tool("_test_crash", "测试异常")
        def crash():
            raise RuntimeError("boom")

        from v2.tools.base import execute_tool

        result = execute_tool("_test_crash", 5)
        self.assertFalse(result.ok)
        self.assertIn("boom", result.message)

    def test_go_back_offline_returns_failure(self):
        from v2.tools.base import execute_tool

        with patch("v2.tools.go_back.adb", FakeAdb(online=False)):
            result = execute_tool("go_back", 5)
        self.assertFalse(result.ok)
        self.assertIn("未在线", result.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)