"""Offline backup/restore for one local workspace. Never overwrite existing targets."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from c2video.io_atomic import atomic_write_text


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def readonly(path: Path, *, immutable: bool = False):
    suffix = "?mode=ro&immutable=1" if immutable else "?mode=ro"
    return sqlite3.connect(path.resolve().as_uri() + suffix, uri=True)


def backup(work_dir: Path, db_path: Path, destination: Path, *, services_stopped=False) -> Path:
    if not services_stopped:
        raise ValueError("Stop API, Worker and CLI writers, then confirm services_stopped")
    work, db, target = work_dir.resolve(), db_path.resolve(), destination.absolute()
    if not work.is_dir() or not db.is_file() or work == Path("/") or (work / "pyproject.toml").exists():
        raise ValueError("A specific workspace data directory and existing database are required")
    if target.exists() or target.is_symlink() or target.resolve().is_relative_to(work):
        raise ValueError("Backup destination must be new and outside the workspace")
    with closing(readonly(db)) as source:
        tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "runs" not in tables:
            raise ValueError("Not a C2Video database")
        if "worker_status" in tables and source.execute(
            "SELECT 1 FROM worker_status WHERE heartbeat_at>? LIMIT 1", (time.time() - 10,)
        ).fetchone():
            raise ValueError("Worker heartbeat is active; stop services before backup")
        if "run_jobs" in tables and source.execute(
            "SELECT 1 FROM run_jobs WHERE status='running' LIMIT 1"
        ).fetchone():
            raise ValueError("Unsettled running jobs; perform recovery before offline backup")
        paths = []
        excluded = {db, Path(str(db) + "-wal"), Path(str(db) + "-shm")}
        for path in work.rglob("*"):
            if path.is_symlink():
                raise ValueError("Symlinks are not accepted in workspace backups")
            if path.is_file() and path not in excluded and ".execution-locks" not in path.parts:
                paths.append(path)
        target.mkdir(parents=True, exist_ok=False, mode=0o700)
        payload = target / "work"
        payload.mkdir(mode=0o700)
        copied_db = payload / "c2video-agent.db"
        with closing(sqlite3.connect(copied_db)) as output:
            source.backup(output)
            # Publish a standalone snapshot, not sidecars removed on process exit.
            # Close the connection before hashing the manifest.
            output.execute("PRAGMA journal_mode=DELETE")
            if output.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Database backup integrity check failed")
        copied_db.chmod(0o600)
        for path in paths:
            relative = path.relative_to(work)
            if relative == Path("c2video-agent.db"):
                # Custom --db plus another default DB is ambiguous; don't overwrite.
                raise ValueError("Workspace contains a second database; use separate workspaces")
            copied = payload / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, copied)
    manifest = {"version": 1, "source_work_dir": str(work),
                "created_at": time.time(),
                "files": {p.relative_to(payload).as_posix(): digest(p)
                          for p in payload.rglob("*") if p.is_file()}}
    atomic_write_text(target / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return target


def verify_backup(source: Path) -> dict:
    root = source.resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    payload = root / "work"
    if manifest.get("version") != 1 or not isinstance(manifest.get("files"), dict):
        raise ValueError("Unsupported backup manifest")
    if not isinstance(manifest.get("source_work_dir"), str):
        raise ValueError("Missing source workspace")
    paths = list(payload.rglob("*"))
    if payload.is_symlink() or any(p.is_symlink() for p in paths):
        raise ValueError("Backup symlinks are not accepted")
    actual = {p.relative_to(payload).as_posix() for p in paths if p.is_file()}
    if actual != set(manifest["files"]) or "c2video-agent.db" not in actual:
        raise ValueError("Backup file list mismatch")
    for relative, expected in manifest["files"].items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or digest(payload / path) != expected:
            raise ValueError("Backup checksum or path validation failed")
    with closing(readonly(payload / "c2video-agent.db", immutable=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup database integrity check failed")
    return manifest


def restore(source: Path, destination: Path) -> Path:
    target = destination.absolute()
    if target.exists() or target.is_symlink() or target.resolve().is_relative_to(source.resolve()):
        raise ValueError("Restore destination must be new; existing data is never overwritten")
    manifest = verify_backup(source)
    shutil.copytree(source / "work", target)
    db_path = target / "c2video-agent.db"
    with closing(sqlite3.connect(db_path)) as db, db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("platform_sessions", "auth_attempts", "worker_status"):
            if table in tables:
                db.execute(f"DELETE FROM {table}")
        if "run_jobs" in tables:
            db.execute("UPDATE run_jobs SET status='idle',claim_token=NULL,worker_pid=NULL")
        db.execute("""UPDATE runs SET is_paused=1 WHERE state NOT IN
                      ('COMPLETE','FAILED','CANCELED','WAIT_GATE_1','WAIT_GATE_2')""")
        old_root = Path(manifest["source_work_dir"])
        # Relocate explicit Artifact paths only, never rewrite evidence or user prose.
        for artifact_id, path, raw in db.execute("SELECT artifact_id,path,payload_json FROM artifacts").fetchall():
            original = Path(path)
            if original.is_absolute() and original.is_relative_to(old_root):
                updated = str(target / original.relative_to(old_root))
                payload = json.loads(raw)
                payload["path"] = updated
                db.execute("UPDATE artifacts SET path=?,payload_json=? WHERE artifact_id=?",
                           (updated, json.dumps(payload, ensure_ascii=False), artifact_id))
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    save = commands.add_parser("backup")
    save.add_argument("--work-dir", type=Path, required=True)
    save.add_argument("--db", type=Path, required=True)
    save.add_argument("--output", type=Path, required=True)
    save.add_argument("--confirm-services-stopped", action="store_true")
    check = commands.add_parser("verify")
    check.add_argument("--backup", type=Path, required=True)
    recover = commands.add_parser("restore")
    recover.add_argument("--backup", type=Path, required=True)
    recover.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "backup":
        print(backup(args.work_dir, args.db, args.output,
                     services_stopped=args.confirm_services_stopped))
    elif args.action == "verify":
        manifest = verify_backup(args.backup)
        print(f"Verified {len(manifest['files'])} files and SQLite integrity")
    else:
        print(restore(args.backup, args.output))
        print("Sessions revoked; active runs paused; queue not restarted. Review before resuming.")


if __name__ == "__main__":
    main()
