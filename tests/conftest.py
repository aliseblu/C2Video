"""Shared fixtures for C2Video tests.

Add fixtures here as the pipeline grows. For now this file exists
so the tests/ directory is wired into the project structure.

To add a fixture that loads a sample config:

    import pytest
    from c2video.config.loader import load_config

    @pytest.fixture
    def sample_config(tmp_path):
        config_file = tmp_path / "test.toml"
        config_file.write_text('[curation]\ntop_n = 3\n')
        return load_config(str(config_file))
"""

import os
import tempfile
from pathlib import Path

# Set before test collection imports the global API app. Never open the user's
# real database or load the root .env/c2video.toml when running plain pytest.
_TEST_RUNTIME = tempfile.TemporaryDirectory(prefix="c2video-tests-")
for _key, _value in {
    "PYTHON_DOTENV_DISABLED": "1",
    "C2VIDEO_CONFIG": str(Path(__file__).parent / "fixtures" / "settings.toml"),
    "C2VIDEO_WORK_DIR": str(Path(_TEST_RUNTIME.name) / "work"),
    "C2VIDEO_FINAL_DIR": str(Path(_TEST_RUNTIME.name) / "final"),
    "C2VIDEO_ENV": "development",
    "C2VIDEO_STUDIO_TOKEN": "",
    "C2VIDEO_VIEWER_TOKEN": "",
    "C2VIDEO_ALLOWED_HOSTS": "127.0.0.1,localhost,::1,testserver",
    "C2VIDEO_ALLOWED_ORIGINS": "",
    "C2VIDEO_WORKER_MODE": "off",
    "C2VIDEO_WORKER_CONCURRENCY": "1",
    "C2VIDEO_MAX_QUEUED_RUNS": "100",
    "C2VIDEO_MIN_FREE_DISK_MB": "128",
    "C2VIDEO_LLM_PROVIDER": "local",
    "C2VIDEO_LLM_API_KEY": "",
    "C2VIDEO_SOURCE_ZHIHU_ACCESS_SECRET": "",
    "TTS_PROVIDER": "edge",
}.items():
    os.environ[_key] = _value
