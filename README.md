# C2Video

输入主题，或导入有权使用的知乎素材，完成选题 → 中文口播 → 配音 → 字幕 → 1080×1920 视频和发布素材。

## 工程发布版 0.4

已提供双服务 Docker Compose、非 root 和资源限制、持久队列、访问保护、媒体质检、监控指标、备份恢复及 GitHub Actions 发布检查。适用于个人／受信小团队的单机共享工作空间，不宣称多租户或分布式高可用。

- [容器部署与本机体验](docs/container-deployment.md)
- [安全边界](SECURITY.md)与[项目来源](NOTICE.md)
- [本次验收结果](docs/verification/release-0.4.md)

当前仅具备部署交付能力，没有部署到公网；正式上线仍需目标机器、HTTPS 和外部服务小样本验收。

## 单机平台升级

已加入持久执行队列、独立 Worker、取消／超时保护、操作员与只读登录、真实审核节点，以及“平台运行”页面。默认面向个人／受信小团队，不是多租户 SaaS。

先看 [生产运行手册](docs/production-runbook.md) 与 [本机验收记录](docs/verification/platform-2026-09-15.md)。本轮没有停止旧服务或开放公网；新版本需在确认旧任务状态、备份后手动切换。历史任务保留旧计划，新建实时任务才启用新门禁。

## AgentChat / MCP 接入

已提供独立 MCP 网关，支持本机 stdio 和带独立访问密钥的 Streamable HTTP。AgentChat 可查询配置、创建／启动后台视频任务、查看进度与审核资料、获取发布包、提交 AI 反馈、管理记忆和查询 Token 用量；默认保留人工审核，不暴露自动通过 Gate 的工具。

本机接入见 [MCP 手册](docs/mcp-agentchat.md)，可直接导入 [AgentChat 本机配置](examples/agentchat-c2video.local.json)。MCP 通过现有 Studio API 复用持久队列，不创建另一套数据库或 Worker；Studio 仍需正常运行。

## 数据来源

默认使用知乎官方搜索接口，需在本机配置 Access Secret。官方热榜和本地素材导入也已接入；无需订阅会员，但官方接口的权限、可用额度和计费以[知乎开放平台](https://developer.zhihu.com/)为准。程序不会绕过登录、验证码或付费内容限制。

- 官方搜索：`source.provider = "zhihu"`、`zhihu_mode = "api"`、`zhihu_method = "search"`。
- 官方热榜：改 `zhihu_method = "hot_list"`。热榜没有提供的作者、时间和互动数据不会编造。
- 本地导入：网页新建页选择素材 JSON，或 CLI 使用 `agent run --materials 文件.json`；不会调用知乎 API。
- 公开聚合：仍可显式选择 `source.provider = "public"`，使用 RSS、Hacker News、GitHub；不冒充知乎。

没有密钥或素材时会提示配置，不会把演示数据当作在线结果。
默认 `llm.provider = "local"`，只做规则筛选和摘录，不调用大模型。知乎 Access Secret 与模型 API Key 不通用。

## 本机启动（PyCharm 终端）

```bash
cd /path/to/C2Video
.venv/bin/python -m c2video studio --host 127.0.0.1 --port 8765
```

浏览器访问终端显示的地址。不要重复启动已经运行的服务。

仓库提供不含密钥的模板 `deploy/pycharm/C2Video Studio.run.xml`，可复制到自己的 `.run` 目录；个人 `.run` 不上传。

PyCharm 的 **C2Video Studio** 运行配置应使用 `.venv/bin/python`、模块 `c2video`、项目根目录为工作目录。环境变量中添加 `C2VIDEO_SOURCE_ZHIHU_ACCESS_SECRET`，值为自己的 Access Secret。不要发到聊天、截图或提交到仓库。保存后停止旧服务并重新运行。

## 素材格式

文件是 UTF-8 JSON 数组（最多 100 条、1 MB）。每条包含：

- `title`：原标题，必填。
- `content`：有权使用的内容或摘要；没有正文时仅处理标题，不推断作者观点。
- `url`：知乎问题、回答或专栏文章的真实 HTTPS 原文链接，必填。
- `author`、`created_at`、`voteup_count`、`comment_count`、`favorite_count`：可选；未知就省略。

复制 [素材模板](examples/zhihu-materials.example.json)，替换占位内容和链接后再导入。每次任务会保存独立的素材快照，之后修改原文件不会影响已创建的任务。导入是人工选材，不按发布时间丢弃旧内容，也不把导入日期写成原文发布日期。

## CLI

```bash
.venv/bin/python -m c2video --help
.venv/bin/python -m c2video zhihu status
.venv/bin/python -m c2video agent run --goal "知乎 AI 话题精选" --live --autonomy auto
.venv/bin/python -m c2video agent run --goal "知乎内容摘录" --materials /绝对路径/素材.json --autonomy auto
```

CLI 临时导入和网页导入都可以在没有知乎密钥时使用。普通分阶段流水线需要先导入：
```bash
.venv/bin/python -m c2video zhihu import /绝对路径/素材.json
C2VIDEO_SOURCE_ZHIHU_MODE=import .venv/bin/python -m c2video run --auto
```

已有导入文件不会默认覆盖；明确替换时给 `zhihu import` 加 `--replace`。

## 自动记忆与用量看板

任务详情底部新增“反馈与自动记忆”：反馈先由已配置的 API 模型提炼，校验、去重后自动保存长期偏好；AI 不可用时只记录反馈，不直接把原文当成记忆。可在“记忆”页面查看依据、停用或重新启用。已启用且经 AI 处理的选题／写稿偏好会进入新任务 API 模型输入，规则模式不使用偏好；不保证模型总能正确遵从。

左侧“用量看板”展示 Token、请求次数、失败与耗时、模型/用途拆分、费用参考和任务/记忆概况。供应商未返回的用量显示未知，不按历史视频补造数字。修改后需要重启 Studio 并刷新页面。

详见 [自动记忆说明](docs/automatic-memory.md)、[用量统计口径](docs/usage-dashboard.md)。

## 安装及验证

需要 Python 3.11+、Chromium、支持 subtitles/drawtext 的 FFmpeg。程序自动检测 PATH 中的版本；不支持时自动选择已安装的 Homebrew ffmpeg-full，不需要在 PyCharm 手动维护 PATH。也可通过 C2VIDEO_FFMPEG / C2VIDEO_FFPROBE 显式指定二进制路径；无效显式配置会报错而不会忽略。macOS 可使用 Homebrew 的 `ffmpeg-full`。先安装 Node 24 并运行 `npm --prefix studio ci`、`npm --prefix studio run build` 构建网页，再在虚拟环境中安装 `pip install -c constraints-mcp.txt ".[dev,mcp]"`、`playwright install chromium`；使用当前项目源代码开发后，必要时重新安装本地包。

`doctor` 检查本机配置与依赖，密钥已配置不等于已联网验证。没有真实 Access Secret 的测试只能证明解析、错误处理和离线流程，不能证明知乎在线接口成功。

## 来源与历史

代码改编自[原始上游](https://github.com/asashiki/X2Video)。当前已移除旧平台的登录与数据适配器，不读取原有登录凭证。原始归属、历史技术记录及已有视频、数据库不伪造改写；旧记录中的来源链接仍指向真实的历史来源。

详细接入约束见 [知乎接入说明](docs/zhihu-source.md)。
