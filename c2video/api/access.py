"""Single-workspace access control. Shared operator/viewer keys, revocable sessions."""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from urllib.parse import urlsplit
from uuid import uuid4

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from c2video.capacity import minimum_free_bytes, queue_limit

logger = logging.getLogger(__name__)
COOKIE = "c2video_session"
AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_idempotency (
    request_key TEXT PRIMARY KEY,
    body_hash TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS platform_sessions (
    session_hash TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    expires_at REAL NOT NULL,
    credential_tag TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON platform_sessions(expires_at);
CREATE TABLE IF NOT EXISTS auth_attempts (
    client_hash TEXT PRIMARY KEY,
    window_start REAL NOT NULL,
    attempts INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class PlatformSettings:
    environment: str = "development"
    operator_token: str = field(default="", repr=False)
    viewer_token: str = field(default="", repr=False)
    worker_mode: str = "embedded"
    concurrency: int = 1
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "::1", "testserver")
    allowed_origins: tuple[str, ...] = ()
    cookie_secure: bool = False
    max_body_bytes: int = 1_100_000
    session_seconds: int = 8 * 3600

    def __post_init__(self):
        queue_limit()
        minimum_free_bytes(production=self.environment == "production")
        if self.environment not in {"development", "production"}:
            raise ValueError("C2VIDEO_ENV must be development or production")
        if self.worker_mode not in {"embedded", "external", "off"} or not 1 <= self.concurrency <= 4:
            raise ValueError("Invalid worker mode or concurrency (1–4)")
        if self.viewer_token and (not self.operator_token or self.viewer_token == self.operator_token):
            raise ValueError("Viewer token requires a different operator token")
        if any(token and len(token) < 32 for token in (self.operator_token, self.viewer_token)):
            raise ValueError("Studio access keys must contain at least 32 characters")
        if self.environment == "production" and not self.operator_token:
            raise ValueError("Production requires C2VIDEO_STUDIO_TOKEN; no unauthenticated fallback")
        if not self.allowed_hosts or "*" in self.allowed_hosts:
            raise ValueError("Explicit allowed hosts are required")
        if any(not token.isascii() or not token.isprintable() or len(token) > 512
               for token in (self.operator_token, self.viewer_token) if token):
            raise ValueError("Studio access keys must be printable ASCII and at most 512 characters")
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
                    or (self.environment == "production" and parsed.scheme != "https")):
                raise ValueError("Allowed origins must be exact origins; production requires HTTPS")
        if not 1024 <= self.max_body_bytes <= 2_000_000:
            raise ValueError("Invalid request body limit")
        if self.environment == "production" and not self.cookie_secure:
            raise ValueError("Production requires secure cookies and HTTPS")

    @classmethod
    def from_env(cls):
        production = os.getenv("C2VIDEO_ENV", "development") == "production"
        return cls(
            environment=os.getenv("C2VIDEO_ENV", "development"),
            operator_token=os.getenv("C2VIDEO_STUDIO_TOKEN", ""),
            viewer_token=os.getenv("C2VIDEO_VIEWER_TOKEN", ""),
            worker_mode=os.getenv("C2VIDEO_WORKER_MODE", "external" if production else "embedded"),
            concurrency=int(os.getenv("C2VIDEO_WORKER_CONCURRENCY", "1")),
            allowed_hosts=tuple(s.strip() for s in os.getenv(
                "C2VIDEO_ALLOWED_HOSTS", "127.0.0.1,localhost,::1" if production else "127.0.0.1,localhost,::1,testserver").split(",") if s.strip()),
            allowed_origins=tuple(s.strip().rstrip("/") for s in os.getenv(
                "C2VIDEO_ALLOWED_ORIGINS", "").split(",") if s.strip()),
            cookie_secure=production or os.getenv("C2VIDEO_COOKIE_SECURE", "false").lower() == "true",
        )


class AccessControl:
    def __init__(self, store, settings: PlatformSettings):
        self.store = store
        self.settings = settings

    def key_role(self, key: str) -> str | None:
        if not key:
            return None
        if self.settings.operator_token and secrets.compare_digest(key.encode(), self.settings.operator_token.encode()):
            return "operator"
        if self.settings.viewer_token and secrets.compare_digest(key.encode(), self.settings.viewer_token.encode()):
            return "viewer"
        return None

    def _tag(self, role):
        key = self.settings.operator_token if role == "operator" else self.settings.viewer_token
        return hashlib.sha256(key.encode()).hexdigest()

    def role(self, headers: Headers) -> str | None:
        authorization = headers.get("authorization", "")
        if authorization.startswith("Bearer "):
            return self.key_role(authorization[7:])
        raw = self.cookie(headers)
        if not raw:
            return None
        with self.store.transaction() as db:
            session = db.execute("SELECT * FROM platform_sessions WHERE session_hash=?",
                                 (hashlib.sha256(raw.encode()).hexdigest(),)).fetchone()
        if (session and session["expires_at"] > time.time()
                and secrets.compare_digest(session["credential_tag"], self._tag(session["role"]))):
            return session["role"]
        return None

    @staticmethod
    def cookie(headers: Headers) -> str:
        jar = SimpleCookie()
        try:
            jar.load(headers.get("cookie", ""))
            return jar[COOKIE].value if COOKIE in jar else ""
        except Exception:
            return ""

    def login(self, key: str, client: str) -> tuple[str, str]:
        digest = hashlib.sha256(client.encode()).hexdigest()
        now = time.time()
        with self.store.atomic() as db:
            db.execute("DELETE FROM platform_sessions WHERE expires_at<?", (now,))
            db.execute("DELETE FROM auth_attempts WHERE window_start<?", (now - 300,))
            attempt = db.execute("SELECT * FROM auth_attempts WHERE client_hash=?", (digest,)).fetchone()
            if attempt and attempt["attempts"] >= 10:
                raise PermissionError("登录尝试过多，请 5 分钟后再试。")
            role = self.key_role(key)
            if not role:
                db.execute("""INSERT INTO auth_attempts VALUES (?,?,1)
                    ON CONFLICT(client_hash) DO UPDATE SET attempts=attempts+1""", (digest, now))
            else:
                token = secrets.token_urlsafe(32)
                db.execute("INSERT INTO platform_sessions VALUES (?,?,?,?)",
                           (hashlib.sha256(token.encode()).hexdigest(), role,
                            now + self.settings.session_seconds, self._tag(role)))
                db.execute("DELETE FROM auth_attempts WHERE client_hash=?", (digest,))
        if not role:
            raise ValueError("访问密钥不正确。")
        return token, role

    def logout(self, headers: Headers) -> None:
        token = self.cookie(headers)
        with self.store.transaction() as db:
            db.execute("DELETE FROM platform_sessions WHERE session_hash=?",
                       (hashlib.sha256(token.encode()).hexdigest(),))


class PlatformMiddleware:
    def __init__(self, app, *, access: AccessControl):
        self.app = app
        self.access = access

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        request_id = uuid4().hex
        started = time.monotonic()
        path = scope["path"]
        settings = self.access.settings
        sent = False

        async def wrapped_send(message):
            nonlocal sent
            if message["type"] == "http.response.start":
                sent = True
                extra = [(b"x-request-id", request_id.encode()),
                         (b"x-content-type-options", b"nosniff"),
                         (b"referrer-policy", b"no-referrer"),
                         (b"x-frame-options", b"DENY")]
                if settings.environment == "production":
                    extra.append((b"strict-transport-security", b"max-age=31536000"))
                if not path.startswith(("/docs", "/redoc")):
                    extra.append((b"content-security-policy",
                                  b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                  b"img-src 'self' data:; media-src 'self' blob:; font-src 'self'; "
                                  b"connect-src 'self'; object-src 'none'; base-uri 'none'; "
                                  b"frame-ancestors 'none'; form-action 'self'"))
                if path.startswith("/api"):
                    extra.append((b"cache-control", b"no-store"))
                message = {**message, "headers": list(message.get("headers", [])) + extra}
            await send(message)

        async def reject(status, detail):
            await JSONResponse({"detail": detail, "request_id": request_id},
                               status_code=status)(scope, receive, wrapped_send)

        try:
            host = urlsplit("//" + headers.get("host", "")).hostname
        except ValueError:
            return await reject(400, "请求主机无效。")
        if host not in settings.allowed_hosts:
            return await reject(400, "请求主机不在允许列表中。")
        if (scope["method"] not in {"GET", "HEAD", "OPTIONS"}
                and headers.get("sec-fetch-site") == "cross-site"):
            return await reject(403, "不允许跨站写入请求。")
        origin = headers.get("origin")
        if origin:
            same_origin = f"{scope.get('scheme', 'http')}://{headers.get('host', '')}"
            if origin not in (*settings.allowed_origins, same_origin):
                return await reject(403, "不允许跨站请求。")
        scope.setdefault("state", {})["request_id"] = request_id
        role = self.access.role(headers) if settings.operator_token else "operator"
        scope["state"]["role"] = role
        public = path in {"/api/auth/status", "/api/auth/login", "/healthz", "/readyz"}
        protected = path.startswith("/api") or path.startswith("/docs") or path in {"/openapi.json", "/redoc"}
        if protected and not public:
            if not settings.operator_token:
                client = (scope.get("client") or ("", 0))[0]
                try:
                    local = ipaddress.ip_address(client).is_loopback
                except ValueError:
                    local = client == "testclient" and "testserver" in settings.allowed_hosts
                if not local:
                    return await reject(403, "远程访问必须先配置 Studio 访问密钥。")
            if not role:
                return await reject(401, "请先登录 Studio。")
            if scope["method"] not in {"GET", "HEAD", "OPTIONS"} and role != "operator":
                if path != "/api/auth/logout":
                    return await reject(403, "只读账号不能执行此操作。")
        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"} and path.startswith("/api"):
            if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                return await reject(415, "写入请求必须使用 application/json。")
            chunks = []
            length = 0
            import asyncio
            try:
                async with asyncio.timeout(10):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        length += len(message.get("body", b""))
                        if length > settings.max_body_bytes:
                            return await reject(413, "请求内容过大。")
                        chunks.append(message)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                return await reject(408, "读取请求超时。")
            original_receive = receive

            async def replay_receive():
                if chunks:
                    return chunks.pop(0)
                return await original_receive()

            receive = replay_receive
        try:
            await self.app(scope, receive, wrapped_send)
        except Exception as exc:
            logger.error("request_failed id=%s type=%s", request_id, type(exc).__name__)
            if not sent:
                await reject(500, "服务内部错误，请凭请求编号检查日志。")
            else:
                raise
        finally:
            # Never log request bodies, query strings, cookies, keys or raw exceptions.
            logger.info("request_completed id=%s method=%s elapsed_ms=%d",
                        request_id, scope["method"], int((time.monotonic() - started) * 1000))
