"""Local configuration status only; does not claim network or credential validity."""

from c2video.config.schema import C2VideoConfig
from c2video.source.zhihu import parse_materials, read_materials


def source_status(config: C2VideoConfig | None) -> dict:
    if config is None:
        return {"configured": False, "mode": "demo", "detail": "未加载配置，仅可运行演示"}
    cfg = config.source
    provider = cfg.provider.lower().strip()
    if provider in {"public", "public_feeds", "free"}:
        return {"configured": True, "mode": "public", "detail": "RSS · Hacker News · GitHub"}
    if provider != "zhihu":
        return {"configured": False, "mode": "unknown", "detail": "请选择知乎或公开数据源"}
    if cfg.zhihu_mode == "import":
        try:
            count = len(parse_materials(read_materials(cfg.zhihu_import_file)))
        except (ValueError, OSError) as exc:
            return {"configured": False, "mode": "import", "detail": str(exc)}
        return {"configured": True, "mode": "import", "detail": f"知乎素材导入 · {count} 条"}
    configured = bool(cfg.zhihu_access_secret.strip())
    return {"configured": configured, "mode": "api",
            "detail": ("知乎官方接口 · 密钥已配置，联网有效性待验证" if configured else
                       "知乎官方接口 · 请配置 Access Secret，或在新建页面导入素材")}
