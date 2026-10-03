# FFmpeg 自动选择修复验证

日期：2026-09-15

根因：PyCharm 的进程环境仍优先选择 `/opt/homebrew/bin/ffmpeg`，它指向普通版，缺少 subtitles/drawtext；旧检查只判断可执行文件存在。

修复：新增统一的媒体工具解析器。检测默认 FFmpeg 的滤镜，必要时选择本机已安装的 Homebrew ffmpeg-full，所有视频合成、配音转码、质检、Demo 和评测都使用验证后的绝对路径。支持显式 C2VIDEO_FFMPEG / C2VIDEO_FFPROBE 覆盖，错误覆盖不会被忽略。检测有超时和基于文件指纹的缓存。未更改系统 PATH、系统链接、账户凭证或既有任务数据。

网页设置与 doctor 显示实际路径和滤镜检测结果，不再把“找到普通版”视为通过。

验证环境故意不将 ffmpeg-full 加入 PATH，未设置显式工具覆盖：

- PATH 默认 `/opt/homebrew/bin/ffmpeg`；项目实际选择 `/opt/homebrew/Cellar/ffmpeg-full/9.0.1_1/bin/ffmpeg` 和配套 ffprobe。
- 使用失败任务 `run_8105799809974d30b0ea642e88b034f3` 的原图片、音频和 ASS 字幕，成功合成 2.366667 秒片头。没有重新抓取或调用 TTS，也没有修改原任务状态。
- 测试输出：`/private/tmp/c2video-ffmpeg-regression-q4nrkv85/opener.mp4`。
- Python 全量：106 passed，2 项第三方弃用警告，16.98 秒。
- 网页：7 passed；生产构建通过；Ruff 通过。
- 项目虚拟环境已重新安装当前包，不升级依赖。安装后的模块在项目目录之外也能自动选中完整版。

修改前源码备份位于 `/private/tmp/c2video-ffmpeg-fix.WmLoF6/before.tar.gz`（系统临时目录，并非永久存档）。

剩余用户操作：停止 PyCharm 中运行的旧服务，再运行 C2Video Studio。刷新设置页应看到 ffmpeg-full 路径，然后重试失败任务。旧服务不会自动热更新。
