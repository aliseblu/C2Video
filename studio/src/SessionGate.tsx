import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api } from "./api";

type Session = { required: boolean; role: "operator" | "viewer" | null; logout: () => Promise<void> };
const SessionContext = createContext<Session>({ required: false, role: "operator", logout: async () => {} });
export const useSession = () => useContext(SessionContext);

export function SessionGate({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<{ required: boolean; role: "operator" | "viewer" | null } | null>(null);
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    setError("");
    try { setSession(await api.session()); }
    catch (cause) { setError(String(cause)); }
  }, []);
  useEffect(() => {
    void load();
    const expired = () => setSession({ required: true, role: null });
    window.addEventListener("c2video:unauthorized", expired);
    return () => window.removeEventListener("c2video:unauthorized", expired);
  }, [load]);
  const logout = async () => {
    try { await api.logout(); setSession({ required: true, role: null }); }
    catch (cause) { setError(String(cause)); }
  };
  if (session && (!session.required || session.role)) {
    return <SessionContext.Provider value={{ ...session, logout }}>
      {error && <div role="alert" className="notice notice--error">{error}</div>}
      {children}
    </SessionContext.Provider>;
  }
  return <div className="auth-page"><section className="auth-card">
    <div className="brand"><span className="brand__mark">C2</span><strong>C2Video Studio</strong></div>
    <h1>{session ? "登录工作空间" : "连接工作空间"}</h1>
    <p>单机团队工作空间。操作员可以执行任务，只读角色可以查看进度与产物。</p>
    {error && <div role="alert" className="notice notice--error">{error}</div>}
    {session ? <form onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError("");
      try { const result = await api.login(token); setToken(""); setSession({ required: true, role: result.role }); }
      catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
      finally { setBusy(false); }
    }}>
      <label htmlFor="studio-token">访问密钥</label>
      <input id="studio-token" type="password" autoComplete="current-password" value={token}
        maxLength={512} onChange={e => setToken(e.target.value)} disabled={busy}/>
      <button className="primary-button" disabled={busy || !token}>{busy ? "正在登录…" : "登录"}</button>
      <p>密钥不保存在浏览器本地存储中。共享电脑使用后请退出。</p>
    </form> : <button className="primary-button" onClick={() => void load()}>重新连接</button>}
  </section></div>;
}
