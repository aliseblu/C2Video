"""Regression checks for the unified package, storage, and IDE entrypoints."""

from importlib.metadata import distribution
from pathlib import Path
from xml.etree import ElementTree

import c2video
from c2video.application import ApplicationService
from c2video.config.schema import C2VideoConfig


def test_package_and_console_entrypoint_agree() -> None:
    assert Path(c2video.__file__).parent.name == "c2video"
    entrypoints = {
        entry.name: entry.value
        for entry in distribution("c2video").entry_points
        if entry.group == "console_scripts"
    }
    assert entrypoints == {"c2video": "c2video.cli.main:app",
                           "c2video-mcp": "c2video.mcp_server.__main__:main"}
    assert C2VideoConfig.__name__ == "C2VideoConfig"


def test_default_run_database_uses_current_name(tmp_path: Path) -> None:
    service = ApplicationService(work_dir=str(tmp_path))
    assert Path(service.db_path) == tmp_path / "c2video-agent.db"
    created = service.create_run(query="AI", mode="demo")
    assert service.get_run(created["run"]["run_id"]) is not None


def test_default_source_is_zhihu() -> None:
    assert C2VideoConfig().source.provider == "zhihu"


def test_pycharm_launches_current_module_from_project_root() -> None:
    root = Path(__file__).resolve().parents[1]
    document = ElementTree.parse(root / "deploy" / "pycharm" / "C2Video Studio.run.xml")
    configuration = document.getroot().find("configuration")
    assert configuration is not None
    options = {item.get("name"): item.get("value") for item in configuration.findall("option")}
    assert configuration.get("name") == "C2Video Studio"
    assert options["SCRIPT_NAME"] == "c2video"
    assert options["MODULE_MODE"] == "true"
    assert options["WORKING_DIRECTORY"] == "$PROJECT_DIR$"
    assert options["SDK_HOME"] == "$PROJECT_DIR$/.venv/bin/python"
