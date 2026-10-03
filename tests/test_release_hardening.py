"""Release boundary regression tests; no remote services or production data."""

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from c2video.api.access import PlatformSettings
from c2video.api.app import create_app
from c2video.capacity import CapacityExceeded, minimum_free_bytes, queue_limit
from c2video.deployment import worker_healthy
from c2video.storage.jobs import RunQueue

KEY = "release-test-" + "a" * 32


def make_client(tmp_path, **settings):
    app = create_app(work_dir=str(tmp_path / "work"), platform=PlatformSettings(
        operator_token=KEY, worker_mode="off", **settings))
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {KEY}"
    return client, app.state.service


@pytest.mark.parametrize("value", ["0", "-1", "10001", "not-a-number"])
def test_invalid_queue_capacity_fails_closed(monkeypatch, value):
    monkeypatch.setenv("C2VIDEO_MAX_QUEUED_RUNS", value)
    with pytest.raises(ValueError):
        queue_limit()


def test_queue_limit_preserves_existing_idempotent_job(tmp_path, monkeypatch):
    monkeypatch.setenv("C2VIDEO_MAX_QUEUED_RUNS", "1")
    client, service = make_client(tmp_path)
    first = client.post("/api/runs", json={"query": "one", "mode": "demo"}).json()["run"]["run_id"]
    second = client.post("/api/runs", json={"query": "two", "mode": "demo"}).json()["run"]["run_id"]
    assert client.post(f"/api/runs/{first}/start", json={}).status_code == 200
    assert client.post(f"/api/runs/{first}/start", json={}).status_code == 200
    response = client.post(f"/api/runs/{second}/start", json={})
    assert response.status_code == 503 and response.headers["retry-after"] == "30"
    assert RunQueue(service.store).status()["counts"]["queued"] == 1
    # Internal callers cannot bypass the same admission control.
    with pytest.raises(CapacityExceeded):
        RunQueue(service.store).enqueue(second)


def test_low_disk_blocks_new_work_but_not_observation(tmp_path, monkeypatch):
    monkeypatch.setenv("C2VIDEO_MIN_FREE_DISK_MB", "1024")
    client, _ = make_client(tmp_path)
    monkeypatch.setattr("c2video.capacity.shutil.disk_usage", lambda path: SimpleNamespace(free=10))
    assert client.post("/api/runs", json={"query": "one"}).status_code == 503
    assert client.get("/api/runs").json()["items"] == []
    assert client.get("/healthz").status_code == 200


def test_production_disk_reserve_cannot_be_disabled(monkeypatch):
    monkeypatch.setenv("C2VIDEO_MIN_FREE_DISK_MB", "0")
    with pytest.raises(ValueError):
        minimum_free_bytes(production=True)


@pytest.mark.parametrize("origin", ["*", "http://example.com", "https://example.com/path",
                                    "https://user:password@example.com", "https://example.com?q=x"])
def test_production_rejects_invalid_origins(origin):
    with pytest.raises(ValueError, match="origins"):
        PlatformSettings(environment="production", operator_token=KEY,
                         cookie_secure=True, allowed_origins=(origin,))


def test_browser_security_and_exact_json_content_type(tmp_path):
    client, _ = make_client(tmp_path, environment="production", cookie_secure=True)
    response = client.get("/healthz")
    assert response.headers["strict-transport-security"] == "max-age=31536000"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert client.post("/api/runs", json={"query": "x"},
                       headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/runs", content='{"query":"x"}',
                       headers={"Content-Type": "application/json-malicious"}).status_code == 415


def test_metrics_are_protected_and_do_not_leak_identifiers(tmp_path):
    client, service = make_client(tmp_path)
    run = service.create_run(query="PRIVATE QUERY", mode="demo")
    response = client.get("/api/metrics")
    assert response.status_code == 200
    assert "c2video_disk_free_bytes " in response.text
    assert "c2video_worker_ready 0" in response.text
    assert run["run"]["run_id"] not in response.text
    assert "PRIVATE QUERY" not in response.text and KEY not in response.text
    client.headers.pop("Authorization")
    assert client.get("/api/metrics").status_code == 401


@pytest.mark.parametrize("directory_link", [True, False])
def test_media_symlink_cannot_escape_publish_directory(tmp_path, directory_link):
    client, service = make_client(tmp_path)
    run_id = service.create_run(query="media", mode="demo")["run"]["run_id"]
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "video.mp4").write_bytes(b"not-public")
    root = tmp_path / "work" / "agent_runs" / run_id / "publish_kit"
    root.parent.mkdir(parents=True, exist_ok=True)
    if directory_link:
        root.symlink_to(outside, target_is_directory=True)
    else:
        root.mkdir()
        (root / "video.mp4").symlink_to(outside / "video.mp4")
    assert client.get(f"/api/runs/{run_id}/media/video").status_code == 404


def test_healthcheck_missing_stale_and_fresh_database(tmp_path):
    db = tmp_path / "heartbeat.db"
    assert not worker_healthy(db, now=100)
    assert not db.exists()
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE worker_status(heartbeat_at REAL)")
        conn.execute("INSERT INTO worker_status VALUES(95)")
    assert worker_healthy(db, now=100)
    assert not worker_healthy(db, now=110)
    assert not worker_healthy(db, now=90)

