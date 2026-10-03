"""Browser QA with real local API + real provider wrapper + mocked model HTTP."""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Isolate the global API app import before any project configuration is loaded.
root = Path(os.environ["C2VIDEO_QA_DIR"]).resolve()
if (root / "work").exists():
    raise RuntimeError("Choose a fresh QA output directory; existing work is never reused")
root.mkdir(parents=True, exist_ok=True)
for key, value in {
    "PYTHON_DOTENV_DISABLED": "1", "C2VIDEO_ENV": "development",
    "C2VIDEO_WORKER_MODE": "embedded", "C2VIDEO_WORK_DIR": str(root / "work"),
    "C2VIDEO_FINAL_DIR": str(root / "final"), "C2VIDEO_LLM_PROVIDER": "local",
    "C2VIDEO_TTS_PROVIDER": "edge", "TTS_PROVIDER": "edge",
    "C2VIDEO_STUDIO_TOKEN": "", "C2VIDEO_VIEWER_TOKEN": "",
}.items():
    os.environ[key] = value
os.environ.pop("C2VIDEO_CONFIG", None)

import c2video.memory as memory_module  # noqa: E402
from c2video.api.access import PlatformSettings  # noqa: E402
from c2video.api.app import create_app  # noqa: E402
from c2video.config.schema import C2VideoConfig, LLMConfig  # noqa: E402
from c2video.llm.api_impl import APICompatibleLLMProvider  # noqa: E402
from c2video.util import discover_browser_executable  # noqa: E402


def mocked_model(request):
    payload = {
        "memories": [{"content": "口播保持简洁克制，避免夸张用词。", "scope": "script",
                      "evidence_quote": "以后口播少用夸张词，保持简洁", "reason": "明确表达今后的长期偏好",
                      "confidence": 0.93}],
        "reason": "保留长期要求",
    }
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}],
                                   "usage": {"prompt_tokens": 120, "completion_tokens": 48, "total_tokens": 168}})


class FrozenTransportProvider(APICompatibleLLMProvider):
    def __init__(self, config):
        super().__init__(config)
        self.original = self._client
        self._client = httpx.AsyncClient(base_url=config.api_base_url, transport=httpx.MockTransport(mocked_model))

    async def close(self):
        await self.original.aclose()
        await super().close()


memory_module.APICompatibleLLMProvider = FrozenTransportProvider
config = C2VideoConfig(llm=LLMConfig(provider="api", api_base_url="https://example.invalid/v1",
                                    api_key="qa-not-a-real-key", model="QA-模拟响应",
                                    input_usd_per_million=1, output_usd_per_million=2))
platform = PlatformSettings(operator_token="qa-operator-" + "x" * 32,
                            viewer_token="qa-viewer-" + "y" * 32, worker_mode="embedded")
app = create_app(work_dir=str(root / "work"), config=config, platform=platform)
run_id = app.state.service.create_run(query="自动记忆与用量看板验收（测试数据）", mode="demo", autonomy="supervised")["run"]["run_id"]
app.state.service.start_worker(run_id)
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
port = sock.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
thread.start()
deadline = time.monotonic() + 10
while not server.started and time.monotonic() < deadline:
    time.sleep(0.05)
if not server.started:
    raise RuntimeError("QA server failed to start")
base = f"http://127.0.0.1:{port}"
screenshots = []
errors = []
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(discover_browser_executable()), headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1050})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{base}/runs/{run_id}", wait_until="networkidle")
            page.get_by_role("heading", name="登录工作空间").wait_for()
            assert page.request.get(f"{base}/api/runs").status == 401
            page.screenshot(path=str(root / "login-desktop.png"), full_page=True)
            screenshots.append(str(root / "login-desktop.png"))
            page.get_by_label("访问密钥").fill(platform.operator_token)
            page.get_by_role("button", name="登录", exact=True).click()
            page.get_by_label("这次有什么建议？").fill("以后口播少用夸张词，保持简洁。这一条的开头再短一点。")
            page.get_by_role("button", name="提交反馈", exact=True).click()
            page.get_by_role("status").filter(has_text="自动记住 1 条").wait_for()
            result = page.request.get(f"{base}/api/usage?days=7").json()
            assert result["totals"]["calls"] == 1
            assert result["totals"]["input_tokens"] == 120
            assert result["totals"]["output_tokens"] == 48
            assert result["purposes"][0]["purpose"] == "memory"
            assert result["recent"][0]["run_id"] == run_id
            assert result["memories"]["approved_now"] == 1

            page.get_by_role("button", name="通过", exact=True).wait_for(timeout=15000)
            assert page.request.get(f"{base}/api/runs/{run_id}").json()["run"]["state"] == "WAIT_GATE_1"
            page.get_by_role("button", name="通过", exact=True).click()
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                snapshot = page.request.get(f"{base}/api/runs/{run_id}").json()
                if snapshot["run"]["state"] == "WAIT_GATE_2":
                    break
                if snapshot["run"]["state"] in {"FAILED", "CANCELED"}:
                    raise AssertionError(snapshot["run"])
                page.wait_for_timeout(200)
            assert snapshot["run"]["state"] == "WAIT_GATE_2"
            assert snapshot["documents"]["publish_kit/qc.after.json"]["ok"]
            page.get_by_role("button", name="通过", exact=True).wait_for()
            page.get_by_role("button", name="通过", exact=True).click()
            page.wait_for_function("""async id => {
                const response = await fetch('/api/runs/' + id);
                return (await response.json()).run.state === 'COMPLETE';
            }""", arg=run_id, timeout=15000)
            for mode, viewport in [("desktop", {"width": 1440, "height": 1050}),
                                   ("mobile", {"width": 390, "height": 844})]:
                page.set_viewport_size(viewport)
                for route, ready in [("usage", "已报告 Token"), ("platform", "持久队列"), ("memory", "AI 已整理")]:
                    page.goto(f"{base}/{route}", wait_until="networkidle")
                    page.get_by_text(ready, exact=True).wait_for()
                    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), (mode, route)
                    image = root / f"{route}-{mode}.png"
                    page.screenshot(path=str(image), full_page=True)
                    screenshots.append(str(image))

            page.get_by_role("button", name="停用", exact=True).click()
            page.get_by_text("已停用", exact=True).wait_for()
            assert page.request.get(f"{base}/api/usage?days=7").json()["memories"]["approved_now"] == 0
            page.goto(f"{base}/usage", wait_until="networkidle")
            page.get_by_label("统计范围").select_option("30")
            page.get_by_text("已报告 Token", exact=True).wait_for()
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
            page.get_by_role("button", name="切换到深色模式").click()
            page.screenshot(path=str(root / "usage-mobile-dark.png"), full_page=True)
            screenshots.append(str(root / "usage-mobile-dark.png"))
            page.set_viewport_size({"width": 1440, "height": 1050})
            page.get_by_role("button", name="退出", exact=True).click()
            page.get_by_label("访问密钥").wait_for()
            assert page.request.get(f"{base}/api/runs").status == 401
            page.get_by_label("访问密钥").fill(platform.viewer_token)
            page.get_by_role("button", name="登录", exact=True).click()
            page.goto(f"{base}/memory", wait_until="networkidle")
            assert page.get_by_role("button", name="启用", exact=True).is_disabled()
            assert page.request.post(f"{base}/api/runs", data={"query": "forbidden", "mode": "demo"}).status == 403
            page.goto(f"{base}/runs/{run_id}", wait_until="networkidle")
            assert page.get_by_label("这次有什么建议？").count() == 0
            assert not page.evaluate("JSON.stringify(localStorage).includes('qa-operator')")
            assert not page.evaluate("JSON.stringify(localStorage).includes('qa-viewer')")
            assert errors == [], errors
        finally:
            browser.close()
    print(json.dumps({"passed": True, "run_state": "COMPLETE", "authentication": "login/logout/viewer checked", "model": "mocked HTTP response; no external model request",
                      "screenshots": screenshots, "page_errors": errors}, ensure_ascii=False))
finally:
    server.should_exit = True
    thread.join(timeout=5)
    sock.close()
