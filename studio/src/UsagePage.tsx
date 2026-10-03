import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "./api";
import type { UsageDashboard, UsageTotals } from "./types";

const count = (value: number | null | undefined) => value == null ? "—" : value.toLocaleString("zh-CN");
const dollars = (value: number | null | undefined) => value == null ? "未提供" : `US$ ${value.toFixed(6)}`;
const duration = (value: number | null | undefined) => value == null ? "—" : `${(value / 1000).toFixed(2)} 秒`;
const purposeName = (value: string) => ({ memory: "记忆整理", curate: "选题", script: "写稿", other: "其他" }[value] || value);

function Breakdown({ rows, dimension }: { rows: Array<UsageTotals & { model?: string; purpose?: string }>; dimension: "model" | "purpose" }) {
  return <div className="usage-table-wrap"><table className="usage-table">
    <thead><tr><th>{dimension === "model" ? "模型" : "用途"}</th><th>请求</th><th>输入 Token</th><th>输出 Token</th><th>合计 Token</th><th>失败 / 取消</th></tr></thead>
    <tbody>{rows.map(row => <tr key={row[dimension]}>
      <td>{dimension === "model" ? row.model : purposeName(row.purpose || "other")}</td>
      <td>{count(row.calls)}</td><td>{count(row.input_tokens)}</td><td>{count(row.output_tokens)}</td>
      <td>{count(row.total_tokens)}{row.unknown_usage_calls > 0 && <small>含 {row.unknown_usage_calls} 次用量不完整</small>}</td>
      <td>{row.failed_calls} / {row.canceled_calls}</td>
    </tr>)}</tbody>
  </table></div>;
}

export function UsagePage() {
  const chart = useRef<HTMLDivElement>(null);
  const [days, setDays] = useState(7);
  const [revision, setRevision] = useState(0);
  const [data, setData] = useState<UsageDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setLoading(true);
    setData(null);
    const load = async () => {
      try {
        const result = await api.usage(days);
        if (active) { setData(result); setError(""); }
      } catch (cause) { if (active) setError(String(cause)); }
      finally { if (active) setLoading(false); }
    };
    void load();
    const timer = window.setInterval(() => void load(), 30000);
    return () => { active = false; window.clearInterval(timer); };
  }, [days, revision]);

  const latestDate = data?.daily[data.daily.length - 1]?.date;
  const hasCalls = Boolean(data?.totals.calls);
  useEffect(() => {
    if (chart.current) chart.current.scrollLeft = chart.current.scrollWidth;
  }, [data?.days, latestDate, hasCalls]);

  const totals = data?.totals;
  const maximum = Math.max(1, ...(data?.daily.map(day => day.total_tokens ?? 0) || []));
  return <div className="page usage-page">
    <div className="page-header"><h1>用量看板</h1><div className="usage-controls">
      <label>统计范围 <select value={days} onChange={event => setDays(Number(event.target.value))}>
        <option value={7}>最近 7 天</option><option value={30}>最近 30 天</option><option value={90}>最近 90 天</option>
      </select></label>
      <button className="primary-button" onClick={() => setRevision(value => value + 1)} disabled={loading}>刷新</button>
    </div></div>
    <p className="usage-note">统计本次功能接入后记录的模型请求，按 UTC 日期汇总，每 30 秒刷新。历史 Demo 示例金额不计入。</p>
    {error && <div className="notice notice--error" role="alert">{error}</div>}
    {loading && !data && <p role="status">正在读取用量…</p>}
    {data && totals && <>
      <div className="usage-cards">
        <article><span>已报告 Token</span><strong>{count(totals.total_tokens)}</strong><p>输入 {count(totals.input_tokens)} · 输出 {count(totals.output_tokens)}</p></article>
        <article><span>已记录模型请求</span><strong>{count(totals.calls)}</strong><p>失败 {totals.failed_calls} · 取消 {totals.canceled_calls}</p></article>
        <article><span>平均请求耗时</span><strong>{duration(totals.average_latency_ms)}</strong><p>包含失败请求，不是视频生成总耗时</p></article>
        <article><span>供应商返回费用</span><strong className="usage-money">{dollars(totals.reported_cost_usd)}</strong><p>仅使用明确标注为 USD 的返回值</p></article>
        <article><span>按配置价格估算</span><strong className="usage-money">{dollars(totals.estimated_cost_usd)}</strong><p>不是实际账单，不与已返回费用重复计入</p></article>
        <article><span>期间新建任务</span><strong>{count(Object.values(data.runs).reduce((sum, value) => sum + value, 0))}</strong><p>已完成 {data.runs.COMPLETE || 0} · 失败 {data.runs.FAILED || 0}</p></article>
      </div>
      <p className="usage-coverage" role="note">{totals.unknown_usage_calls} 次请求未返回完整 Token 用量，{totals.unpriced_calls} 次费用未知。“— / 未提供”不代表零消耗；估算暂不考虑缓存折扣。</p>
      <div className="usage-memory-summary"><span>当前已启用记忆 <strong>{data.memories.approved_now}</strong></span><span>期间新增记忆 <strong>{data.memories.created_in_period}</strong></span><span>期间反馈 <strong>{data.feedback_count}</strong></span><Link to="/memory">管理记忆</Link></div>
      {totals.calls === 0 ? <section className="work-panel quiet-state"><strong>这段时间还没有模型调用记录</strong><p>使用已配置的 API 模型完成选题、写稿或记忆整理后，这里会开始统计。规则模式和未调用模型的 Demo 不产生 Token 记录。</p></section>
        : <>
          <section className="work-panel usage-section"><h2>每日已报告 Token</h2>
            <p className="usage-note">较窄屏幕可左右滑动趋势和明细表；趋势默认显示最近日期。</p>
            <div ref={chart} className="usage-chart" role="img" aria-label="每日已报告 Token 趋势，按 UTC 日期汇总">
              {data.daily.map(day => <div className="usage-day" key={day.date} title={`${day.date}：${day.calls} 次请求，${count(day.total_tokens)} Token，${day.unknown_usage_calls} 次用量不完整`}>
                <span>{day.total_tokens == null ? (day.calls ? "未知" : "") : count(day.total_tokens)}</span>
                <div className="usage-bar-space"><div className={`usage-bar${day.calls && day.total_tokens == null ? " usage-bar--unknown" : ""}`} style={{height: day.total_tokens == null ? (day.calls ? 10 : 0) : Math.max(2, day.total_tokens / maximum * 120)}}/></div>
                <small>{day.date.slice(5)}</small>
              </div>)}
            </div>
          </section>
          <section className="work-panel usage-section"><h2>按模型</h2><Breakdown rows={data.models} dimension="model"/></section>
          <section className="work-panel usage-section"><h2>按用途</h2><Breakdown rows={data.purposes} dimension="purpose"/></section>
          <section className="work-panel usage-section"><h2>最近请求</h2><p className="usage-note">最多显示 30 次；一次业务操作可能因格式回退或改稿而产生多次请求。</p>
            <div className="usage-table-wrap"><table className="usage-table"><thead><tr><th>时间</th><th>模型 / 用途</th><th>状态</th><th>Token</th><th>耗时</th><th>来源任务</th></tr></thead><tbody>
              {data.recent.map(call => <tr key={call.usage_id}><td>{new Date(call.created_at).toLocaleString("zh-CN")}</td><td>{call.model}<small>{purposeName(call.purpose)}</small></td><td>{call.status === "succeeded" ? "接口成功" : call.status === "canceled" ? "已取消" : "失败"}{call.error_type && <small>{call.error_type}</small>}</td><td>{count(call.total_tokens)}</td><td>{duration(call.latency_ms)}</td><td>{call.run_id ? <Link to={`/runs/${call.run_id}`}>查看任务</Link> : "独立调用"}</td></tr>)}
            </tbody></table></div>
          </section>
        </>}
      <p className="usage-note">接口成功不代表记忆写入或内容质量通过。未记录请求正文、生成正文或密钥；供应商未提供的用量无法从历史结果可靠补算。</p>
    </>}
  </div>;
}

