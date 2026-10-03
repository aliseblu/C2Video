# C2Video 知乎替换验证记录

日期：2026-09-15

## 实现

- 默认数据源：知乎官方搜索；可选官方热榜和每次任务独立的 JSON 素材导入。
- 按知乎官方字段实现 Bearer + 秒级时间戳鉴权、条数限制、原文溯源及明确错误处理。使用 Context7 的 HTTPX 文档核对请求、重定向和 MockTransport 测试方式。
- X/Grok 适配器和登录代码从可运行项目移除；模型调用不再默认使用订阅凭证。
- CandidateItem、shares、favorites 等中性字段；旧 JSON 指标保留读取别名，不更改真实历史来源。
- 网页增加素材文件选择、无密钥提示及真实/演示模式边界。保留原作者、原文 URL，未知日期不默认填成当天。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| Python 完整回归 | 94 passed，2 项第三方弃用警告，18.76 秒 |
| 网页测试 | 7 passed，包括未配置密钥和上传素材请求 |
| 网页生产构建 | 通过，产物已写入打包静态目录 |
| Ruff | 通过 |
| pip check | 无依赖冲突 |
| 本地包重新安装 | 已完成，不升级第三方依赖；CLI 帮助已显示 zhihu，不再提供旧登录入口 |
| 浏览器检查 | 桌面设置页、桌面/390px 手机导入页通过；无页面异常或横向溢出；手机截图已目视检查 |
| 实际导入合成 | COMPLETE；1080×1920 H.264 + AAC，23.042643 秒 |
| 真实知乎抓取 | 未执行：当前验证进程未配置用户 Access Secret，不能宣称在线 API 已通过 |

实际合成使用明确标注为“自编测试”的文字，URL 仅作离线格式测试，不是真实知乎内容证据。

测试视频：`/private/tmp/c2video-zhihu-smoke-_0_1_yed/work/agent_runs/run_872bb80b2d834e3fb1d27bb7103336de/publish_kit/video.mp4`。
界面截图：`/private/tmp/c2video-zhihu.lRcFXi/settings-desktop.png`、`import-desktop.png`、`import-mobile.png`。

## 数据保护与待办

修改前源码备份、退役适配器及旧构建保存在 `/private/tmp/c2video-zhihu.lRcFXi/`。这是系统临时目录，可能被清理，不是永久备份。已有正式任务数据库、视频与用户目录中的旧凭证不修改。

临时网页服务 8891 已关闭。正式项目需在 PyCharm 的 C2Video Studio 环境变量填写 `C2VIDEO_SOURCE_ZHIHU_ACCESS_SECRET`，保留完整版 FFmpeg 的 PATH，停止旧服务后重新启动，再进行真实抓取验证。密钥不要放进聊天、截图、源码或素材文件。
