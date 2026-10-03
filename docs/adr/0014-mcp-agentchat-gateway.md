# MCP 接入复用已认证的 C2Video HTTP 控制面

日期：2026-09-17。用户要求将 C2Video 封装为 MCP，供本机 AgentChat 使用。

## 决策

- 增加可选的官方 MCP Python SDK 1.27.0，与本机 AgentChat 客户端主版本一致；不将 AgentChat 或 LangChain 引入 C2Video 核心依赖。
- 默认 stdio，AgentChat 按需启动接入进程；另提供带独立入口密钥、Host／Origin 限制及请求体上限的私有 Streamable HTTP。
- MCP 经固定配置的 Studio API 复用 ApplicationService、RunQueue、Supervisor 和权限检查；不直接打开 SQLite，不运行第二套 Worker，不在进程内持有视频制作状态。
- 创建与启动分开。创建必须给幂等键，启动固定 background=true。恢复／失败重试只改变任务状态，继续执行需单独排队；不自动重试超时或不确定的写请求。
- 默认 supervised，仅提供 supervised／assisted；不暴露 auto、approve_gate 或通用动作执行工具。人工在 Studio 审核，不能把 Agent 的参数当成人类审批证据。
- AI 反馈继续走已有提炼、校验与入库流程，不提供原文记忆写入口。confirmed 参数只是工作流提醒，不替代 API 权限，也不能证明真实用户确认。
- 发布包仅返回受保护的固定媒体路由，不在 URL 放密钥，不读任意文件，不把本地工作路径发送给客户端；增加固定 publish.md 下载类型，不自动发布。
- 接入层只接收 Studio 的访问密钥，不读取模型或数据源配置。错误返回固定错误码，不回显上游错误正文；工具内容限长，数据文本不能作为 Agent 指令。
- 本轮不修改 AgentChat 代码、账号、数据库或正在运行的服务。交付导入配置和操作说明；AgentChat 的 UI 注册步骤由用户执行。

## 取舍与边界

需要 Studio API 和一个正常执行的 Supervisor。MCP 断连不停止已入队的视频任务，但 MCP 本身不是任务监督者。读 API 目前返回完整任务列表／快照，网关限制响应大小与模型输出，不宣称数据库级分页或无限规模支持。

HTTP 入口是受信个人／小团队使用的共享密钥连接，不是完整 OAuth 服务，也没有自然人身份、多租户或逐用户权限映射。不同权限应使用不同网关配置与 Studio 访问密钥，或 stdio 只读实例；不要公开共享操作员入口。远程访问需受限网络和 TLS。

沿用 ADR-0013 的单机持久执行、人工审核、明确来源和不确定重试边界，无冲突。
