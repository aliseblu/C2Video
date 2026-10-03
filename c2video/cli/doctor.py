"""Environment / dependency checks."""

from __future__ import annotations

import typer

from c2video.config.loader import load_config
from c2video.media_tools import media_tools_status
from c2video.source.status import source_status
from c2video.util import discover_browser_executable

app = typer.Typer(help="Check local setup (source, ffmpeg, Playwright, config)")


def _ok(label: str, detail: str) -> None:
    typer.secho(f"  OK   {label}: {detail}", fg=typer.colors.GREEN)


def _bad(label: str, detail: str) -> None:
    typer.secho(f"  FAIL {label}: {detail}", fg=typer.colors.RED)


@app.callback(invoke_without_command=True)
def doctor(
    ctx: typer.Context,
) -> None:
    """Print a setup checklist. Exit 1 if anything required is missing."""
    failed = False
    typer.echo("c2video doctor")

    try:
        cfg = load_config()
        _ok("config", f"source={cfg.source.provider} tts={cfg.tts.provider} llm={cfg.llm.provider}")
    except Exception as exc:
        _bad("config", str(exc))
        failed = True
        cfg = None

    if cfg is not None:
        status = source_status(cfg)
        if status["configured"]:
            _ok("source", status["detail"])
        else:
            _bad("source", status["detail"])
            failed = True

    media = media_tools_status()
    if media["ok"]:
        _ok("ffmpeg", media["detail"])
    else:
        _bad("ffmpeg", media["detail"])
        failed = True

    exe = discover_browser_executable()
    if exe is not None:
        _ok("playwright", str(exe))
    else:
        _bad("playwright", "chromium missing — run `playwright install chromium`")
        failed = True

    try:
        import edge_tts  # noqa: F401

        _ok("tts", "edge-tts import ok")
    except Exception as exc:
        _bad("tts", str(exc))
        failed = True

    if failed:
        raise typer.Exit(code=1)
    typer.secho("All checks passed.", fg=typer.colors.GREEN)



