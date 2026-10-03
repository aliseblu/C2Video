import {
  Activity,
  BarChart3,
  ArrowDown,
  ArrowUp,
  BookOpen,
  Bot,
  Check,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Clock3,
  FileDiff,
  Film,
  LayoutDashboard,
  MemoryStick,
  Menu,
  Moon,
  OctagonX,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Settings,
  ShieldCheck,
  Sun,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, Route, Routes, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "./api";
import { FeedbackForm } from "./FeedbackForm";
import { UsagePage } from "./UsagePage";
import { PlatformPage } from "./PlatformPage";
import { useSession } from "./SessionGate";
import type { Decision, EvidencePack, JsonObject, RunEvent, RunRow, Snapshot, TaskRow } from "./types";

const navItems = [
  ["/", "总览", LayoutDashboard],
  ["/new", "新建", Plus],
  ["/usage", "用量看板", BarChart3],
  ["/platform", "平台运行", Activity],
  ["/memory", "记忆", MemoryStick],
  ["/settings", "设置", Settings],
] as const;

function stateTone(state: string) {
  if (["COMPLETE", "succeeded", "approved"].includes(state)) return "success";
  if (["FAILED", "error", "blocker", "failed"].includes(state)) return "danger";
  if (state.includes("WAIT") || state.includes("warning") || state === "waiting_human") return "warning";
  if (["CANCELED", "canceled", "pending", "rejected", "expired"].includes(state)) return "muted";
  return "running";
}

function statusLabel(value: string) {
  return ({
    COMPLETE: "完成", FAILED: "失败", PLAN: "未开始", CANCELED: "已取消",
    succeeded: "完成", failed: "失败", pending: "等待", running: "进行中",
    skipped: "跳过", paused: "暂停", warning: "注意",
    approved: "已启用", rejected: "已停用", expired: "已过期",
  } as Record<string, string>)[value] || value.replaceAll("_", " ");
}

function taskTitle(task: TaskRow) {
  return ({
    legacy_fetch: "抓素材", legacy_curate: "选题", legacy_card: "做卡片",
    legacy_script: "写口播", legacy_render: "合成视频",
    discover: "抓素材", research: "核材料", curate: "选题", script: "写口播",
    produce: "合成视频",
  } as Record<string, string>)[task.task_type] || task.task_type.replaceAll("_", " ");
}

function Status({ value }: { value: string }) {
  return <span className={`status status--${stateTone(value)}`}><i />{statusLabel(value)}</span>;
}

function Shell({ children }: { children: React.ReactNode }) {
  const session = useSession();
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    window.localStorage.getItem("c2video-theme") === "dark" ? "dark" : "light",
  );
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    window.localStorage.setItem("c2video-theme", theme);
  }, [theme]);
  return (
    <div className="shell">
      <aside className={`rail ${open ? "rail--open" : ""}`}>
        <div className="brand"><span className="brand__mark">C2</span><span><strong>C2Video</strong><small>Agent Studio</small></span></div>
        <nav aria-label="主要导航">
          {navItems.filter(([to]) => session.role !== "viewer" || to !== "/new").map(([to, label, Icon]) => <NavLink key={to} to={to} end={to === "/"} onClick={() => setOpen(false)}><Icon size={18}/><span>{label}</span></NavLink>)}
        </nav>
      </aside>
      <div className="shell__body">
        <header className="topbar">
          <button className="icon-button mobile-only" aria-label="打开导航" onClick={() => setOpen(!open)}><Menu size={19}/></button>
          <div className="topbar__spacer" />
          <div className="topbar__right">
            <button className="theme-toggle" aria-label={`切换到${theme === "light" ? "深色" : "浅色"}模式`} onClick={() => setTheme(theme === "light" ? "dark" : "light")}>
              {theme === "light" ? <Moon size={16}/> : <Sun size={16}/>}<span>{theme === "light" ? "深色" : "浅色"}</span>
            </button>
            {session.role !== "viewer" && <Link className="primary-button primary-button--small" to="/new"><Plus size={16}/>新建 Run</Link>}
            {session.required && <button onClick={() => void session.logout()}>{session.role === "viewer" ? "只读 · " : ""}退出</button>}
          </div>
        </header>
        <main>{children}</main>
      </div>
      {open && <button className="scrim" aria-label="关闭导航" onClick={() => setOpen(false)}/>} 
    </div>
  );
}

function PageHeader({ title, action }: { eyebrow?: string; title: string; detail?: string; action?: React.ReactNode }) {
  return <div className="page-header"><h1>{title}</h1>{action && <div className="page-header__actions">{action}</div>}</div>;
}

function useRuns() {
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [error, setError] = useState("");
  const load = useCallback(() => api.runs().then(r => { setRuns(r.items); setError(""); }).catch(e => setError(String(e))), []);
  useEffect(() => { void load(); const timer = window.setInterval(load, 2500); return () => clearInterval(timer); }, [load]);
  return { runs, error, load };
}

function Dashboard() {
  const { runs, error } = useRuns();
  const active = runs.filter(r => !["COMPLETE", "FAILED", "CANCELED", "PLAN"].includes(r.state) && !r.state.includes("WAIT"));
  const completed = runs.filter(r => r.state === "COMPLETE");
  const gates = runs.filter(r => r.state.includes("WAIT"));
  return <div className="page dashboard">
    <PageHeader title="今天的生产台" action={<Link className="primary-button" to="/new"><Plus size={16}/>新建</Link>}/>
    {error && <div className="notice notice--error"><CircleAlert size={17}/>{error}</div>}
    <section className="attention-strip">
      <div><span>等待决策</span><strong>{gates.length}</strong></div>
      <div><span>运行中</span><strong>{active.length}</strong></div>
      <div><span>已完成</span><strong>{completed.length}</strong></div>
      <div><span>完成率</span><strong>{runs.length ? Math.round(completed.length / runs.length * 100) : 0}%</strong></div>
    </section>
    <div className="dashboard-grid">
      <section className="work-panel work-panel--runs">
        <div className="section-heading"><div><h2>最近</h2></div></div>
        {runs.length === 0 ? <EmptyState title="还没有 Run" detail="新建一条，抓热点做成片。"/> : <div className="run-table">
          <div className="run-table__head"><span>Run / Goal</span><span>状态</span><span>自治</span><span>更新时间</span><span/></div>
          {runs.map(run => <Link className="run-row" key={run.run_id} to={`/runs/${run.run_id}`}><div><code>{run.run_id.slice(0, 16)}</code><strong>{run.summary || "等待 Planner"}</strong></div><Status value={run.is_paused ? "paused" : run.state}/><span className="mono">{run.autonomy}</span><time>{relativeTime(run.updated_at)}</time><ChevronRight size={17}/></Link>)}
        </div>}
      </section>
      <aside className="work-panel gates-panel">
        <div className="section-heading"><div><h2>待处理</h2></div></div>
        {gates.length ? gates.map(run => <Link className="gate-item" key={run.run_id} to={`/runs/${run.run_id}`}><span className="gate-item__icon"><Pause size={16}/></span><div><strong>{run.state === "WAIT_GATE_1" ? "选题组合待确认" : "成片审查待确认"}</strong><small>{run.run_id.slice(0, 14)} · {run.format}</small></div><ChevronRight size={16}/></Link>) : <div className="quiet-state"><CircleCheck size={24}/><strong>没有阻塞中的 Gate</strong><span>新问题会出现在这里。</span></div>}
      </aside>
    </div>
  </div>;
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return <div className="empty-state"><Bot size={28}/><strong>{title}</strong><p>{detail}</p><Link to="/new">新建 <ChevronRight size={15}/></Link></div>;
}

function NewRun() {
  const navigate = useNavigate();
  const { role } = useSession();
  const [autonomy, setAutonomy] = useState("supervised");
  const creation = useRef<{ signature: string; key: string } | null>(null);
  const [query, setQuery] = useState("整理知乎上的 AI 与科技话题，保留原文出处，制作中文口播视频。");
  const [duration, setDuration] = useState(60);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [health, setHealth] = useState<Awaited<ReturnType<typeof api.health>> | null>(null);
  const [materials, setMaterials] = useState<Record<string, unknown>[] | null>(null);
  const [fileName, setFileName] = useState("");
  useEffect(() => { void api.health().then(setHealth).catch(() => setError("无法读取服务配置，请检查服务是否已启动。")); }, []);
  const loadFile = async (file?: File) => {
    setMaterials(null); setFileName(""); setError("");
    if (!file) return;
    try {
      if (file.size > 1_000_000) throw new Error("素材文件不能超过 1 MB。");
      const parsed = JSON.parse((await file.text()).replace(/^\uFEFF/, ""));
      const items = Array.isArray(parsed) ? parsed : parsed.items;
      if (!Array.isArray(items) || !items.length || items.length > 100) throw new Error("请选择含 1–100 条素材的 JSON 数组或 items 对象。");
      setMaterials(items); setFileName(file.name);
    } catch (e) { setError(e instanceof Error ? e.message : "素材文件无法读取"); }
  };
  const submit = async () => {
    setBusy(true); setError("");
    try {
      const payload = {
        query, autonomy, target_duration_seconds: duration,
        preferred_format: "news_recap", mode: "live",
        ...(materials ? { materials } : {}),
      };
      const signature = JSON.stringify(payload);
      if (!creation.current || creation.current.signature !== signature) creation.current = { signature, key: crypto.randomUUID() };
      const snapshot = await api.createRun(payload, creation.current.key);
      await api.start(snapshot.run.run_id, true);
      navigate(`/runs/${snapshot.run.run_id}`);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return <div className="page composer-page">
    <PageHeader title="做成片"/>
    {role === "viewer" && <p role="note">当前为只读角色，不能创建任务。</p>}
    {error && <div className="notice notice--error" role="alert"><CircleAlert size={17}/>{error}</div>}
    <div className="composer">
      <section className="composer__intent">
        <label htmlFor="goal">想做什么</label>
        <textarea id="goal" value={query} onChange={e => setQuery(e.target.value)} />
        <label htmlFor="zhihu-materials">导入知乎素材（可选，无需密钥）</label>
        <input id="zhihu-materials" type="file" accept=".json,application/json" disabled={busy}
          onChange={e => { void loadFile(e.target.files?.[0]); }} />
        <p>每条需包含 title、url，可补充 content 和 author。仅使用你有权使用的内容；原文链接会保留。</p>
        {fileName && <p>已选择 {fileName} · {materials?.length} 条
          <button type="button" disabled={busy} onClick={() => { setMaterials(null); setFileName(""); }}>清除导入</button></p>}
      </section>
      <aside className="composer__constraints">
        <p>{materials ? "本次使用导入素材，不调用知乎接口。" : health?.source_detail || "使用配置的数据源"}</p>
        <label>审核方式<select aria-label="审核方式" value={autonomy} onChange={e => setAutonomy(e.target.value)}><option value="supervised">选题与成片均审核</option><option value="assisted">仅成片审核</option><option value="auto">自动执行（仍做媒体检查）</option></select></label>
        <label>时长<div className="range-row"><input type="range" min="30" max="180" step="15" value={duration} onChange={e => setDuration(Number(e.target.value))}/><output>{duration}s</output></div></label>
        <button className="primary-button primary-button--wide" onClick={submit}
          disabled={role === "viewer" || !query.trim() || busy || (!health?.live_ready && !materials)}>
          {busy ? <RefreshCw className="spin" size={17}/> : <Play size={17}/>}做成片
        </button>
      </aside>
    </div>
  </div>;
}

const views = [["timeline", "进度", Activity], ["curation", "选题", BookOpen], ["script", "口播", FileDiff], ["qc", "成片", ShieldCheck]] as const;

function RunWorkspace() {
  const canWrite = useSession().role !== "viewer";
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const view = params.get("view") || "timeline";
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const load = useCallback(() => api.run(id).then(next => { setSnapshot(next); setError(""); }).catch(e => setError(String(e))), [id]);
  useEffect(() => { setSnapshot(null); setError(""); }, [id]);
  useEffect(() => { void load(); const timer = window.setInterval(load, 1000); return () => clearInterval(timer); }, [load]);
  const action = async (name: string, payload: Record<string, unknown> = {}) => { setBusy(name); try { const next = await api.action(id, name, payload); setSnapshot(next); if (name === "fork") { navigate(`/runs/${next.run.run_id}`); return; } if ((name === "resume" || name === "approve_gate" || name === "retry") && next.run.state !== "COMPLETE") await api.start(id, true); } catch (e) { setError(String(e)); } finally { setBusy(""); } };
  if (!snapshot && error) return <div className="page"><div className="notice notice--error" role="alert"><CircleAlert size={17}/>{error}</div><Link className="primary-button" to="/">返回总览</Link></div>;
  if (!snapshot) return <div className="page loading-state"><RefreshCw className="spin"/>载入 Run…</div>;
  const run = snapshot.run;
  const spent = run.spent as Record<string, number>;
  return <div className="run-workspace">
    <div className="run-controlbar">
      <div className="run-title"><Link to="/">Runs</Link><ChevronRight size={14}/><code>{run.run_id}</code><Status value={run.is_paused ? "paused" : run.state}/></div>
      <div className="run-stats"><span><Clock3 size={14}/>{spent.runtime_seconds ?? 0}s</span></div>
      <div className="run-actions">
        {canWrite && run.state === "FAILED" && <button disabled={Boolean(busy)} onClick={() => { if (window.confirm("重试可能重复外部调用或费用。已检查产物并确认重试？")) void action("retry"); }}><RefreshCw size={15}/>重试</button>}
        {canWrite && snapshot.tasks.some(task => task.status === "waiting_human") && <button disabled={Boolean(busy)} className="approve-button" onClick={() => action("approve_gate", { summary: "通过" })}><Check size={15}/>通过</button>}
        {canWrite && !["COMPLETE", "FAILED", "CANCELED"].includes(run.state) && <><button disabled={Boolean(busy)} onClick={() => action(run.is_paused ? "resume" : "pause")}>{run.is_paused ? "恢复" : "暂停"}</button></>}
        {canWrite && !["COMPLETE", "FAILED", "CANCELED"].includes(run.state) && <button disabled={Boolean(busy)} className="danger-ghost" onClick={() => action("cancel")}><OctagonX size={15}/>取消</button>}
      </div>
    </div>
    {(error || (run.state === "FAILED" && run.error)) && <div className="notice notice--error"><CircleAlert size={16}/>{error || run.error}<button onClick={() => setError("")}><X size={14}/></button></div>}
    <div className="workspace-tabs" role="tablist">{views.map(([key, label, Icon]) => <button key={key} className={view === key ? "active" : ""} onClick={() => setParams({ view: key })}><Icon size={16}/>{label}</button>)}</div>
    <div className="workspace-content">
      {view === "timeline" && <Timeline snapshot={snapshot}/>} 
      {view === "curation" && <Curation snapshot={snapshot} action={action}/>} 
      {view === "script" && <ScriptView snapshot={snapshot}/>} 
      {view === "qc" && <QCLab snapshot={snapshot}/>} 
    </div>
    {canWrite && <FeedbackForm key={id} runId={id}/>}
    {busy && <div className="action-toast"><RefreshCw className="spin" size={15}/>{busy.replaceAll("_", " ")}</div>}
  </div>;
}

function Timeline({ snapshot }: { snapshot: Snapshot }) {
  const eventsByTask = useMemo(() => new Map(snapshot.events.filter(e => e.status).map(e => [e.state, e])), [snapshot.events]);
  return <div className="timeline-layout">
    <section className="timeline-panel">
      <div className="section-heading"><div><h2>进度</h2></div></div>
      <div className="timeline-list">{snapshot.tasks.map((task, index) => <TaskItem key={task.task_id} task={task} event={eventsByTask.get(task.target_state)} index={index}/>)}</div>
    </section>
    <aside className="inspector">
      <h2>这条要做什么</h2>
      <p className="inspector__summary">{snapshot.goal.query}</p>
    </aside>
  </div>;
}

function TaskItem({ task, event, index }: { task: TaskRow; event?: RunEvent; index: number }) {
  return <article className={`task-item task-item--${stateTone(task.status)}`}><div className="task-spine"><span>{task.status === "succeeded" || task.status === "skipped" ? <Check size={14}/> : index + 1}</span></div><div className="task-main"><div className="task-main__top"><div><strong>{taskTitle(task)}</strong></div><Status value={task.status}/></div><p>{task.error || event?.summary || (task.status === "pending" ? "还没轮到" : "")}</p></div></article>;
}

function Curation({ snapshot, action }: { snapshot: Snapshot; action: (name: string, payload?: Record<string, unknown>) => Promise<void> }) {
  const readOnly = useSession().role === "viewer" || (snapshot.run.mode === "live" && !snapshot.tasks.some(t => t.status === "waiting_human" && t.target_state === "WAIT_GATE_1"));
  const ordered = [...snapshot.decisions].sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99));
  const [selected, setSelected] = useState(ordered[0]?.candidate_id ?? "");
  const current = snapshot.evidence.find(e => e.candidate_id === selected);
  const move = (decision: Decision, delta: number) => { const picks = ordered.filter(d => d.selected); const index = picks.findIndex(d => d.candidate_id === decision.candidate_id); const target = index + delta; if (target < 0 || target >= picks.length) return; [picks[index], picks[target]] = [picks[target], picks[index]]; void action("reorder", { candidate_ids: picks.map(d => d.candidate_id) }); };
  return <div className="curation-layout">
    <section className="candidate-queue"><div className="section-heading"><div><h2>选题</h2></div><span className="queue-count">留 {ordered.filter(d => d.selected).length} 条</span></div>
      <div className="candidate-list">{ordered.map(decision => <button key={decision.candidate_id} className={`candidate-row ${selected === decision.candidate_id ? "active" : ""} ${decision.selected ? "picked" : "rejected"}`} onClick={() => setSelected(decision.candidate_id)}><span className="rank">{decision.rank ? String(decision.rank).padStart(2,"0") : "—"}</span><div className="candidate-copy"><strong>{decision.decision_summary}</strong></div><div className="candidate-actions">{decision.selected && !readOnly && <><span onClick={e => {e.stopPropagation(); move(decision,-1);}}><ArrowUp size={14}/></span><span onClick={e => {e.stopPropagation(); move(decision,1);}}><ArrowDown size={14}/></span></>}<span className={`pick-indicator ${decision.selected ? "on" : ""}`}>{decision.selected ? <Check size={14}/> : <X size={14}/>}</span></div></button>)}</div>
    </section>
    <EvidenceInspector readOnly={readOnly} pack={current} decision={ordered.find(d => d.candidate_id === selected)} action={action}/>
  </div>;
}

function EvidenceInspector({ pack, decision, action, readOnly = false }: { readOnly?: boolean; pack?: EvidencePack; decision?: Decision; action: (name: string, payload?: Record<string, unknown>) => Promise<void> }) {
  const canWrite = useSession().role !== "viewer" && !readOnly;
  if (!pack || !decision) return <aside className="inspector"><div className="quiet-state">点左边一条看原文</div></aside>;
  return <aside className="inspector evidence-inspector"><h2>原文</h2>
    <div className="claim-list">{pack.claims.map(claim => <div key={claim.claim_id}><p>{claim.normalized_claim}</p></div>)}</div>
    {pack.sources.map(source => <a className="source-block" key={source.source_id} href={source.url} target="_blank" rel="noreferrer"><strong>{source.title}</strong><p>{source.excerpt}</p></a>)}
    <div className="inspector-actions"><button disabled={!canWrite} onClick={() => action(decision.selected ? "reject_candidate" : "approve_candidate", {candidate_id:decision.candidate_id})}>{decision.selected ? <X size={15}/> : <Check size={15}/>} {decision.selected ? "去掉" : "留下"}</button></div>
  </aside>;
}

function ScriptView({ snapshot }: { snapshot: Snapshot }) {
  const script = snapshot.documents["script.final.json"] as JsonObject | undefined;
  const segments = (script?.segments ?? []) as Array<Record<string, unknown>>;
  return <div className="editor-layout">
    <section className="script-editor"><div className="section-heading"><div><h2>口播</h2></div></div>
      {script?.hook ? <div className="hook-block"><p>{String(script.hook)}</p></div> : null}
      <div className="segments">{segments.map((segment,index) => <article className="segment" key={String(segment.segment_id ?? segment.pick_id ?? index)}><div className="segment__head"><span>{String(index+1).padStart(2,"0")}</span></div><p>{String(segment.narration)}</p></article>)}</div>
    </section>
  </div>;
}

function formatClock(seconds: unknown) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return "—";
  const mm = Math.floor(value / 60);
  const ss = (value % 60).toFixed(1).padStart(4, "0");
  return `${String(mm).padStart(2, "0")}:${ss}`;
}

function QCLab({ snapshot }: { snapshot: Snapshot }) {
  const before = snapshot.documents["publish_kit/qc.before.json"] as JsonObject | undefined;
  const after = snapshot.documents["publish_kit/qc.after.json"] as JsonObject | undefined;
  const issues = (before?.issues ?? []) as Array<Record<string, unknown>>;
  const duration = Number(before?.duration_seconds);
  const marker = Number(issues[0]?.timestamp_seconds);
  const checks = (after?.checks ?? before?.checks ?? {}) as Record<string, boolean>;
  const structureOk = Boolean(checks.files_and_duration ?? after?.ok);
  const resolution = checks.vertical_video ? "1080×1920" : snapshot.run.mode === "demo" && after?.ok ? "1080×1920" : "待检查";
  return <div className="qc-layout">
    <section className="viewer-panel"><div className="video-stage"><ReviewPlayer video={snapshot.media.video} cover={snapshot.media.cover}/></div><div className="playback-strip"><span aria-hidden="true"><Film size={15}/></span><div className="timeline-track">{Number.isFinite(marker) && Number.isFinite(duration) && duration > 0 && <i style={{left:`${Math.min(marker / duration * 100, 100)}%`}}/>}<span style={{width: structureOk ? "100%" : "0%"}}/></div><time>{structureOk ? `${formatClock(marker || 0)} / ${formatClock(duration)}` : "—"}</time></div>
      <div className="metric-strip"><div><span>视频</span><strong>{snapshot.media.video ? resolution : "—"}</strong></div><div><span>时长</span><strong>{Number.isFinite(duration) ? `${duration}s` : "—"}</strong></div><div><span>结构检查</span><strong className={structureOk ? "good" : "warn"}>{structureOk ? "PASS" : "WAIT"}</strong></div><div><span>回归 QC</span><strong className={after?.ok ? "good" : "warn"}>{after?.ok ? "PASS" : "WAIT"}</strong></div></div>
    </section>
    <aside className="issue-panel"><div className="section-heading"><div><h2>成片</h2></div></div>{issues.map(issue => {
      const evidence = (issue.evidence as string[]) ?? [];
      const beforePx = evidence.find(item => item.startsWith("subtitle_bottom="))?.split("=")[1] ?? "1810";
      const patched = (issue.proposed_patch as Record<string, unknown> | undefined)?.subtitle_bottom ?? 1620;
      return <article className="quality-issue" key={String(issue.issue_id)}><div className="quality-issue__head"><Status value={String(issue.severity)}/><code>{String(issue.code)}</code><time>@ {String(issue.timestamp_seconds)}s</time></div><h3>{String(issue.description)}</h3><div className="evidence-code">{evidence.map(item => <span key={item}>{item}</span>)}</div>{Boolean(issue.auto_fixable) && <div className="repair-flow"><span className="before">{beforePx}px</span><ChevronRight size={15}/><span className="after">{String(patched)}px</span>{after?.ok ? <strong><Check size={13}/>REGRESSION PASS</strong> : <strong className="warn">WAITING</strong>}</div>}{!issue.auto_fixable && <p>需人工检查；未执行自动修复。</p>}</article>;
    })}
    </aside>
  </div>;
}

function ReviewPlayer({video,cover}:{video?:string|null;cover?:string|null}) {
  const [playing,setPlaying]=useState(false);
  if(!video)return <div className="empty-video"><Film size={34}/>等待 Producer 输出</div>;
  if(playing)return <video autoPlay controls preload="auto" src={video}/>;
  return <button className="video-poster" aria-label="播放最终成片" onClick={()=>setPlaying(true)}>{cover&&<img src={cover} alt="最终成片封面"/>}<span><Play size={22} fill="currentColor"/>播放成片</span></button>;
}

function MemoryPage() {
  const canWrite = useSession().role !== "viewer";
  const [memories, setMemories] = useState<import("./types").MemoryCandidate[]>([]);
  const [detail, setDetail] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [result, health] = await Promise.all([api.memories(), api.health()]);
      setMemories(result.items);
      setDetail(health.memory?.detail ?? "在任务详情提交反馈，由 AI 整理后自动保存。");
    } catch (cause) { setError(String(cause)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const decide = async (id: string, status: "approved" | "rejected") => {
    setBusy(id);
    setError("");
    try { await api.memoryStatus(id, status); await load(); }
    catch (cause) { setError(String(cause)); }
    finally { setBusy(""); }
  };
  return <div className="page">
    <PageHeader title="记忆" action={<button className="primary-button" disabled={loading || Boolean(busy)} onClick={() => void load()}>刷新</button>}/>
    <p className="memory-intro">{detail} 可查看提炼依据，或停用不合适的记忆。</p>
    <p className="memory-intro">已启用且经 AI 处理的选题／写稿偏好会作为新任务 API 模型的输入；规则模式不应用偏好，不保证模型始终遵从。停用只影响之后创建的任务。</p>
    {error && <div className="notice notice--error" role="alert">{error}</div>}
    <section className="work-panel">
      {loading && memories.length === 0 ? <div className="quiet-state">正在读取记忆…</div>
        : memories.length === 0 ? <div className="quiet-state"><strong>还没有记忆</strong><p>先打开一条任务，在“反馈与自动记忆”中提交建议。</p><Link to="/">查看任务</Link></div>
        : <div className="memory-list">{memories.map(memory => <article className="memory-item" key={memory.memory_id}>
          <div><Status value={memory.status}/><span className="memory-origin">{memory.ai_processed ? "AI 已整理" : "历史记录 · 未经 AI 整理"}</span></div>
          <p>{memory.content}</p>
          {memory.ai_processed && <details className="memory-evidence"><summary>查看依据</summary>
            <p>整理说明：{memory.decision_summary || "未提供"}</p>
            <blockquote>{memory.source_quote}</blockquote>
            <p>处理模型：{memory.processing_model || "未记录"}</p>
            <Link to={`/runs/${memory.run_id}`}>查看来源任务</Link>
          </details>}
          {!memory.ai_processed && memory.status === "pending" && <p>旧原文不会直接启用，请回到任务重新提交反馈进行 AI 整理。</p>}
          <footer>
            {["approved", "pending"].includes(memory.status) && <button disabled={!canWrite || Boolean(busy)} onClick={() => void decide(memory.memory_id, "rejected")}><X size={14}/>{memory.status === "pending" ? "拒绝" : "停用"}</button>}
            {memory.ai_processed && memory.status !== "approved" && <button className="approve-button" disabled={!canWrite || Boolean(busy)} onClick={() => void decide(memory.memory_id, "approved")}><Check size={14}/>启用</button>}
          </footer>
        </article>)}</div>}
    </section>
  </div>;
}

function SettingsPage() {
  const [health,setHealth] = useState<Awaited<ReturnType<typeof api.health>> | null>(null);
  useEffect(()=>{void api.health().then(setHealth)},[]);
  const live = Boolean(health?.live_ready);
  const source = health?.source_provider ?? "";
  const sourceDetail = health?.source_detail ?? (source === "public" ? "RSS · Hacker News · GitHub" : "知乎 · 等待配置");
  const localEditorial = health?.llm_provider === "local";
  const editorialReady = localEditorial || (health?.llm_provider === "api" && live);
  return <div className="page"><PageHeader title="设置"/><section className="doctor-list"><DoctorRow label="服务" detail={health?.ok ? "正常" : "检查中"} ok={Boolean(health?.ok)}/><DoctorRow label="FFmpeg" detail={health?.ffmpeg_detail ?? "检测字幕与文字滤镜"} ok={Boolean(health?.checks.ffmpeg)}/><DoctorRow label="浏览器" detail="做卡片用" ok={Boolean(health?.checks.browser)}/><DoctorRow label="内容数据源" detail={sourceDetail} ok={live}/><DoctorRow label="内容处理" detail={localEditorial ? "本地规则 · 无需密钥" : "API 模型"} ok={editorialReady}/><DoctorRow label="自动记忆" detail={health?.memory?.detail ?? "等待检查 AI 整理配置"} ok={Boolean(health?.memory?.ready)}/></section></div>;
}

function DoctorRow({label,detail,ok}:{label:string;detail:string;ok:boolean}) { return <div className="doctor-row"><span className={`doctor-icon ${ok?"ok":"warn"}`}>{ok?<Check size={16}/>:<CircleAlert size={16}/>}</span><div><strong>{label}</strong><span>{detail}</span></div><Status value={ok?"succeeded":"warning"}/></div>; }

export function App() { return <Shell><Routes><Route path="/" element={<Dashboard/>}/><Route path="/new" element={<NewRun/>}/><Route path="/runs/:id" element={<RunWorkspace/>}/><Route path="/usage" element={<UsagePage/>}/><Route path="/platform" element={<PlatformPage/>}/><Route path="/memory" element={<MemoryPage/>}/><Route path="/settings" element={<SettingsPage/>}/></Routes></Shell>; }

function relativeTime(value:string) { const delta=Math.max(0,Date.now()-new Date(value).getTime()); if(delta<60_000)return "刚刚"; if(delta<3_600_000)return `${Math.floor(delta/60_000)} 分钟前`; return new Date(value).toLocaleDateString("zh-CN",{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}); }
