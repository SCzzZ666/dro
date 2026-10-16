"""导入所有工具使其完成注册。

用法：from .tools import get_tool, list_tools, execute_tool
"""
from .base import (
    AgentTool,
    REGISTRY,
    execute_tool,
    get_tool,
    list_tools,
    register_tool,
)
from . import open_app as _open_app  # noqa: F401
from . import search_product as _search_product  # noqa: F401
from . import click_product as _click_product  # noqa: F401
from . import extract_info as _extract_info  # noqa: F401
from . import go_back as _go_back  # noqa: F401
from . import screenshot as _screenshot  # noqa: F401
from . import close_popup as _close_popup  # noqa: F401
from . import gesture as _gesture  # noqa: F401
from . import clipboard as _clipboard  # noqa: F401
from . import checkout as _checkout  # noqa: F401
from . import image_search as _image_search  # noqa: F401

__all__ = [
    "AgentTool",
    "REGISTRY",
    "execute_tool",
    "get_tool",
    "list_tools",
    "register_tool",
]