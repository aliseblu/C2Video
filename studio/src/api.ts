import type { FeedbackResult, MemoryCandidate, RunRow, Snapshot, UsageDashboard } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const contentType = (response.headers.get("Content-Type") ?? "").split(";")[0].trim().toLowerCase();
  const location = `${path}（HTTP ${response.status}）`;
  if (contentType !== "application/json" && !/^application\/[\w.+-]+\+json$/.test(contentType)) {
    throw new Error(
      `接口 ${location} 返回了网页或非 JSON 内容，可能连接到了旧版后端或错误地址。请重启 C2Video Studio，并打开启动时显示的地址后刷新页面。`,
    );
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new Error(`接口 ${location} 返回的数据不是有效的 JSON。请重试；若刚更新过项目，请重启 C2Video Studio。`);
  }
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/auth/login") {
      window.dispatchEvent(new Event("c2video:unauthorized"));
    }
    const detail = typeof body === "object" && body !== null && "detail" in body ? body.detail : null;
    throw new Error(typeof detail === "string" && detail ? detail : `接口 ${location} 请求失败。`);
  }
  return body as T;
}

export const api = {
  session: () => request<{ required: boolean; role: "operator" | "viewer" | null; environment: string }>("/api/auth/status"),
  login: (token: string) => request<{ role: "operator" | "viewer" }>("/api/auth/login", {
    method: "POST", body: JSON.stringify({ token }),
  }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST", body: "{}" }),
  platform: () => request<{
    environment: string; version: string; authentication_enabled: boolean;
    concurrency_limit: number; worker_ready: boolean; worker_mode: string; scope: string;
    counts: Record<string, number>;
    workers: { worker_id: string; heartbeat_at: number; concurrency: number }[];
    recent: { run_id: string; status: string; requested_at: number; worker_pid: number | null; error: string | null }[];
  }>("/api/platform"),
  usage: (days: number) => request<UsageDashboard>(`/api/usage?days=${days}`),
  health: () => request<{
    ok: boolean;
    version: string;
    mode: string;
    live_ready?: boolean;
    source_provider?: string | null;
    llm_provider?: string | null;
    memory?: { enabled: boolean; ready: boolean; detail: string };
    source_configured?: boolean;
    source_mode?: string;
    source_detail?: string;
    ffmpeg_path?: string | null;
    ffprobe_path?: string | null;
    ffmpeg_detail?: string;
    checks: Record<string, boolean>;
  }>("/api/health"),
  runs: () => request<{ items: RunRow[] }>("/api/runs"),
  run: (id: string) => request<Snapshot>(`/api/runs/${id}`),
  createRun: (payload: Record<string, unknown>, idempotencyKey?: string) =>
    request<Snapshot>("/api/runs", { method: "POST", body: JSON.stringify(payload),
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {} }),
  start: (id: string, background = true) =>
    request<Snapshot | { run_id: string; worker_pid: number | null; job_status: string }>(`/api/runs/${id}/start`, {
      method: "POST",
      body: JSON.stringify({ background }),
    }),
  action: (id: string, action: string, payload: Record<string, unknown> = {}) =>
    request<Snapshot>(`/api/runs/${id}/actions`, {
      method: "POST",
      body: JSON.stringify({ action, payload }),
    }),
  replay: (id: string) => request<{ run_id: string; event_count: number }>(`/api/runs/${id}/replay`),
  feedback: (id: string, comment: string) =>
    request<FeedbackResult>(`/api/runs/${id}/feedback`, {
      method: "POST", body: JSON.stringify({ category: "preference", comment }),
    }),
  memories: () => request<{ items: MemoryCandidate[] }>("/api/memories"),
  memoryStatus: (id: string, status: "approved" | "rejected") =>
    request<MemoryCandidate>(`/api/memories/${id}`, {
      method: "POST",
      body: JSON.stringify({ status }),
    }),
};
