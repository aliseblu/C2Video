"""Factory for candidate data sources."""
from c2video.config.schema import C2VideoConfig, SourceConfig
from c2video.source.base import CandidateSource
from c2video.source.public import PublicFeedSource
from c2video.source.zhihu import ZhihuSource


def create_source(config: C2VideoConfig | SourceConfig) -> CandidateSource:
    cfg = config if isinstance(config, SourceConfig) else config.source
    provider = cfg.provider.lower().strip()
    if provider == "zhihu":
        return ZhihuSource(mode=cfg.zhihu_mode, method=cfg.zhihu_method,
                           access_secret=cfg.zhihu_access_secret,
                           import_file=cfg.zhihu_import_file,
                           timeout=float(cfg.timeout_seconds))
    if provider in {"public", "public_feeds", "free"}:
        return PublicFeedSource(enabled_sources=cfg.public_sources, rss_feeds=cfg.rss_feeds,
                                hacker_news_api_base=cfg.hacker_news_api_base,
                                github_api_base=cfg.github_api_base,
                                timeout=float(cfg.timeout_seconds))
    raise ValueError(f"不支持的数据源：{cfg.provider}。请选择 zhihu 或 public。")

