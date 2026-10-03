"""Explicit gateway configuration; never load model/source credentials implicitly."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


def loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def checked_url(value: str) -> str:
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        raise ValueError("MCP API 地址无效。") from None
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username
            or url.password or url.query or url.fragment or url.path not in {"", "/"}
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError("MCP API 地址必须是无账号、路径、查询参数的 HTTP(S) 服务根地址。")
    if url.scheme == "http" and not loopback(url.hostname):
        raise ValueError("MCP 到非本机 Studio 的连接必须使用 HTTPS。")
    return value.rstrip("/")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, str(default)).lower()
    if value not in {"true", "false", "1", "0"}:
        raise ValueError(f"{name} 必须为 true/false。")
    return value in {"true", "1"}


@dataclass(frozen=True)
class MCPSettings:
    api_url: str = "http://127.0.0.1:8765"
    api_token: str = field(default="", repr=False)
    public_url: str | None = None
    read_only: bool = False
    timeout_seconds: float = 90
    http_token: str = field(default="", repr=False)
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "[::1]")
    allowed_origins: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "api_url", checked_url(self.api_url))
        object.__setattr__(self, "public_url", checked_url(self.public_url or self.api_url))
        if not 1 <= self.timeout_seconds <= 180:
            raise ValueError("MCP 请求超时必须为 1–180 秒。")
        if any(not token.isascii() or any(ord(c) < 32 for c in token)
               for token in (self.api_token, self.http_token)):
            raise ValueError("MCP 访问密钥必须为不含控制字符的 ASCII 字符。")
        if not self.allowed_hosts or any("*" in host or "/" in host for host in self.allowed_hosts):
            raise ValueError("MCP Host 白名单不能为空，也不能使用通配符或路径。")

    @classmethod
    def from_env(cls):
        return cls(
            api_url=os.environ.get("C2VIDEO_MCP_API_URL", "http://127.0.0.1:8765"),
            api_token=os.environ.get("C2VIDEO_MCP_API_TOKEN", ""),
            public_url=os.environ.get("C2VIDEO_MCP_PUBLIC_URL") or None,
            read_only=env_bool("C2VIDEO_MCP_READ_ONLY"),
            timeout_seconds=float(os.environ.get("C2VIDEO_MCP_TIMEOUT_SECONDS", "90")),
            http_token=os.environ.get("C2VIDEO_MCP_TOKEN", ""),
            allowed_hosts=tuple(v.strip() for v in os.environ.get(
                "C2VIDEO_MCP_ALLOWED_HOSTS", "127.0.0.1,localhost,[::1]",
            ).split(",") if v.strip()),
            allowed_origins=tuple(v.strip() for v in os.environ.get(
                "C2VIDEO_MCP_ALLOWED_ORIGINS", "",
            ).split(",") if v.strip()),
        )
