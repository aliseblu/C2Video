import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "./api";

export function FeedbackForm({ runId }: { runId: string }) {
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy || !comment.trim()) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await api.feedback(runId, comment.trim());
      setMessage(result.memory_processing.message);
      setComment("");
    } catch (cause) {
      setError(`反馈提交失败：${String(cause)}。内容已保留，可以重试。`);
    } finally {
      setBusy(false);
    }
  };

  return <section className="feedback-panel" aria-labelledby="feedback-heading">
    <h2 id="feedback-heading">反馈与自动记忆</h2>
    <p>提交反馈后，AI 会提炼明确的长期创作偏好并自动保存；临时要求不应记住。
      反馈与近期记忆会交给已配置的模型处理，可能产生调用费用，请勿填写敏感信息。</p>
    <form onSubmit={submit}>
      <label htmlFor="run-feedback">这次有什么建议？</label>
      <textarea id="run-feedback" value={comment} onChange={event => setComment(event.target.value)}
        maxLength={4000} rows={3} disabled={busy} required
        placeholder="例如：以后优先选官方来源，口播少用夸张词。这一条的开头再短一点。"/>
      <div className="feedback-actions">
        <span>{comment.length}/4000</span>
        <button className="primary-button" type="submit" disabled={busy || !comment.trim()}>
          {busy ? "AI 正在整理…" : "提交反馈"}
        </button>
        <Link to="/memory">查看记忆</Link>
      </div>
    </form>
    {message && <p className="feedback-result" role="status">{message}</p>}
    {error && <p className="notice notice--error" role="alert">{error}</p>}
  </section>;
}
