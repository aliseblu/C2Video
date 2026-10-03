# SILENCE 质检修复与验证

验证日期：2026-09-18。项目：C2Video。

## 根因与修复

两次失败任务都有有效音轨，平均音量为 -19.8 dB。原规则把 FFmpeg 的每一条 silence_start 观测都当作致命错误：超过 0.5 秒的正常句间停顿也会阻断发布前质检。

修复保留原始静音观测，单独计算配对后的起止区间、最长持续时间、总时长和占比。连续静音达到 1.5 秒，或检测到的静音总占比达到 35%，才判为 SILENCE。探测灵敏度仍为 -50 dB、0.5 秒。这是本项目口播场景的启发式规则，不是通用媒体行业标准。

规则在演示与真实流水线的质检中共用。异常报告提供发生时间、持续时长和判定依据；解析失败不会被当成通过。无音轨、黑帧等原检查保留。此前同一任务的误报，仅在重试并实际复检通过后标记解决。

FFmpeg 官方说明中，silencedetect 输出的是满足音量和持续时间条件的观测区间，并非应用层质量结论：[silencedetect 文档](https://ffmpeg.org/ffmpeg-filters.html#silencedetect)。

## 验证结果

- 专项测试：44 项通过，包括新增的 22 项静音策略与真实媒体测试。
- 真实生成测试素材：0.7 秒正常停顿通过；2 秒断音、整段无声、高占比短断音、无音轨继续阻断。
- 全量后端测试：356 通过、1 跳过、1 失败。失败为 tests/test_c2video_identity.py::test_package_and_console_entrypoint_agree：pyproject.toml 已新增 c2video-mcp 启动入口，但旧断言只接受 c2video。与本次静音改动无关，未修改该测试或移除入口。
- 本次改动文件 Ruff 检查通过。
- 已使用 --no-deps --no-build-isolation 重新安装到项目现有虚拟环境，未升级依赖。

对原失败任务的视频、封面和脚本创建隔离副本，执行完整 LiveQualityTool 复检：

| 原任务 | 视频时长 | 停顿数 | 最长停顿 | 静音占比 | 复检 |
| --- | ---: | ---: | ---: | ---: | --- |
| run_3de09de285ba42c8b3b4e82428338170 | 66.624 秒 | 9 | 0.709583 秒 | 9.2684% | 通过 |
| run_a9084eff48f54f7889e5e7178a113104 | 55.632 秒 | 9 | 0.684834 秒 | 10.5411% | 通过 |

复检报告（临时目录可能被系统清理）：

- /private/tmp/c2video-silence-recheck-08futjz6/agent_runs/run_bb764509802b45d28dce44e76c7452f5/publish_kit/qc.after.json
- /private/tmp/c2video-silence-recheck-08futjz6/agent_runs/run_7fd52197d18548a19e1d0cf49cddb4e8/publish_kit/qc.after.json

复检前后原视频 SHA-256 相同。没有重新抓取、调用模型或生成配音；没有修改 .env、原任务数据库、原质检报告或审批状态。修改前的代码备份：/private/tmp/c2video-silence.LLa3tE/before.tar.gz。

## 使用与边界

在 PyCharm 中停止并重新启动服务，然后对失败任务点击重试。当前任务重试机制只恢复失败步骤；SILENCE 失败在 live_quality 阶段时，会复用已生成的视频重新质检，通过后仍需要人工审批。

这项检查识别音量层面的静音，不验证口播是否完整、语义是否正确，也不能识别被背景音乐掩盖的缺失人声。没有宣称所有媒体缺陷或项目全部问题已解决。

