"""v2 统一入口。

启动：python -m v2            （默认 http://localhost:8001）
可选：python -m v2 --port 9000
"""
import argparse
import uvicorn

from .config import settings
from .api import create_app


def main():
    parser = argparse.ArgumentParser(description="Dro v2 电商 AI 助手")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    args = parser.parse_args()

    print("🍪 Dro v2 启动中...")
    print(f"📡 API: http://{args.host if args.host != '0.0.0.0' else 'localhost'}:{args.port}")
    print(f"📱 设备: {settings.device_serial}")
    print("🔧 可用工具:", " / ".join(_tool_names()))
    uvicorn.run(create_app(), host=args.host, port=args.port)


def _tool_names() -> list:
    from .tools import list_tools
    return sorted(list_tools().keys())


if __name__ == "__main__":
    main()