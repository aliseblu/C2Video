import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import { jsonResponse } from "./test/json";
import { FeedbackForm } from "./FeedbackForm";

beforeEach(() => { vi.stubGlobal("fetch", vi.fn()); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("feedback waits for AI processing and displays the backend outcome", async () => {
  let finish!: (value: Response) => void;
  vi.mocked(fetch).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  render(<MemoryRouter><FeedbackForm runId="run_test"/></MemoryRouter>);
  expect(screen.getByRole("button", { name: "提交反馈" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("这次有什么建议？"), { target: { value: "以后口播简洁克制" } });
  fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));
  expect(screen.getByRole("button", { name: "AI 正在整理…" })).toBeDisabled();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/api/runs/run_test/feedback");
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual({
    category: "preference", comment: "以后口播简洁克制",
  });
  finish(jsonResponse({ memory_processing: {
    status: "stored", message: "AI 已整理并自动记住 1 条长期偏好。", memory_ids: ["m1"],
  } }));
  expect(await screen.findByRole("status")).toHaveTextContent("自动记住 1 条");
  expect(screen.getByLabelText("这次有什么建议？")).toHaveValue("");
  expect(screen.getByRole("link", { name: "查看记忆" })).toHaveAttribute("href", "/memory");
});

test.each(["unavailable", "failed", "skipped"])("does not pretend %s means memory was saved", async status => {
  vi.mocked(fetch).mockImplementation(async () => jsonResponse({
    memory_processing: { status, message: "反馈已保存；未写入新记忆。", memory_ids: [] },
  }));
  render(<MemoryRouter><FeedbackForm runId="run_test"/></MemoryRouter>);
  fireEvent.change(screen.getByLabelText("这次有什么建议？"), { target: { value: "这一条开头再短一点" } });
  fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));
  expect(await screen.findByRole("status")).toHaveTextContent("未写入新记忆");
  expect(screen.queryByText(/自动记住 \d 条/)).not.toBeInTheDocument();
});

test.each([
  ["model_http_error", "AI 模型接口返回错误（HTTP 429）"],
  ["model_timeout", "AI 整理超时"],
  ["invalid_json", "AI 返回内容不是有效 JSON"],
  ["schema_invalid", "AI 返回的记忆字段或类型不符合要求"],
  ["memory_write_failed", "AI 整理及校验已完成，但记忆数据库保存失败"],
])("shows the specific %s memory failure without resubmitting", async (code, detail) => {
  vi.mocked(fetch).mockImplementation(async () => jsonResponse({
    feedback_id: "feedback_test", memory_id: null, memory_ids: [],
    memory_processing: {
      status: "failed", message: `反馈已保存；${detail}；未写入新记忆。`, memory_ids: [],
      diagnostic: { diagnostic_id: "memory_error_test", code, validation_issues: [] },
    },
  }));
  render(<MemoryRouter><FeedbackForm runId="run_test"/></MemoryRouter>);
  fireEvent.change(screen.getByLabelText("这次有什么建议？"), { target: { value: "以后口播保持简洁" } });
  fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));
  expect(await screen.findByRole("status")).toHaveTextContent(detail);
  expect(screen.getByRole("status")).toHaveTextContent("反馈已保存");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByText(/自动记住 \d 条/)).not.toBeInTheDocument();
  expect(screen.getByLabelText("这次有什么建议？")).toHaveValue("");
  expect(fetch).toHaveBeenCalledTimes(1);
});

test("HTTP failure preserves feedback and allows retry", async () => {
  vi.mocked(fetch).mockImplementation(async () => jsonResponse({ detail: "连接暂时不可用" }, 503));
  render(<MemoryRouter><FeedbackForm runId="run_test"/></MemoryRouter>);
  fireEvent.change(screen.getByLabelText("这次有什么建议？"), { target: { value: "以后保留官方来源" } });
  fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("内容已保留");
  expect(screen.getByLabelText("这次有什么建议？")).toHaveValue("以后保留官方来源");
  expect(screen.getByRole("button", { name: "提交反馈" })).toBeEnabled();
});

test("memory page shows provenance, disables memories, and does not approve raw legacy entries", async () => {
  const ai = { memory_id: "m1", run_id: "run_test", status: "approved", content: "口播简洁克制",
    ai_processed: true, processing_model: "frozen-test", source_quote: "以后少用夸张词",
    decision_summary: "明确的长期偏好" };
  const legacy = { memory_id: "old", run_id: "run_test", status: "pending", content: "旧原文", ai_processed: false };
  vi.mocked(fetch).mockImplementation(async (path, init) => {
    if (init?.method === "POST") {
      expect(path).toBe("/api/memories/m1");
      expect(JSON.parse(String(init.body))).toEqual({ status: "rejected" });
      ai.status = "rejected";
      return jsonResponse(ai);
    }
    return jsonResponse(path === "/api/health"
      ? { memory: { ready: true, detail: "模型已配置" } }
      : { items: [ai, legacy] });
  });
  render(<MemoryRouter initialEntries={["/memory"]}><App/></MemoryRouter>);
  expect(await screen.findByText("AI 已整理")).toBeInTheDocument();
  expect(screen.getByText("历史记录 · 未经 AI 整理")).toBeInTheDocument();
  expect(screen.getByText(/作为新任务 API 模型的输入/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "启用" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "停用" }));
  await waitFor(() => expect(screen.getByText("已停用")).toBeInTheDocument());
  expect(screen.getByRole("button", { name: "启用" })).toBeEnabled();
});

test("memory loading failure is visible and retryable", async () => {
  vi.mocked(fetch).mockRejectedValue(new Error("网络不可用"));
  render(<MemoryRouter initialEntries={["/memory"]}><App/></MemoryRouter>);
  expect(await screen.findByRole("alert")).toHaveTextContent("网络不可用");
  expect(screen.getByRole("button", { name: "刷新" })).toBeEnabled();
});
