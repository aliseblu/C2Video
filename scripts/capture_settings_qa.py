"""Capture deterministic responsive QA screenshots for the Studio settings page."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chromium", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", default=f"artifacts/ui-qa/{date.today().isoformat()}")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    output = root / args.output
    output.mkdir(parents=True, exist_ok=True)
    states = {
        "public-ready": {
            "ok": True,
            "version": "0.2.0",
            "mode": "live",
            "live_ready": True,
            "source_provider": "public",
            "llm_provider": "local",
            "source_configured": False,
            "checks": {"ffmpeg": True, "browser": True},
        },
        "zhihu-needs-secret": {
            "ok": True,
            "version": "0.2.0",
            "mode": "demo",
            "live_ready": False,
            "source_provider": "zhihu",
            "llm_provider": "api",
            "source_configured": False,
            "checks": {"ffmpeg": True, "browser": True},
        },
    }
    viewports = {
        "desktop": {"width": 1440, "height": 1000},
        "mobile": {"width": 390, "height": 844},
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium, headless=True)
        try:
            for state_name, health in states.items():
                for viewport_name, viewport in viewports.items():
                    context = browser.new_context(viewport=viewport, device_scale_factor=1)
                    context.add_init_script("localStorage.setItem('c2video-theme', 'light')")
                    page = context.new_page()
                    page.route(
                        "**/api/health",
                        lambda route, _request, payload=health: route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps(payload),
                        ),
                    )
                    page.goto(f"{args.base_url}/settings", wait_until="networkidle")
                    expected = (
                        "RSS · Hacker News · GitHub"
                        if state_name == "public-ready"
                        else "知乎 · 等待配置"
                    )
                    page.get_by_text(expected, exact=True).wait_for()
                    overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
                    if overflow:
                        raise RuntimeError(f"Horizontal overflow: {state_name}/{viewport_name}")
                    path = output / f"settings-{state_name}-{viewport_name}.png"
                    page.screenshot(path=path, full_page=False)
                    context.close()
        finally:
            browser.close()
    print(output)


if __name__ == "__main__":
    main()

