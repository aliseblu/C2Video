"""Commands for the bounded Content Director runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from c2video.application import ApplicationService
from c2video.config.loader import load_config
from c2video.source.zhihu import read_materials

app = typer.Typer(help="Create and execute bounded Agent Studio runs")


@app.command("run")
def run_agent(
    goal: str = typer.Option(..., "--goal", help="Natural-language content goal"),
    materials: Path | None = typer.Option(None, help="知乎素材 JSON；本次任务独立导入，无需密钥"),
    autonomy: str = typer.Option("assisted", help="supervised, assisted, or auto"),
    duration: int = typer.Option(60, min=15, max=600, help="Target duration in seconds"),
    wait: bool = typer.Option(True, "--wait/--background", help="Wait for the current run state"),
    work_dir: str = typer.Option(None, help="Agent control-plane directory"),
    demo: bool = typer.Option(False, "--demo", help="Force offline Demo fixtures"),
    live: bool = typer.Option(
        False,
        "--live",
        help="Force the configured real-time source + production pipeline",
    ),
) -> None:
    """Create a Goal and run it with the configured Zhihu or public source."""
    if autonomy not in {"supervised", "assisted", "auto"}:
        raise typer.BadParameter("must be supervised, assisted, or auto", param_hint="autonomy")
    if demo and live:
        raise typer.BadParameter("use either --demo or --live", param_hint="live")
    try:
        config = load_config()
    except Exception:
        config = None
    service = ApplicationService(
        work_dir=work_dir or os.environ.get("C2VIDEO_WORK_DIR", "work"),
        config=config,
    )
    mode = "demo" if demo else "live" if live else None
    snapshot = service.create_run(
        query=goal,
        autonomy=autonomy,
        target_duration_seconds=duration,
        mode=mode,
        materials=read_materials(materials) if materials else None,
    )
    run_id = snapshot["run"]["run_id"]
    if wait:
        snapshot = service.execute_sync(run_id)
        typer.echo(json.dumps({"run_id": run_id, "state": snapshot["run"]["state"]}, ensure_ascii=False))
    else:
        pid = service.start_worker(run_id)
        typer.echo(json.dumps({"run_id": run_id, "worker_pid": pid}, ensure_ascii=False))

