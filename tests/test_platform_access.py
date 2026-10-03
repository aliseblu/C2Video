"""Authentication and request boundaries, all using isolated databases."""
import pytest
from fastapi.testclient import TestClient

from c2video.api.access import COOKIE, PlatformSettings
from c2video.api.app import create_app

OPERATOR = "operator-test-key-" + "a" * 32
VIEWER = "viewer-test-key-" + "b" * 32


def client(tmp_path, **kwargs):
    return TestClient(create_app(work_dir=str(tmp_path), platform=PlatformSettings(
        operator_token=OPERATOR, viewer_token=VIEWER, worker_mode="off", **kwargs)))


@pytest.mark.parametrize("path", ["/api/runs", "/api/memories", "/api/usage",
                                  "/api/platform", "/api/runs/missing/media/video",
                                  "/api/runs/missing/events", "/openapi.json", "/docs", "/redoc"])
def test_private_surfaces_require_authentication(tmp_path, path):
    response = client(tmp_path).get(path)
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["x-request-id"]


def test_cookie_login_revoke_and_role_permissions(tmp_path):
    browser = client(tmp_path)
    response = browser.post("/api/auth/login", json={"token": VIEWER})
    assert response.status_code == 200
    cookie_header = response.headers["set-cookie"].lower()
    assert "httponly" in cookie_header and "samesite=strict" in cookie_header
    stolen = browser.cookies.get(COOKIE)
    assert browser.get("/api/runs").status_code == 200
    assert browser.post("/api/runs", json={"query": "test"}).status_code == 403
    assert browser.post("/api/auth/logout", json={}).status_code == 200
    browser.cookies.set(COOKIE, stolen)
    assert browser.get("/api/runs").status_code == 401


def test_rotating_operator_key_invalidates_old_cookie(tmp_path):
    browser = client(tmp_path)
    browser.post("/api/auth/login", json={"token": OPERATOR})
    cookie = browser.cookies.get(COOKIE)
    rotated = TestClient(create_app(work_dir=str(tmp_path), platform=PlatformSettings(
        operator_token="new-key-" + "c" * 32, worker_mode="off")))
    rotated.cookies.set(COOKIE, cookie)
    assert rotated.get("/api/runs").status_code == 401


def test_bearer_auth_and_idempotent_creation(tmp_path):
    browser = client(tmp_path)
    headers = {"Authorization": f"Bearer {OPERATOR}", "Idempotency-Key": "create-test-1"}
    first = browser.post("/api/runs", headers=headers, json={"query": "test"})
    second = browser.post("/api/runs", headers=headers, json={"query": "test"})
    assert first.status_code == second.status_code == 201
    assert first.json()["run"]["run_id"] == second.json()["run"]["run_id"]
    conflict = browser.post("/api/runs", headers=headers, json={"query": "changed"})
    assert conflict.status_code == 409
    run_id = first.json()["run"]["run_id"]
    a = browser.post(f"/api/runs/{run_id}/start", headers=headers, json={})
    b = browser.post(f"/api/runs/{run_id}/start", headers=headers, json={})
    assert a.status_code == b.status_code == 200
    assert a.json()["job_status"] == b.json()["job_status"] == "queued"


def test_login_throttling_and_validation_do_not_echo_keys(tmp_path):
    browser = client(tmp_path)
    secret = "DO-NOT-ECHO-" + "x" * 600
    response = browser.post("/api/auth/login", json={"token": secret})
    assert response.status_code == 422 and "DO-NOT-ECHO" not in response.text
    for _ in range(10):
        assert browser.post("/api/auth/login", json={"token": "wrong"}).status_code == 401
    assert browser.post("/api/auth/login", json={"token": OPERATOR}).status_code == 429


def test_body_origin_and_host_limits(tmp_path):
    browser = client(tmp_path, max_body_bytes=1024)
    headers = {"Authorization": f"Bearer {OPERATOR}"}
    assert browser.post("/api/runs", headers=headers, json={"query": "x" * 2000}).status_code == 413
    assert browser.post("/api/runs", headers=headers, content="query=x").status_code == 415
    assert browser.post("/api/runs", headers={**headers, "Origin": "https://evil.example"},
                        json={"query": "x"}).status_code == 403
    assert browser.get("/api/runs", headers={**headers, "Host": "evil.example"}).status_code == 400


def test_production_fails_closed_without_credentials():
    with pytest.raises(ValueError, match="requires C2VIDEO_STUDIO_TOKEN"):
        PlatformSettings(environment="production", cookie_secure=True)
    with pytest.raises(ValueError, match="HTTPS"):
        PlatformSettings(environment="production", operator_token=OPERATOR)


def test_production_requires_queue_and_secure_cookie(tmp_path):
    browser = client(tmp_path, environment="production", cookie_secure=True)
    response = browser.post("/api/auth/login", json={"token": OPERATOR})
    assert "secure" in response.headers["set-cookie"].lower()
    headers = {"Authorization": f"Bearer {OPERATOR}"}
    result = browser.post("/api/runs", headers=headers, json={"query": "test"})
    run_id = result.json()["run"]["run_id"]
    assert browser.post(f"/api/runs/{run_id}/start", headers=headers,
                        json={"background": False}).status_code == 409
    assert browser.get("/readyz").status_code == 503


def test_open_event_stream_stops_when_session_expires(tmp_path):
    app = create_app(work_dir=str(tmp_path), platform=PlatformSettings(
        operator_token="operator-" + "x" * 32, worker_mode="off", session_seconds=1))
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"token": "operator-" + "x" * 32})
        run_id = client.post("/api/runs", json={"query": "test", "mode": "demo"}).json()["run"]["run_id"]
        response = client.get(f"/api/runs/{run_id}/events")
        assert "event: auth_required" in response.text
        assert client.get("/api/runs").status_code == 401
