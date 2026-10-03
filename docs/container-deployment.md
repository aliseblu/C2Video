# C2Video 容器部署与发布

本项目面向个人或受信小团队的单台 Linux 主机。API、独立 Worker 和 SQLite 使用同一个本地数据卷；不是多租户 SaaS，不支持跨主机共享 SQLite。本次交付只做本机部署演练，不发布公网。

## 本机体验

安装并启动 Docker Desktop 或 Linux Docker Engine 和 Compose v2。建议先给 Docker 6 GB 内存，CPU 预算先按 API 1 核、Worker 2 核；这是起始配置，不是实测容量承诺。

在项目根目录执行：

```bash
cp deploy/compose.env.example deploy/compose.env
chmod 600 deploy/compose.env
python3 -c "import secrets; print(secrets.token_urlsafe(40))"
```

将生成的随机值填入私密文件的 C2VIDEO_STUDIO_TOKEN。可另外生成一个不同的 C2VIDEO_VIEWER_TOKEN 供只读访问。不要使用模型或知乎密钥充当登录密钥。

仅在自己的电脑以 HTTP 体验时，将 C2VIDEO_ENV 改为 development。保持端口绑定 127.0.0.1；不要把该开发配置开放到公网。随后运行：

```bash
docker compose --env-file deploy/compose.env up -d --build --wait
docker compose --env-file deploy/compose.env ps
```

浏览器打开 http://127.0.0.1:8765 并输入登录密钥。先在页面创建明确标记的演示任务；演示数据不会伪装成实时内容。准备真实制作时，可导入有权使用的知乎素材，或填写官方数据源密钥。模型改写需要单独设置 C2VIDEO_LLM_PROVIDER=api、接口地址、模型和密钥；没有模型时只有规则摘录。Edge 配音默认免费，但依赖外网且没有本项目提供的 SLA；语音合成 API 配置沿用 docs/tts-config.md。

修改配置后执行同一条 up 命令重建服务。不要把既有本机 work 目录直接挂进去覆盖数据卷；迁移使用备份恢复流程。

## 未来接入公网的必备条件

1. 恢复 C2VIDEO_ENV=production，必须使用 HTTPS；此模式强制 Secure Cookie。
2. 配置实际的 C2VIDEO_ALLOWED_HOSTS 和 HTTPS 的 C2VIDEO_ALLOWED_ORIGINS。
3. 将受信反向代理放在宿主机，转发到回环端口；参考 deploy/nginx.conf.example。覆盖而不是追加来自客户端的转发头。
4. Docker 端口映射下代理地址可能是网桥网关；按目标环境实际地址设置 C2VIDEO_FORWARDED_ALLOW_IPS，默认仅信任 127.0.0.1。不要盲目设置 *。
5. 保持数据卷在本地文件系统，不放 NFS／网盘；配置磁盘与费用告警、离机备份。
6. 授权后对实际知乎、模型与语音服务做小样本验收；离线测试不证明服务额度、事实准确性或商用权利。

模板不会自动申请服务器、DNS、证书或把代码部署出去。MCP 是可选的独立接入层，默认容器只提供 Studio；如需 MCP，按专门手册安装并配置，不开放无鉴权网关。

## 启动保护与运行检查

API 和 Worker 使用同一环境文件。默认各 1 个进程，Worker 内部并发 1，可调 1–4；不要通过横向扩容 Worker 绕过单机锁。固定端口被占用时启动失败，不自动换端口。

容器使用 UID 10001、只读根文件系统、独立临时目录、去除 Linux capabilities、禁止提升权限、内存／CPU／PID 限额和日志轮转。Chromium 的应用启动参数仍由 Playwright 管理；这些加固不等于针对恶意租户的完整浏览器安全沙箱。

- /healthz：API 进程存活；Compose 用它启动 Worker，避免就绪检查相互等待。
- /readyz：数据库可读取并有新鲜 Worker 心跳；Worker 停止时返回 503。
- /api/metrics：需登录或 Bearer 密钥；提供 Worker、队列和可用磁盘指标，不带用户主题、任务 ID 或密钥标签。
- 待执行队列默认上限 100，同一事务内检查并入队；已排队任务再次启动仍幂等。
- 生产默认至少保留 1024 MB 可用磁盘，低于阈值拒绝创建／启动任务，返回 503 与 Retry-After。该检查不是硬磁盘配额，正在渲染的任务仍可能写满磁盘。
- 保留静音、黑帧、音轨、画幅与音量检查，正常句间停顿不再直接判失败；事实和版权由人工审查。

## 停止与数据保护

```bash
docker compose --env-file deploy/compose.env stop
docker compose --env-file deploy/compose.env down
```

这两条命令保留数据卷。不要加 --volumes；它会删除持久数据。应用只生成发布包，绝不自动上传到社交平台。

停止全部写入者后，可用一次性容器在同一数据卷内备份：

```bash
docker compose --env-file deploy/compose.env run --rm --no-deps --entrypoint python worker \
  -m c2video.ops backup --work-dir /data/work --db /data/work/c2video-agent.db \
  --output /data/backup-before-upgrade --confirm-services-stopped
docker compose --env-file deploy/compose.env run --rm --no-deps --entrypoint python worker \
  -m c2video.ops verify --backup /data/backup-before-upgrade
```

该备份仍在同一卷中，不足以防磁盘损坏；必须另存一份受访问保护的离机备份。恢复只写新目录，步骤及限制见 docs/production-runbook.md。回滚同时保留旧镜像和匹配的备份，不用旧代码直接读取升级后的数据库。

## 发布验证

```bash
npm --prefix studio ci
npm --prefix studio test
npm --prefix studio run build
python -m pip install -c constraints-mcp.txt -e '.[dev,mcp]'
python -m pytest -q -rs
python scripts/verify_platform.py  # 先设置 C2VIDEO_QA_DIR 为全新临时目录
docker build -t c2video:local .
python scripts/verify_container.py
```

容器脚本使用随机命名的隔离 Compose 项目、临时密钥和离线演示素材，验证队列重启、两个审核节点、实际 MP4、权限、备份恢复与持久化。结束只清理它创建的测试容器和合成测试数据，不读取项目 .env 或现有工作目录。

GitHub Actions 执行相同的后端／前端／MCP、浏览器、漏洞审计和 Linux 容器检查；不会自动部署或公开镜像。基础镜像固定摘要，Python 和前端依赖固定版本；OS 包仍从 Debian 仓库安装，Python 清单尚未包含 wheel 哈希，所以不宣称逐字节可复现。Dependabot 提议升级后需重新验收。

参考官方部署约定：[FastAPI 容器](https://fastapi.tiangolo.com/deployment/docker/)、[受信代理](https://fastapi.tiangolo.com/advanced/behind-a-proxy/)、[Compose 启动依赖](https://docs.docker.com/compose/how-tos/startup-order/)。

