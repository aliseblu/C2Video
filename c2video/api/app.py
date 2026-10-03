"""FastAPI routes for Agent Studio and SSE event observation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from c2video import __version__
from c2video.agent.locking import RunBusy
from c2video.agent.supervisor import Supervisor
from c2video.api.access import COOKIE, AccessControl, PlatformMiddleware, PlatformSettings
from c2video.application import ApplicationService
from c2video.capacity import CapacityExceeded, ensure_disk_capacity, minimum_free_bytes
from c2video.config.loader import load_config
from c2video.media_tools import media_tools_status
from c2video.memory import memory_availability
from c2video.source.status import source_status
from c2video.storage.jobs import RunQueue
from c2video.usage import usage_dashboard
from c2video.util import discover_browser_executable


def _studio_config():
    # Invalid configuration must fail startup, never silently create a Demo service.
    return load_config()


class CreateRunRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    autonomy: Literal["supervised", "assisted", "auto"] = "assisted"
    target_duration_seconds: int = Field(default=60, ge=20, le=600)
    preferred_format: Literal["news_recap", "single_explainer", "thread_story"] | None = None
    mode: Literal["live", "demo"] | None = None
    materials: list[dict[str, Any]] | dict[str, Any] | None = None


class StartRequest(BaseModel):
    background: bool = True


class ActionRequest(BaseModel):
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


class FeedbackRequest(BaseModel):
    category: str = "preference"
    comment: str = Field(min_length=1, max_length=4000)
    rating: int | None = Field(default=None, ge=1, le=5)
    target_id: str | None = None


class LoginRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class MemoryStatusRequest(BaseModel):
    status: str


def create_app(
    *,
    work_dir: str | None = None,
    db_path: str | None = None,
    config=None,
    platform: PlatformSettings | None = None,
) -> FastAPI:
    resolved_work = work_dir or os.environ.get("C2VIDEO_WORK_DIR", "work")
    service = ApplicationService(work_dir=resolved_work, db_path=db_path, config=config)
    settings = platform or PlatformSettings.from_env()
    access = AccessControl(service.store, settings)
    supervisor = Supervisor(service, concurrency=settings.concurrency)
    disk_reserve = minimum_free_bytes(production=settings.environment == "production")

    @asynccontextmanager
    async def lifespan(_app):
        task = asyncio.create_task(supervisor.run()) if settings.worker_mode == "embedded" else None
        try:
            yield
        finally:
            supervisor.stopping.set()
            if task:
                await task

    app = FastAPI(title="C2Video Agent Studio API", version=__version__, lifespan=lifespan)
    app.state.service = service
    app.state.access = access
    app.add_middleware(PlatformMiddleware, access=access)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _exc: RequestValidationError):
        # Default validation errors can echo secrets from login request bodies.
        return JSONResponse({"detail": "请求字段不合法，请检查必填项、类型与长度。"}, status_code=422)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, _exc: Exception):
        return JSONResponse({"detail": "服务内部错误，请检查日志。",
                             "request_id": getattr(request.state, "request_id", "")}, status_code=500)

    @app.exception_handler(RunBusy)
    async def busy_run(_request: Request, exc: RunBusy):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(CapacityExceeded)
    async def capacity_exceeded(_request: Request, exc: CapacityExceeded):
        return JSONResponse({"detail": str(exc)}, status_code=503, headers={"Retry-After": "30"})

    @app.get("/healthz", include_in_schema=False)
    def liveness():
        return {"ok": True}

    @app.get("/readyz", include_in_schema=False)
    def readiness():
        try:
            with service.store.transaction() as db:
                db.execute("SELECT 1").fetchone()
            ready = RunQueue(service.store).status()["worker_ready"]
        except Exception:
            ready = False
        return JSONResponse({"ok": ready}, status_code=200 if ready else 503)

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        return {"required": bool(settings.operator_token),
                "role": request.state.role, "environment": settings.environment}

    @app.post("/api/auth/login")
    def login(body: LoginRequest, request: Request):
        try:
            token, role = access.login(body.token, request.client.host if request.client else "unknown")
        except PermissionError as exc:
            raise HTTPException(429, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(401, str(exc)) from exc
        response = JSONResponse({"role": role})
        response.set_cookie(COOKIE, token, max_age=settings.session_seconds, httponly=True,
                            secure=settings.cookie_secure, samesite="strict", path="/")
        return response

    @app.post("/api/auth/logout")
    def logout(request: Request):
        access.logout(request.headers)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/", secure=settings.cookie_secure,
                               httponly=True, samesite="strict")
        return response

    @app.get("/api/platform")
    def platform_status():
        return {**RunQueue(service.store).status(), "environment": settings.environment,
                "authentication_enabled": bool(settings.operator_token),
                "worker_mode": settings.worker_mode, "concurrency_limit": settings.concurrency,
                "version": __version__, "memory_consumption": "api-model-only",
                "execution_semantics": "single-host lock; ambiguous failures require manual retry"}


    @app.get("/api/metrics", response_class=PlainTextResponse)
    def metrics():
        """Authenticated aggregates; no query text, run IDs or secrets as labels."""
        import shutil

        status = RunQueue(service.store).status()
        free = shutil.disk_usage(service.work_dir).free
        lines = ["# TYPE c2video_worker_ready gauge",
                 f"c2video_worker_ready {int(status['worker_ready'])}",
                 "# TYPE c2video_disk_free_bytes gauge", f"c2video_disk_free_bytes {free}",
                 "# TYPE c2video_queue_jobs gauge"]
        for state in ("queued", "running", "idle", "failed"):
            lines.append(f'c2video_queue_jobs{{status="{state}"}} {status["counts"].get(state, 0)}')
        return PlainTextResponse("\n".join(lines) + "\n",
                                 media_type="text/plain; version=0.0.4")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        browser_path = discover_browser_executable()
        source = source_status(service.config)
        media = media_tools_status()
        live_ready = service.live_ready()
        checks = {
            "sqlite": Path(service.db_path).exists(),
            "ffmpeg": media["ok"],
            "browser": bool(browser_path and browser_path.exists()),
            "studio": (Path(__file__).resolve().parent / "static" / "index.html").exists(),
            "source": live_ready,
        }
        return {
            "ok": all(value for key, value in checks.items() if key != "source"),
            "version": __version__,
            "mode": "live" if service.config is not None else "demo",
            "database": str(service.db_path),
            "checks": checks,
            "live_ready": live_ready,
            "source_provider": getattr(getattr(service.config, "source", None), "provider", None),
            "llm_provider": getattr(getattr(service.config, "llm", None), "provider", None),
            "memory": memory_availability(service.config),
            "source_configured": source["configured"],
            "source_mode": source["mode"],
            "source_detail": source["detail"],
            "ffmpeg_path": media["ffmpeg"],
            "ffprobe_path": media["ffprobe"],
            "ffmpeg_detail": media["detail"],
        }

    @app.get("/api/usage")
    def usage(days: int = Query(default=7, ge=7, le=90)) -> dict[str, Any]:
        try:
            return usage_dashboard(service.db_path, days=days)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/runs")
    def list_runs() -> dict[str, Any]:
        return {"items": service.list_runs()}

    @app.post("/api/runs", status_code=201)
    def create_run(body: CreateRunRequest, request: Request) -> dict[str, Any]:
        ensure_disk_capacity(service.work_dir, minimum_bytes=disk_reserve)
        key = request.headers.get("Idempotency-Key")
        if key and (len(key) > 128 or not key.isascii()):
            raise HTTPException(422, "Idempotency-Key 必须是 1–128 个 ASCII 字符。")
        payload = body.model_dump()
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        try:
            if not key:
                return service.create_run(**payload)
            with service.store.atomic() as db:
                previous = db.execute("SELECT * FROM api_idempotency WHERE request_key=?", (key,)).fetchone()
                if previous:
                    if previous["body_hash"] != digest:
                        raise HTTPException(409, "此幂等键已经用于不同的创建请求。")
                    return service.get_run(previous["run_id"]) or {}
                result = service.create_run(**payload)
                db.execute("INSERT INTO api_idempotency VALUES (?,?,?)",
                           (key, digest, result["run"]["run_id"]))
                return result
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        snapshot = service.get_run(run_id)
        if not snapshot:
            raise HTTPException(status_code=404, detail="Run not found")
        return snapshot

    @app.post("/api/runs/{run_id}/start")
    async def start_run(run_id: str, request: StartRequest) -> dict[str, Any]:
        if not service.get_run(run_id):
            raise HTTPException(status_code=404, detail="Run not found")
        if request.background:
            ensure_disk_capacity(service.work_dir, minimum_bytes=disk_reserve)
            try:
                job = service.start_worker(run_id)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            return {"run_id": run_id, "job_status": job["status"],
                    "worker_pid": job["worker_pid"]}
        if settings.environment == "production":
            raise HTTPException(409, "生产模式仅允许持久队列执行。")
        await service.execute(run_id)
        return service.get_run(run_id) or {}

    @app.post("/api/runs/{run_id}/actions")
    def run_action(run_id: str, request: ActionRequest) -> dict[str, Any]:
        try:
            return service.action(run_id, request.action, request.payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/feedback", status_code=201)
    async def feedback(run_id: str, request: FeedbackRequest) -> dict[str, Any]:
        try:
            return await service.feedback_async(run_id, **request.model_dump())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/memories")
    def memories(status: str | None = None) -> dict[str, Any]:
        return {"items": service.store.list_memories(status=status)}

    @app.post("/api/memories/{memory_id}")
    def memory_status(memory_id: str, request: MemoryStatusRequest) -> dict[str, Any]:
        try:
            return service.store.set_memory_status(memory_id, request.status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/replay")
    def replay(run_id: str) -> dict[str, Any]:
        try:
            return service.runtime.replay(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request, after: int = Query(default=0, ge=0)) -> StreamingResponse:
        if not service.store.get_run(run_id):
            raise HTTPException(status_code=404, detail="Run not found")
        try:
            cursor = max(after, int(request.headers.get("Last-Event-ID", "0")))
            if cursor < 0 or cursor > 2**63 - 1:
                raise ValueError("cursor out of range")
        except ValueError as exc:
            raise HTTPException(422, "无效的事件游标。") from exc

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                if settings.operator_token and not await asyncio.to_thread(access.role, request.headers):
                    yield "event: auth_required\ndata: {}\n\n"
                    return
                items = await asyncio.to_thread(service.store.list_events, run_id, after_sequence=cursor)
                for item in items:
                    cursor = item["sequence"]
                    yield f"id: {cursor}\nevent: run_event\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                run = await asyncio.to_thread(service.store.get_run, run_id)
                if run and (run["state"] in {"COMPLETE", "FAILED", "CANCELED"}
                            or run["is_paused"] or run["state"].startswith("WAIT")):
                    yield "event: stream_end\ndata: {}\n\n"
                    return
                yield ": heartbeat\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no"})

    @app.get("/api/runs/{run_id}/media/{kind}")
    def media(run_id: str, kind: str) -> FileResponse:
        names = {"video": "video.mp4", "cover": "cover.png", "publish": "publish.md"}
        if kind not in names:
            raise HTTPException(status_code=404, detail="Media not found")
        path = Path(service.work_dir) / "agent_runs" / run_id / "publish_kit" / names[kind]
        # Resolving the allowed root would let a symlink redefine the security boundary.
        root = Path(service.work_dir).resolve() / "agent_runs" / run_id / "publish_kit"
        if not service.store.get_run(run_id) or not path.resolve().is_relative_to(root) or not path.is_file():
            raise HTTPException(status_code=404, detail="Media not found")
        return FileResponse(path)

    dist = Path(__file__).resolve().parent / "static"
    if dist.exists():
        assets = dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="studio-assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def studio_shell(full_path: str) -> FileResponse:
            # Missing API endpoints must never be disguised as the Studio HTML shell.
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(
                    status_code=404,
                    detail="接口不存在。若刚更新过项目，请重启 C2Video Studio，并使用启动时显示的地址。",
                )
            candidate = (dist / full_path).resolve()
            if candidate.is_relative_to(dist.resolve()) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
    return app


app = create_app(config=_studio_config())
