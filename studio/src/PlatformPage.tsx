import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "./api";

export function PlatformPage() {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.platform>> | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setData(await api.platform()); setError(""); }
    catch (cause) { setError(String(cause)); }
  }, []);
  useEffect(() => { void load(); const timer = setInterval(load, 5000); return () => clearInterval(timer); }, [load]);
  return <div className="page">
    <div className="page-header"><h1>平台运行</h1><button className="primary-button" onClick={() => void load()}>刷新</button></div>
    {error && <div role="alert" className="notice notice--error">{error}</div>}
    {!data ? <p>正在读取服务状态…</p> : <>
      <div className="platform-grid">
        <section className="work-panel"><h2>执行服务</h2><strong>{data.worker_ready ? "在线" : "离线"}</strong><p>模式：{data.worker_mode} · 并发上限 {data.concurrency_limit}</p></section>
        <section className="work-panel"><h2>持久队列</h2><strong>{data.counts.queued ?? 0} 排队中</strong><p>{data.counts.running ?? 0} 个执行请求处理中</p></section>
        <section className="work-panel"><h2>访问保护</h2><strong>{data.authentication_enabled ? "已启用" : "仅本机开发"}</strong><p>{data.environment} · v{data.version}</p></section>
      </div>
      {!data.worker_ready && <div role="status" className="notice">执行服务未就绪，新任务会保留在队列中。请检查 Worker 或重新启动 Studio。</div>}
      <p className="memory-intro">单机本地磁盘、共享工作空间；不提供多租户隔离。中途退出的任务需检查后确认重试，可能涉及重复外部调用。</p>
      <section className="work-panel platform-jobs"><h2>最近执行请求</h2>
        {!data.recent.length ? <p>还没有执行请求</p> : <div className="platform-table-scroll"><table><thead><tr><th>任务</th><th>队列状态</th><th>提交时间</th><th>说明</th></tr></thead>
          <tbody>{data.recent.map(job => <tr key={job.run_id}>
            <td><Link to={`/runs/${job.run_id}`}>{job.run_id.slice(0, 16)}</Link></td>
            <td>{{ queued: "排队", running: "处理中", idle: "已停止／等待审核", failed: "失败" }[job.status] ?? job.status}</td>
            <td>{new Date(job.requested_at * 1000).toLocaleString()}</td><td>{job.error ?? "—"}</td>
          </tr>)}</tbody></table></div>}
      </section>
    </>}
  </div>;
}
