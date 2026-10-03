from pathlib import Path

import pytest

from c2video.application import ApplicationService
from c2video.domain.models import Artifact
from c2video.io_atomic import atomic_write_text
from c2video.ops import backup, restore, verify_backup


def fixture(tmp_path):
    work = tmp_path / "work"
    service = ApplicationService(work_dir=str(work))
    run_id = service.create_run(query="backup test")["run"]["run_id"]
    artifact = work / "agent_runs" / run_id / "test.json"
    atomic_write_text(artifact, '{"fixture":true}')
    service.store.add_artifact(Artifact(run_id=run_id, kind="test", path=str(artifact)))
    service.start_worker(run_id)
    return service, run_id, work


def test_backup_restore_roundtrip_and_no_automatic_resume(tmp_path):
    service, run_id, work = fixture(tmp_path)
    output = backup(work, Path(service.db_path), tmp_path / "backup", services_stopped=True)
    assert len(verify_backup(output)["files"]) >= 2
    restored = restore(output, tmp_path / "restored")
    clone = ApplicationService(work_dir=str(restored))
    snapshot = clone.get_run(run_id)
    assert snapshot["run"]["is_paused"] == 1
    assert snapshot["goal"]["query"] == "backup test"
    path = Path(snapshot["artifacts"][0]["path"])
    assert path.is_relative_to(restored) and path.read_text() == '{"fixture":true}'
    # The original was not changed by restoration.
    assert service.get_run(run_id)["run"]["is_paused"] == 0
    with clone.store.transaction() as db:
        assert db.execute("SELECT status FROM run_jobs").fetchone()[0] == "idle"


def test_backup_rejects_active_and_existing_targets(tmp_path):
    service, _, work = fixture(tmp_path)
    with pytest.raises(ValueError, match="Stop API"):
        backup(work, Path(service.db_path), tmp_path / "backup")
    with pytest.raises(ValueError, match="outside"):
        backup(work, Path(service.db_path), work / "nested", services_stopped=True)
    from c2video.storage.jobs import RunQueue
    RunQueue(service.store).heartbeat("test-worker", 1)
    with pytest.raises(ValueError, match="heartbeat"):
        backup(work, Path(service.db_path), tmp_path / "backup", services_stopped=True)


def test_restore_detects_corruption_and_preserves_existing_target(tmp_path):
    service, run_id, work = fixture(tmp_path)
    output = backup(work, Path(service.db_path), tmp_path / "backup", services_stopped=True)
    with pytest.raises(ValueError, match="existing data"):
        restore(output, work)
    (output / "work" / "agent_runs" / run_id / "test.json").write_text("corrupted")
    with pytest.raises(ValueError, match="checksum"):
        restore(output, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


def test_backup_refuses_symlinks(tmp_path):
    service, _, work = fixture(tmp_path)
    (work / "external-link").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="Symlinks"):
        backup(work, Path(service.db_path), tmp_path / "backup", services_stopped=True)


def test_backup_survives_producing_process_exit_and_repeated_verify(tmp_path):
    import subprocess
    import sys

    service, _, work = fixture(tmp_path)
    target = tmp_path / "backup"
    subprocess.run(
        [sys.executable, "-m", "c2video.ops", "backup", "--work-dir", str(work),
         "--db", service.db_path, "--output", str(target), "--confirm-services-stopped"],
        check=True, capture_output=True, text=True,
    )
    before = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    verify_backup(target)
    verify_backup(target)
    after = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    assert before == after
    assert not (target / "work/c2video-agent.db-wal").exists()
    assert not (target / "work/c2video-agent.db-shm").exists()
    restore(target, tmp_path / "restored")
