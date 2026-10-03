"""Offline container acceptance in a disposable Compose project; never uses .env."""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    root = Path(__file__).resolve().parents[1]
    project = "c2video-check-" + secrets.token_hex(5)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    operator, viewer = secrets.token_urlsafe(40), secrets.token_urlsafe(40)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("C2VIDEO_", "TTS_", "COMPOSE_"))}
    env["COMPOSE_DISABLE_ENV_FILE"] = "1"
    with tempfile.TemporaryDirectory(prefix="c2video-compose-") as temporary:
        settings = Path(temporary) / "test.env"
        values = {
            "C2VIDEO_ENV_FILE": str(settings), "C2VIDEO_ENV": "production",
            "C2VIDEO_STUDIO_TOKEN": operator, "C2VIDEO_VIEWER_TOKEN": viewer,
            "C2VIDEO_ALLOWED_HOSTS": "127.0.0.1,localhost",
            "C2VIDEO_ALLOWED_ORIGINS": "", "C2VIDEO_PORT": str(port),
            "C2VIDEO_LLM_PROVIDER": "local", "TTS_PROVIDER": "edge",
            "C2VIDEO_WORKER_CONCURRENCY": "1", "C2VIDEO_MIN_FREE_DISK_MB": "128",
        }
        settings.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
        settings.chmod(0o600)
        command = ["docker", "compose", "-p", project, "--env-file", str(settings),
                   "-f", str(root / "compose.yaml")]

        def compose(*args, timeout=180):
            result = subprocess.run([*command, *args], cwd=root, env=env,
                                    text=True, capture_output=True, timeout=timeout)
            if result.returncode:
                # Only synthetic credentials and data are used in this script.
                print(result.stdout + result.stderr, flush=True)
                result.check_returncode()
            return result.stdout

        def request(path, body=None, key=operator):
            headers = {"Content-Type": "application/json"}
            if key:
                headers["Authorization"] = f"Bearer {key}"
            req = Request(f"http://127.0.0.1:{port}{path}", headers=headers,
                          data=json.dumps(body).encode() if body is not None else None)
            try:
                response = urlopen(req, timeout=10)
            except HTTPError as exc:
                response = exc
            with response:
                return response.status, response.headers, response.read()

        def wait_state(run_id, expected, timeout=120):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                code, _, data = request(f"/api/runs/{run_id}")
                assert code == 200
                result = json.loads(data)
                state = result["run"]["state"]
                if state == expected:
                    return result
                if state in {"FAILED", "CANCELED"}:
                    raise AssertionError(f"Fixture unexpectedly ended in {state}: {result['run'].get('error')}")
                time.sleep(0.4)
            raise TimeoutError(f"Fixture did not reach {expected}")

        try:
            compose("config", "--quiet")
            compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "90")
            assert request("/readyz", key=None)[0] == 200
            assert request("/api/runs", key=None)[0] == 401
            assert request("/api/runs", {"query": "forbidden", "mode": "demo"}, viewer)[0] == 403
            status, headers, _ = request("/api/auth/login", {"token": operator}, key=None)
            assert status == 200 and "Secure" in headers["Set-Cookie"]
            assert "HttpOnly" in headers["Set-Cookie"]
            assert request("/api/metrics", key=None)[0] == 401
            assert request("/api/metrics")[0] == 200
            compose("exec", "-T", "-w", "/tmp", "worker", "python", "-c",
                    "from pathlib import Path; import c2video; "
                    "p=Path(c2video.__file__).parent; "
                    "assert 'site-packages' in str(p); "
                    "assert (p/'fixtures/demo/candidates.json').is_file(); "
                    "assert (p/'fixtures/evals/context_candidates.json').is_file()")
            # Verify Chromium/fonts execute with the actual non-root container restrictions.
            assert compose("exec", "-T", "worker", "id", "-u").strip() == "10001"
            compose("exec", "-T", "worker", "python", "-c",
                    "from pathlib import Path; from c2video.pipeline.card import screenshot_html; "
                    "screenshot_html('<html><body>中文部署验收</body></html>', Path('/tmp/card.png')); "
                    "assert Path('/tmp/card.png').stat().st_size > 1000")
            code, _, payload = request("/api/runs", {
                "query": "Offline deployment acceptance", "mode": "demo", "autonomy": "supervised"})
            assert code == 201
            run_id = json.loads(payload)["run"]["run_id"]
            # Queue persists while worker is down and is claimed on restart.
            compose("stop", "worker")
            assert request("/readyz", key=None)[0] == 503
            assert request(f"/api/runs/{run_id}/start", {})[0] == 200
            compose("start", "worker")
            wait_state(run_id, "WAIT_GATE_1")
            for next_state in ("WAIT_GATE_2", "COMPLETE"):
                assert request(f"/api/runs/{run_id}/actions", {"action": "approve_gate"})[0] == 200
                assert request(f"/api/runs/{run_id}/start", {})[0] == 200
                snapshot = wait_state(run_id, next_state)
            assert snapshot["documents"]["publish_kit/qc.after.json"]["ok"]
            status, _, media = request(f"/api/runs/{run_id}/media/video")
            assert status == 200 and len(media) > 1000 and b"ftyp" in media[:32]
            assert request(f"/api/runs/{run_id}/media/video", key=None)[0] == 401
            # Stop all writers, then exercise existing backup, verify and restore commands.
            compose("stop", "worker", "api")
            base = ("run", "--rm", "--no-deps", "--entrypoint", "python", "worker", "-m", "c2video.ops")
            compose(*base, "backup", "--work-dir", "/data/work", "--db",
                    "/data/work/c2video-agent.db", "--output", "/data/acceptance-backup",
                    "--confirm-services-stopped")
            compose(*base, "verify", "--backup", "/data/acceptance-backup")
            compose(*base, "restore", "--backup", "/data/acceptance-backup",
                    "--output", "/data/acceptance-restore")
            compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "90")
            wait_state(run_id, "COMPLETE")
            print(json.dumps({"passed": True, "mode": "isolated-offline-demo",
                              "checks": ["nonroot-browser", "authentication", "secure-cookie",
                                         "metrics", "queue-restart", "both-review-gates",
                                         "video-and-qc", "backup-verify-restore", "restart-persistence"],
                              "external_provider_calls": 0}, ensure_ascii=False))
        except Exception:
            print(compose("logs", "--no-color", "--tail", "60"), flush=True)
            raise
        finally:
            # Only this randomly named test project's containers and synthetic data.
            compose("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    main()
