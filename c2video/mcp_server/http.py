"""Private shared-key HTTP transport for trusted local/small-team clients.

This is not an OAuth authorization server or per-user identity system. Each
gateway has one configured Studio identity; HTTP callers never supply that key.
"""

from __future__ import annotations

import asyncio
import secrets

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from c2video.mcp_server.config import MCPSettings
from c2video.mcp_server.server import build_server


class GatewayBoundary:
    def __init__(self, app, settings: MCPSettings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        expected = f"Bearer {self.settings.http_token}".encode()
        supplied = headers.get("authorization", "").encode()

        async def reject(status, detail):
            await JSONResponse(
                {"detail": detail}, status_code=status,
                headers={"Cache-Control": "no-store",
                         **({"WWW-Authenticate": "Bearer"} if status == 401 else {})},
            )(scope, receive, send)

        if not secrets.compare_digest(expected, supplied):
            return await reject(401, "MCP 访问密钥缺失或不正确。")
        if scope["method"] == "POST":
            chunks, length = [], 0
            try:
                async with asyncio.timeout(10):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        length += len(message.get("body", b""))
                        if length > 1_100_000:
                            return await reject(413, "MCP 请求过大。")
                        chunks.append(message)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                return await reject(408, "读取 MCP 请求超时。")
            original_receive = receive

            async def replay_receive():
                return chunks.pop(0) if chunks else await original_receive()

            receive = replay_receive
        await self.app(scope, receive, send)


def create_http_app(settings: MCPSettings, *, server=None):
    if len(settings.http_token) < 32:
        raise ValueError("HTTP MCP 必须配置至少 32 字符的 C2VIDEO_MCP_TOKEN。")
    if settings.api_token and settings.http_token == settings.api_token:
        raise ValueError("MCP 入口密钥必须与 Studio 访问密钥不同。")
    server = server or build_server(settings)
    return GatewayBoundary(server.streamable_http_app(), settings)
