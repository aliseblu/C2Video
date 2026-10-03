"""Run with python -m c2video.mcp_server; stdout belongs to MCP in stdio mode."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path

from c2video.mcp_server.client import GatewayError, StudioClient
from c2video.mcp_server.config import MCPSettings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="C2Video MCP gateway for AgentChat")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--api-url", help="Studio root URL, e.g. http://127.0.0.1:8765")
    parser.add_argument("--env-file", help="Explicit private MCP env file; project .env is NOT loaded")
    parser.add_argument("--read-only", action="store_true", help="Expose read-only tools only")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address")
    parser.add_argument("--port", type=int, default=8766, help="HTTP port (default 8766)")
    parser.add_argument("--check", action="store_true", help="Check Studio without starting an MCP server")
    args = parser.parse_args(argv)
    try:
        if args.env_file:
            from dotenv import load_dotenv

            path = Path(args.env_file).expanduser()
            if not path.is_file():
                raise ValueError("指定的 MCP 环境文件不存在。")
            load_dotenv(path, override=False)
        settings = MCPSettings.from_env()
        if args.api_url:
            settings = replace(settings, api_url=args.api_url,
                               public_url=os.environ.get("C2VIDEO_MCP_PUBLIC_URL") or args.api_url)
        if args.read_only:
            settings = replace(settings, read_only=True)
        if args.check:
            print(json.dumps(asyncio.run(StudioClient(settings).health()), ensure_ascii=False, indent=2))
            return
        try:
            from c2video.mcp_server.server import build_server
        except ModuleNotFoundError:
            raise ValueError("请先安装 MCP 可选依赖：pip install -e '.[mcp]'。") from None
        server = build_server(settings)
        if args.transport == "stdio":
            server.run(transport="stdio")
        else:
            import uvicorn

            from c2video.mcp_server.http import create_http_app

            if not 1 <= args.port <= 65535:
                raise ValueError("MCP 端口必须为 1–65535。")
            app = create_http_app(settings, server=server)
            uvicorn.run(app, host=args.host, port=args.port, log_level="warning",
                        access_log=False, proxy_headers=False)
    except (ValueError, GatewayError) as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
