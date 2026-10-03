"""C2Video CLI entry point.

Mounts pipeline subcommands under a single Typer app.
"""

from __future__ import annotations

import typer
from rich.console import Console

from c2video.application import ApplicationService
from c2video.cli import agent, card, curate, doctor, eval, fetch, render, run, script, zhihu
from c2video.config.loader import load_config

console = Console(stderr=True)

app = typer.Typer(
    name="c2video",
    pretty_exceptions_show_locals=False,
    help="Turn Zhihu content or public tech sources into vertical short videos.",
    context_settings={"help_option_names": ["-h", "--help"]},
)

_NO_CONFIG = {"agent", "zhihu", "doctor", "eval", "feedback", "replay", "studio"}


@app.callback()
def main(
    ctx: typer.Context,
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file [default: auto-detect ./c2video.toml; legacy config supported]",
    ),
) -> None:
    """C2Video — automated pipeline from source items to publish kit."""
    if ctx.invoked_subcommand in _NO_CONFIG:
        ctx.obj = None
        return
    try:
        ctx.obj = load_config(config)
    except FileNotFoundError:
        console.print("[red]Config file not found.[/red]")
        raise typer.Exit(code=1)
    except ValueError as exc:
        console.print(f"[red]Config validation failed:[/red] {exc}")
        raise typer.Exit(code=1)


app.add_typer(zhihu.app, name="zhihu", help="Import Zhihu materials and check source configuration")
app.add_typer(doctor.app, name="doctor", help="Check ffmpeg, Playwright, source, and config")
app.add_typer(fetch.app, name="fetch", help="Fetch candidates and apply hard filters")
app.add_typer(curate.app, name="curate", help="Score candidates and select picks")
app.add_typer(card.app, name="card", help="Render bilingual content cards")
app.add_typer(script.app, name="script", help="Generate Chinese narration script")
app.add_typer(render.app, name="render", help="Synthesize TTS audio and compose final MP4")
app.add_typer(run.app, name="run", help="Execute the full pipeline end-to-end")
app.add_typer(agent.app, name="agent", help="Execute a bounded Content Director plan")
app.add_typer(eval.app, name="eval", help="Run or compare the fixed evaluation suite")


@app.command("replay")
def replay(
    run_id: str,
    work_dir: str = typer.Option("work", help="Agent control-plane directory"),
) -> None:
    """Replay a durable Run trace without repeating side effects."""
    import json

    service = ApplicationService(work_dir=work_dir)
    try:
        typer.echo(json.dumps(service.runtime.replay(run_id), ensure_ascii=False, indent=2))
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command("feedback")
def feedback(
    run_id: str,
    comment: str = typer.Option(..., prompt=True, help="Concrete feedback for this Run"),
    category: str = typer.Option("preference", help="selection, script, visual, quality, preference, other"),
    rating: int | None = typer.Option(None, min=1, max=5),
    work_dir: str = typer.Option("work", help="Agent control-plane directory"),
) -> None:
    """Record feedback; AI extracts and automatically saves durable preferences."""
    try:
        config = load_config()
        service = ApplicationService(work_dir=work_dir, config=config)
        result = service.feedback(run_id, category=category, comment=comment, rating=rating)
    except (KeyError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    typer.echo(f"feedback={result['feedback_id']} memories={','.join(result['memory_ids']) or 'none'}")
    typer.echo(result["memory_processing"]["message"])


def _available_studio_port(host: str, preferred: int) -> int:
    """Bind the requested port, then fall back when Windows has reserved it."""
    import socket

    candidates = [preferred]
    for extra in (8877, 8788, 18765, 9765, preferred + 1, preferred + 2):
        if extra not in candidates and 1 <= extra <= 65535:
            candidates.append(extra)
    errors: list[str] = []
    for candidate in candidates:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((host, candidate))
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")
            continue
        finally:
            sock.close()
        return candidate
    detail = "; ".join(errors[:3]) or "no ports tried"
    raise RuntimeError(f"Could not bind Studio on {host} ({detail})")


@app.command("studio")
def studio(
    host: str = typer.Option("127.0.0.1", help="Bind address; local-only by default"),
    port: int = typer.Option(8765, min=1, max=65535),
    reload: bool = typer.Option(False, help="Enable development reload"),
) -> None:
    """Start the FastAPI control plane and packaged React Studio."""
    import uvicorn

    bound = _available_studio_port(host, port)
    if bound != port:
        console.print(
            f"[yellow]Port {port} is unavailable on this machine; Studio is using {bound}.[/yellow]"
        )
    console.print(f"Studio: http://{host}:{bound}")
    uvicorn.run("c2video.api.app:app", host=host, port=bound, reload=reload)



