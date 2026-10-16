"""dro Web 页面渲染：HTML 已抽离到 templates/，CSS/JS 抽离到 static/。"""
from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parent / "templates"


def _read(name: str) -> str:
    return (_TEMPLATES / name).read_text(encoding="utf-8")


def render_chat_page() -> str:
    return _read("chat.html")


def render_home_page() -> str:
    return _read("home.html")
