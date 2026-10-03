"""Configuration data models.

All field names and docstrings use CONTEXT.md glossary terms.
No vendor-specific names in config keys, values, or docstrings.
"""

from typing import Literal

from pydantic import AliasChoices, BaseModel, Field


class HardFilterConfig(BaseModel):
    """Hard filter thresholds — rule-based, no semantic judgment.

    Applied at the fetch stage to narrow candidates by time window
    and engagement metrics.
    """

    time_window_hours: int = 24
    min_likes: int = 0
    min_shares: int = Field(0, validation_alias=AliasChoices("min_shares", "min_retweets"))
    min_replies: int = 0
    views_threshold: int = 0
    # Drop anything older than this, even if the source returned it.
    max_age_hours: int = 168


class CurationConfig(BaseModel):
    """Curation / LLM scoring parameters.

    Controls the second-stage filtering where an LLM scores candidates
    on their suitability for a Chinese short video.
    """

    top_n: int = 6
    blocking_mode: bool = True  # True = Gate 1 waits for user; False = direct
    max_candidates: int = 50


class LLMConfig(BaseModel):
    """LLM provider configuration (neutral naming)."""

    provider: str = "local"  # "api" or deterministic, account-free "local"
    api_base_url: str = ""
    api_key: str = ""  # overridden from env, never committed
    # NOTE: Validation (non-empty when provider="api") belongs at the
    # call site (curate.py, script.py), not here. Subcommands that don't
    # call LLM (fetch, card) must not be blocked by a missing key.
    model: str = ""
    timeout_seconds: int = 60
    max_tokens: int = 4096
    temperature: float = 0.7
    input_usd_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_usd_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    # Internal attribution; not exported in configuration snapshots.
    telemetry_db_path: str | None = Field(default=None, exclude=True, repr=False)
    telemetry_run_id: str | None = Field(default=None, exclude=True, repr=False)
    telemetry_purpose: str = Field(default="other", exclude=True, repr=False)


class MemoryConfig(BaseModel):
    """Automatic AI extraction; never falls back to raw or rule-based writes."""

    enabled: bool = True
    min_confidence: float = Field(default=0.8, ge=0, le=1, allow_inf_nan=False)
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class TTSConfig(BaseModel):
    """TTS provider configuration. Defaults to Edge free TTS.

    API fields are only read when provider == "api".
    """

    provider: str = "edge"  # "edge" or "api"
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: str = "+12%"
    volume: str = "+0%"
    pitch: str = "+0Hz"
    # API mode fields (only used when provider == "api")
    api_base_url: str = ""
    api_key: str = ""  # overridden from env
    # NOTE: Validation (non-empty when provider="api") belongs at the
    # call site (render.py), not here. Subcommands that don't call TTS
    # (fetch, card, script) must not be blocked by a missing key.
    api_model: str = ""
    api_voice: str = ""
    api_format: str = "mp3"
    api_timeout_seconds: int = 60


class SourceConfig(BaseModel):
    """Zhihu official APIs or imported materials; public feeds remain optional."""
    provider: str = "zhihu"
    zhihu_mode: Literal["api", "import"] = "api"
    zhihu_method: Literal["search", "hot_list"] = "search"
    zhihu_access_secret: str = Field("", repr=False, exclude=True)
    zhihu_import_file: str = "work/zhihu-materials.json"
    timeout_seconds: int = 30
    public_sources: list[str] = Field(
        default_factory=lambda: ["rss", "hacker_news", "github"]
    )
    rss_feeds: list[str] = Field(
        default_factory=lambda: [
            "https://openai.com/news/rss.xml",
            "https://blog.google/technology/ai/rss/",
        ]
    )
    hacker_news_api_base: str = "https://hacker-news.firebaseio.com/v0"
    github_api_base: str = "https://api.github.com"


class C2VideoConfig(BaseModel):
    """Root configuration — single source of truth for all tunable parameters."""

    domain_keywords: list[str] = [
        "AI",
        "artificial intelligence",
        "machine learning",
        "LLM",
    ]
    hard_filter: HardFilterConfig = HardFilterConfig()
    curation: CurationConfig = CurationConfig()
    llm: LLMConfig = LLMConfig()
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    tts: TTSConfig = TTSConfig()
    source: SourceConfig = SourceConfig()
    work_dir: str = "work"
    final_dir: str = "final"
    # Optional bed music. Empty = look for assets/bgm.mp3, skip if missing.
    bgm_path: str = ""



