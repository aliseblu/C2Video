# C2Video 包名统一与 PyCharm 启动验收

日期：2026-09-15。

## 已完成

- Python 包目录、导入路径、配置类、CLI、环境变量与应用配置目录统一为 `c2video` / `C2VIDEO_` / `C2VideoConfig`。
- 移除旧命令与旧前缀兼容分支，消除统一名称后产生的重复配置项。
- 前端构建目录改为 `c2video/api/static`，主题存储、脚本、测试、项目文档和包元数据同步更新。
- 三份任务数据库统一为 `c2video-agent.db`，迁移数据库内与运行产物中的本地路径、模块名称及生产者名称。
- 保留原有任务、视频、音频与截图；历史媒体内容未重新生成。
- 原登录文件安全复制到新的应用配置目录，原文件保留，未输出任何凭据内容。
- 虚拟环境启动脚本中的项目绝对路径已更新。由于本机可编辑安装的路径文件被赋予隐藏属性、Python 会跳过该文件，最终采用普通包安装，避免 CLI 依赖该导入钩子。PyCharm 的运行配置仍以项目根目录作为源码入口。
- 新增 `.run/C2Video Studio.run.xml`，使用 `.venv/bin/python` 运行模块 `c2video`，参数为 `studio --host 127.0.0.1 --port 8765`，配置了 Edge TTS 和完整版 FFmpeg 路径。

例外：真实上游 GitHub 地址仍保留原地址，未伪造不存在的远程仓库。第三方依赖、历史媒体内部画面和归档备份不作盲目替换。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 后端全量测试 | 79 通过，2 条第三方弃用提醒 |
| 前端测试 | 4 通过 |
| Ruff | 通过 |
| TypeScript / Vite 生产构建 | 通过，输出新包目录 |
| 依赖一致性 | `pip check` 通过 |
| CLI | `c2video --help` 正常 |
| 环境体检 | 公开源、本地规则、Edge TTS、Chromium、完整版 FFmpeg 均通过 |
| 健康接口 | 临时端口实测 HTTP 200，`ok=true`，全部检查为 true |
| 网页与 JavaScript | HTTP 200 |
| 原有视频访问 | 原 9 月 11 日实时任务的视频接口 HTTP 200 |
| 活跃源码旧命名扫描 | 代码、测试、配置、前端源码与 IDE 启动配置无旧项目名残留 |

后端全量复验使用以下环境：

```bash
PATH="/opt/homebrew/opt/ffmpeg-full/bin:$PATH" TTS_PROVIDER=edge .venv/bin/python -m pytest -q
```

过程中有一次未带上述 PATH 的局部测试失败，原因是系统默认 FFmpeg 缺少 `drawtext`，不是名称迁移导致；使用与 PyCharm 配置一致的完整版 FFmpeg 后，全量 79 项再次通过。没有据此声称所有输入均无缺陷，也没有重新执行付费服务或联网资讯成片。

## 历史数据完整性

以下数量在迁移前后保持一致；三份数据库均通过 SQLite `integrity_check`。

| 数据库 | Run | Task | Event | Artifact | Memory |
| --- | --- | --- | --- | --- | --- |
| `work/c2video-agent.db` | 9 | 63 | 110 | 102 | 0 |
| `work/free-final-qa/c2video-agent.db` | 1 | 5 | 12 | 15 | 0 |
| `work/free-final-qa-2/c2video-agent.db` | 1 | 5 | 12 | 15 | 0 |

## PyCharm 状态与剩余动作

项目已在 PyCharm 打开，运行配置已创建并通过配置回归测试。
macOS 拒绝自动化进程的辅助访问，因而未能代替用户点击 IDE 的运行按钮，不能声称已经由 PyCharm 启动。

用户在右上角选择 **C2Video Studio** 并点击绿色运行按钮后，访问 [本地网页](http://127.0.0.1:8765)。
临时 HTTP 验证使用 8877 端口，测试进程已正常关闭，未占用正式端口。

## 备份与回滚材料

本次开始前的备份目录：

`/private/tmp/c2video-rename.bZLHcq/`

- `project-before.tar.gz`：约 106 MB，包含改名前的项目与运行数据，不含第三方依赖目录。
- `databases/`：通过 SQLite backup API 保存的迁移前数据库快照。
- `generated/`、`environment/`、`bytecode/`：移出的旧构建产物、包元数据、入口与字节码缓存，可恢复。
- `rename-summary.json`：批量迁移记录。

备份含本机配置，应仅保存在可信本地位置，不上传公开仓库。临时目录不适合作为长期备份位置，如需长期保留，应另行保存。
