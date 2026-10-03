# C2Video 单机平台运行手册

容器发布版请先看 [容器部署手册](container-deployment.md)；下面保留非容器服务管理与数据恢复步骤。

这版交付的是**个人／受信小团队的单机生产化基础**：持久任务队列、受控后台执行、审核、访问保护、记忆消费、用量与平台运行页、备份恢复。代码已在本机隔离验证；没有切换现有服务，也没有开放公网或调用付费模型验收。

## 1. 使用范围与入口

使用同一台 Linux／macOS 主机的本地磁盘。SQLite 与锁文件不要放到 NFS、网盘同步目录；不支持多主机共同执行。操作员和只读角色共享整个工作空间，**不是每位用户独立项目或多租户隔离**。

Studio 新增“平台运行”，显示执行服务心跳、排队数、处理中请求和最近任务。已有“用量看板”显示模型请求的 Token、失败与耗时、用途、已知费用和未知用量。“离线”表示执行服务未就绪，已提交请求仍在数据库中。

本机开发可以继续使用：

```bash
.venv/bin/python -m c2video studio --host 127.0.0.1 --port 8765
```

默认启动内置 Supervisor。没有访问密钥时只允许本机访问；不要将无鉴权的开发入口经反向代理转发给别人。启动前确认没有旧实例占用相同数据库。CLI 可能选用可用端口，以启动输出为准。

## 2. 团队部署前的必填配置

真实环境使用 HTTPS，后端只监听 127.0.0.1。模板位于 deploy/platform.env.example；复制到受限环境文件后填写，不把密钥提交到仓库或发送到聊天。

| 配置 | 要求 |
| --- | --- |
| C2VIDEO_ENV | production；缺少操作员密钥将拒绝启动 |
| C2VIDEO_STUDIO_TOKEN | 至少 32 字符的随机操作员密钥 |
| C2VIDEO_VIEWER_TOKEN | 可选，独立随机只读密钥，不得与操作员相同 |
| C2VIDEO_ALLOWED_HOSTS | 实际访问主机名，逗号分隔，不带协议／端口，不用 * |
| C2VIDEO_ALLOWED_ORIGINS | 实际 HTTPS 来源，例如 https://studio.example.com |
| C2VIDEO_WORKER_MODE | external，用独立 Worker；embedded 适合本机调试 |
| C2VIDEO_WORKER_CONCURRENCY | 先用 1，范围 1–4；独立 Worker 参数保持一致 |
| C2VIDEO_WORK_DIR／C2VIDEO_FINAL_DIR | 同一服务用户可写的绝对数据路径 |
| C2VIDEO_CONFIG | API 和 Worker 使用同一个配置文件，缺失会报错 |

生产 Cookie 强制 Secure，不可用 HTTP 临时替代；只在回环地址测试登录时使用 development。访问密钥不是知乎／模型密钥；浏览器使用会话 Cookie，退出会在服务器撤销该会话。轮换共享密钥并重启 API 后，对应旧会话失效。共享密钥无法证明某次动作由哪个自然人执行。

API 客户端可在 Authorization: Bearer 请求头携带密钥；写入使用 application/json。创建 Run 可传 Idempotency-Key，同键不同参数返回冲突；同键同参数返回已有 Run。不要把密钥加到查询参数。

## 3. 安装与服务管理

先在目标机器的独立目录安装并检查 FFmpeg、ffprobe、Chromium、中文字体和磁盘空间。以下是操作模板，**本轮未执行服务器安装**：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -c constraints-platform.txt -e '.[dev]'
npm --prefix studio ci
npm --prefix studio run build
.venv/bin/python -m playwright install chromium
.venv/bin/python -m c2video doctor
```

constraints-platform.txt 固定本次验证环境的 Python 依赖版本，不含下载哈希，也不是漏洞扫描。前端使用 package-lock.json，验证环境为 Node 24。FFmpeg、浏览器和字体仍需在目标系统验证；升级依赖后重新运行测试。

Linux 可参考 deploy/c2video-api.service、deploy/c2video-worker.service、deploy/nginx.conf.example。默认路径 /opt/c2video、/etc/c2video、/var/lib/c2video 都是占位部署约定，需由管理员准备非特权服务用户、目录所有权、私密环境文件、TLS 证书及反向代理。

服务模板使用 PLAYWRIGHT_BROWSERS_PATH=/opt/c2video/browsers；应在安装浏览器时设同一变量，使服务用户可以读取。服务加固项与浏览器沙箱是否兼容需在目标 Linux 验证，不要因为启动失败就直接删除所有安全限制。

独立 Worker 的等价入口：

```bash
.venv/bin/python -m c2video.api.worker --daemon \
  --db /var/lib/c2video/work/c2video-agent.db \
  --work-dir /var/lib/c2video/work --concurrency 1
```

API 与 Worker 必须加载相同工作目录、数据库、配置、模型和 TTS 环境。程序化 create_app(config=...) 的内存配置不会自动序列化给子进程；真实部署应统一使用配置文件与环境变量。每个数据库只有一个有效 Supervisor，不靠多个 API 实例提高执行并发。

不要同时运行旧版本 Worker 或旧分阶段 CLI 来写同一目录。强制取消与超时保证面向后台队列；前台 CLI／开发用 background=false 不是相同的进程隔离边界。

## 4. 一条任务怎样完成

新建页默认“选题与成片均审核”。选题审核时可调整保留项和顺序，确认后才进入卡片、写稿与制作；成片经真实检查后等待最终审核。拒绝／取消会终止流程，不会自动发布视频。

- 暂停：当前步骤结束后停止；不代表模型请求马上停止计费。
- 取消：写入取消终态，后台监督者停止其拥有的 Worker 进程组。已发送的远程请求不能撤回，也不保证供应商停止计费。
- 失败重试：先看产物和供应商记录，再明确确认。只重新执行失败步骤，但该步骤内的外部调用可能重复。
- 崩溃恢复：排队请求保留；执行中结果未知的步骤标为失败，人工确认，不盲目自动重试。
- 已完成／已取消任务不能再执行；需要新的制作使用新任务。历史 Run 保留原计划，不会被偷偷换成新流程。
- 实时就地改稿暂未实现；请调整素材／偏好后新建，不能只改页面上的口播而不重新渲染视频。

真实媒体检查包括文件／时长、1080×1920 视频流、音轨、黑帧、静音与音量测量。检查失败保存结构化问题并阻断；静音规则区分正常停顿与异常：连续达到 1.5 秒或检测到的静音占比达到 35% 会阻断。不保证自动发现事实错误、字幕语义错误或所有音画缺陷，不把 Demo 修复当通用实时修复。

## 5. 记忆与费用边界

反馈先经 AI 提炼、校验、去重后入库。新任务冻结最多 10 条已启用且经 AI 处理的记忆；API 模型的选题和写稿按 selection／script 范围消费，事件记录使用的记忆 ID。规则模式不消费偏好，visual／quality 偏好暂未驱动渲染。停用不追溯修改已创建任务，模型是否实际遵从仍需人工抽检。

记忆提炼仍属于提交反馈的限时请求，不是持久后台任务；页面／服务中断后可能需重新提交。重复提交可能再次计费。

用量以逐次模型 HTTP 响应为依据；未知 Token、未知价格保留缺失语义。**Token 看板与 Run 的预算字段不等于美元硬限额**，失败／超时请求也可能由供应商计费；TTS 和数据源费用未统一纳入。应在供应商侧配置额度与告警。这次未使用付费模型验证，没有宣称真实模型质量达标。

## 6. 健康检查与常见故障

- /healthz：API 进程存活，无需登录。
- /readyz：数据库可读取且执行服务有近期心跳，未就绪返回 503；不代表模型／知乎凭证或媒体依赖全部可用。
- /api/health：登录后查看详细依赖和来源配置；“已配置”不是付费服务连通性证明。
- /api/platform：登录后查看队列、Worker 心跳与运行方式。
- SQLite 的 Run／Task／Event 是事实来源；JSONL 导出可能因磁盘问题不完整，不能据此重放付费调用。

| 现象 | 排查 |
| --- | --- |
| 请求得到 HTML／Unexpected token '<' | 检查是否连到旧版本服务或错误端口；新 API 未知路径返回 JSON 404，前端会提示错误入口 |
| 新任务长期排队 | 看平台运行页和 /readyz，检查 Worker 是否启动、数据库路径是否一致 |
| 一直等待审核 | 切换到相应任务，由操作员确认；只读角色不能通过 |
| Worker 异常退出 | 恢复服务，等待锁释放与中断标记，检查外部调用结果后重试 |
| 登录后仍未登录 | production 是否经 HTTPS；Host／Origin 是否正确，代理是否正确传递协议 |
| 429 | 登录失败限流，等待窗口结束；模型 429 则排查供应商额度，平台不会为其反复做格式回退 |
| 媒体检查失败 | 查看 qc.after.json 和结构化问题，修正依赖、输入或渲染后人工重试 |
| Ledger 无法读取 | 停止制作并从有效备份恢复，不删除历史来“恢复运行” |
| 存储空间不足 | 暂停接单，备份并人工归档；平台尚未自动清理历史媒体 |

日常观察排队时间、失败率、未知用量比例、磁盘增长和 Worker 心跳；保留请求编号定位服务错误。当前没有外部告警服务、全链路追踪或保留期限自动清理。

## 7. 备份与恢复演练

先停止接单，让执行中任务到达审核／完成／明确失败状态，再停止 API、Worker 和所有 CLI 写入者。确认没有活跃心跳和 running 作业。不能仅拷贝运行中的 SQLite 主文件来代替一致备份。

```bash
.venv/bin/python -m c2video.ops backup \
  --work-dir /var/lib/c2video/work \
  --db /var/lib/c2video/work/c2video-agent.db \
  --output /var/backups/c2video/backup-20260915 \
  --confirm-services-stopped

.venv/bin/python -m c2video.ops verify \
  --backup /var/backups/c2video/backup-20260915

.venv/bin/python -m c2video.ops restore \
  --backup /var/backups/c2video/backup-20260915 \
  --output /var/lib/c2video/restore-check-20260915
```

备份和恢复目标必须尚不存在，备份不得嵌套在工作目录内；拒绝符号链接。备份包含数据库和工作目录内文件的 SHA-256 清单。恢复会校验文件清单、摘要和 SQLite 完整性，更新明确的 Artifact 工作目录路径，撤销会话并暂停活跃任务，不自动恢复排队执行。

恢复只写新目录，不能覆盖正在使用的数据库。备份工具无法阻止不配合的外部写入者，停止服务是操作前提。工作目录外的素材／配置／模型凭证、旧文本记录中的绝对路径不会自动搬迁；另行私密备份配置并检查这些依赖。不要在原路径直接降级代码读已升级数据库，回滚应使用升级前代码和数据副本。

建议定期在隔离目录恢复，检查任务数、记忆、用量与随机成片；保存至少一份离机、受访问保护的备份。周期和恢复目标由实际数据量与业务需要确定，当前没有测得的 RPO／RTO 或容量承诺。

## 8. 上线前最后确认

- [ ] 目标机器的依赖、中文字体、浏览器沙箱和渲染跑通。
- [ ] HTTPS、Host／Origin、操作员／只读权限和密钥轮换验证。
- [ ] 同一配置下 API 与独立 Worker 配合正常。
- [ ] 真实知乎／导入素材、TTS、模型配置经授权的小样本验收。
- [ ] 供应商限额、磁盘与日志监控安排完成。
- [ ] 目标机器上完成备份恢复演练与持续运行观察。
- [ ] 人工审核来源、事实、版权与记忆效果；不以 Demo 成功率代替。

本机验证结果见 [验收记录](verification/platform-2026-09-15.md)，架构边界见 [ADR-0013](adr/0013-single-host-production-foundation.md)。

