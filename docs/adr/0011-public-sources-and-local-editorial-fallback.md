# 默认采用公开数据源与本地编辑规则

没有 X Premium+、SuperGrok 或模型 API Key 的用户也需要运行实时完整流程。默认数据源改为公开 RSS、Hacker News 和 GitHub Repository Search；Curation 与 Script 增加确定性的本地规则模式。

## Decision

1. `source.provider = "public"` 聚合官方 RSS、Hacker News Top Stories 和 GitHub Repository Search，并继续映射到兼容模型 `CandidateTweet`。
2. 单个公开来源失败时记录错误并继续使用其余来源；结果按 URL 去重。
3. `llm.provider = "local"` 使用可解释的来源权重、互动数据和主题匹配完成排序，并生成来源感知的事实型口播。
4. 公共来源卡片显示各自真实指标，例如 GitHub Stars/Forks、Hacker News Points/Comments，不伪装成 X 点赞和转发。
5. Grok OAuth 和 X 官方 MCP 继续作为可替换的数据源选项，不影响原有用户。

## Consequences

- 新用户无需会员或 API Key 即可运行实时流程，只有 Edge TTS 与公开来源访问需要网络。
- 本地模式不会真正翻译或改写英文标题，其输出是结构化摘要；需要更自然的中文时仍可切换到兼容模型 API。
- GitHub 结果是“近期活跃、按 Star 排序的仓库搜索”，不是对 GitHub Trending 页面进行抓取。
- 免费公开接口存在频率限制，项目不保证每个来源每次都可用。
