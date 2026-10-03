"""CLI for Zhihu material imports and configuration status."""

import json
from pathlib import Path

import typer

from c2video.config.loader import load_config
from c2video.source.status import source_status
from c2video.source.zhihu import parse_materials, read_materials

app = typer.Typer(help="知乎素材导入与配置检查；不会索取会员或浏览器 Cookie")


@app.command("status")
def status() -> None:
    """Check local settings without displaying secrets or calling paid APIs."""
    typer.echo(source_status(load_config())["detail"])


@app.command("import")
def import_file(
    path: Path = typer.Argument(..., help="有权使用的知乎素材 JSON 文件"),
    output: Path | None = typer.Option(None, help="保存位置，默认使用配置中的素材文件"),
    replace: bool = typer.Option(False, help="明确允许替换已有导入文件"),
) -> None:
    """Validate and save materials; does not generate or publish a video."""
    try:
        payload = read_materials(path)
        candidates = parse_materials(payload)
        destination = output or Path(load_config().source.zhihu_import_file).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w" if replace else "x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except (ValueError, OSError) as exc:
        typer.echo(f"导入失败：{exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"已导入 {len(candidates)} 条知乎素材：{destination}")
    typer.echo('运行前设置 [source].zhihu_mode = "import"；或使用 agent run --materials 原文件。')
