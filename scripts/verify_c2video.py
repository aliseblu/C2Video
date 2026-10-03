"""Exercise packaged C2Video Studio against an isolated real Demo API."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright


def wait_http(url: str, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(trust_env=False) as client:
        while time.monotonic() < deadline:
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {url}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chromium", required=True)
    parser.add_argument("--output", default=f"artifacts/ui-qa/{date.today().isoformat()}/c2video")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    output = root / args.output
    output.mkdir(parents=True, exist_ok=True)
    qa_work = Path(tempfile.mkdtemp(prefix="c2video-ui-qa-"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    environment = dict(os.environ)
    environment["C2VIDEO_WORK_DIR"] = str(qa_work)
    environment["C2VIDEO_BROWSER_EXECUTABLE"] = str(Path(args.chromium).resolve())
    environment["PYTHONPATH"] = str(root)
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "c2video.api.app:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=root, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    errors: list[str] = []
    screenshots: list[str] = []
    try:
        wait_http(f"{base}/api/health")
        with httpx.Client(base_url=base, trust_env=False, timeout=90) as client:
            health = client.get("/api/health").json()
            created = client.post("/api/runs", json={
                "query": "C2Video 离线验收：AI 资讯速览", "autonomy": "auto", "mode": "demo",
            })
            created.raise_for_status()
            run_id = created.json()["run"]["run_id"]
            completed = client.post(f"/api/runs/{run_id}/start", json={"background": False})
            completed.raise_for_status()
            assert completed.json()["run"]["state"] == "COMPLETE", completed.text

        routes = {
            "dashboard": "/", "new-run": "/new",
            "timeline": f"/runs/{run_id}?view=timeline",
            "curation": f"/runs/{run_id}?view=curation",
            "script": f"/runs/{run_id}?view=script",
            "qc": f"/runs/{run_id}?view=qc",
            "memory": "/memory", "settings": "/settings",
            "missing-run": "/runs/missing-qa-run",
        }
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.chromium, headless=True)
            try:
                for theme in ("light", "dark"):
                    for label, viewport in (
                        ("desktop", {"width": 1440, "height": 1000}),
                        ("mobile", {"width": 390, "height": 844}),
                    ):
                        context = browser.new_context(viewport=viewport, device_scale_factor=1)
                        context.add_init_script(f"localStorage.setItem('c2video-theme', '{theme}')")
                        page = context.new_page()
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.on("console", lambda msg: errors.append(msg.text)
                                if msg.type == "error" and "404" not in msg.text
                                and "503" not in msg.text else None)
                        for name, route in routes.items():
                            page.goto(base + route, wait_until="networkidle")
                            page.evaluate("document.fonts.ready")
                            expect(page).to_have_title("C2Video Agent Studio")
                            page.evaluate("""() => {
                                const overlay = document.createElement('div');
                                overlay.id = 'codex-agent-overlay-root';
                                overlay.dataset.codexAgentOverlayRoot = 'true';
                                overlay.textContent = 'debug pointer';
                                document.body.append(overlay);
                            }""")
                            expect(page.locator("#codex-agent-overlay-root")).to_be_hidden()
                            assert not page.evaluate(
                                "document.documentElement.scrollWidth > window.innerWidth"
                            ), f"Horizontal overflow: {theme}/{label}/{name}"
                            if name == "missing-run":
                                expect(page.get_by_role("alert")).to_contain_text("Run not found")
                                expect(page.get_by_role("link", name="返回总览")).to_be_visible()
                            if name == "curation":
                                claim = page.locator(".claim-list p").first
                                expect(claim).to_be_visible()
                                assert claim.bounding_box()["width"] > 150
                            if name == "script":
                                editor_width = page.locator(".script-editor").bounding_box()["width"]
                                assert editor_width >= (800 if label == "desktop" else 350)
                            filename = f"{theme}-{label}-{name}.png"
                            page.screenshot(path=output / filename)
                            screenshots.append(filename)
                            if name == "qc":
                                page.get_by_role("button", name="播放").click()
                                expect(page.locator("video")).to_be_visible()
                                page.wait_for_function(
                                    "document.querySelector('video')?.readyState >= 2"
                                )
                                assert page.locator("video").evaluate("v => v.videoWidth") == 1080
                        page.goto(base + "/new", wait_until="networkidle")
                        page.route("**/api/runs", lambda route: route.fulfill(
                            status=503, content_type="application/json",
                            body=json.dumps({"detail": "服务暂不可用，请重试"}),
                        ))
                        page.get_by_role("button", name="做成片", exact=True).click()
                        expect(page.get_by_role("alert")).to_contain_text("服务暂不可用")
                        expect(page.get_by_role("button", name="做成片", exact=True)).to_be_enabled()
                        filename = f"{theme}-{label}-create-error.png"
                        page.screenshot(path=output / filename)
                        screenshots.append(filename)
                        context.close()

                # Choose Demo Mode in this test browser, then exercise the real worker.
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                page = context.new_page()
                page.route("**/api/health", lambda route: route.fulfill(
                    content_type="application/json",
                    body=json.dumps({**health, "live_ready": False, "mode": "demo"}),
                ))
                page.goto(base + "/new", wait_until="networkidle")
                page.get_by_label("想做什么").fill("C2Video 浏览器创建与后台运行验收")
                page.get_by_role("button", name="做成片", exact=True).click()
                page.wait_for_url("**/runs/*")
                expect(page.locator(".run-title .status")).to_have_text("完成", timeout=90000)
                ui_run_url = page.url
                context.close()
            finally:
                browser.close()
        assert not errors, errors
        report = {"demo_run_id": run_id, "ui_run_url": ui_run_url, "qa_work": str(qa_work),
                  "screenshots": screenshots, "browser_errors": errors}
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False))
    finally:
        api.terminate()
        try:
            api.wait(timeout=5)
        except subprocess.TimeoutExpired:
            api.kill()
            api.wait(timeout=5)


if __name__ == "__main__":
    main()
