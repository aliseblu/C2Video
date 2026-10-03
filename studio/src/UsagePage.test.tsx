import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { UsagePage } from "./UsagePage";
import { jsonResponse } from "./test/json";

beforeEach(() => { vi.stubGlobal("fetch", vi.fn()); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function fixture(calls = 2) {
  const totals = { calls, failed_calls: calls ? 1 : 0, canceled_calls: 0,
    unknown_usage_calls: calls ? 1 : 0, unpriced_calls: calls ? 1 : 0,
    input_tokens: calls ? 120 : null, output_tokens: calls ? 48 : null,
    total_tokens: calls ? 168 : null, cached_input_tokens: null,
    reported_cost_usd: null, estimated_cost_usd: calls ? 0.002 : null,
    average_latency_ms: calls ? 180 : null };
  return { days: 7, timezone: "UTC", generated_at: "2026-09-15T10:00:00Z", totals,
    models: calls ? [{ ...totals, model: "test-model" }] : [],
    purposes: calls ? [{ ...totals, purpose: "memory" }] : [],
    daily: [{ date: "2026-09-15", calls, total_tokens: totals.total_tokens, unknown_usage_calls: totals.unknown_usage_calls }],
    recent: calls ? [{ usage_id: "call1", created_at: "2026-09-15T10:00:00Z", model: "test-model",
      purpose: "memory", status: "succeeded", run_id: "run_test", total_tokens: 168,
      latency_ms: 180, error_type: null }] : [],
    runs: { COMPLETE: 1, FAILED: 1 }, memories: { approved_now: 3, created_in_period: 2 }, feedback_count: 4,
  };
}

test("shows recorded tokens, unknown coverage, costs, and attribution", async () => {
  vi.mocked(fetch).mockImplementation(async () => jsonResponse(fixture()));
  render(<MemoryRouter><UsagePage/></MemoryRouter>);
  expect(await screen.findByText("已报告 Token")).toBeInTheDocument();
  expect(screen.getByRole("note")).toHaveTextContent("1 次请求未返回完整 Token 用量");
  expect(screen.getByRole("note")).toHaveTextContent("1 次费用未知");
  expect(screen.getByText("未提供")).toBeInTheDocument();
  expect(screen.getByText("US$ 0.002000")).toBeInTheDocument();
  expect(screen.getAllByText("记忆整理").length).toBeGreaterThan(0);
  expect(screen.getByRole("link", { name: "查看任务" })).toHaveAttribute("href", "/runs/run_test");
  expect(screen.getByRole("img", { name: /Token 趋势/ })).toBeInTheDocument();
});

test("empty usage has no invented model records or zero token claim", async () => {
  vi.mocked(fetch).mockImplementation(async () => jsonResponse(fixture(0)));
  render(<MemoryRouter><UsagePage/></MemoryRouter>);
  expect(await screen.findByText("这段时间还没有模型调用记录")).toBeInTheDocument();
  expect(screen.queryByText("test-model")).not.toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

test("range selection sends a new bounded query", async () => {
  vi.mocked(fetch).mockImplementation(async () => jsonResponse(fixture()));
  render(<MemoryRouter><UsagePage/></MemoryRouter>);
  await screen.findByText("已报告 Token");
  fireEvent.change(screen.getByLabelText("统计范围"), { target: { value: "30" } });
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/usage?days=30", expect.anything()));
});

test("failed loading offers retry rather than fake zeros", async () => {
  vi.mocked(fetch).mockRejectedValueOnce(new Error("读取失败"));
  render(<MemoryRouter><UsagePage/></MemoryRouter>);
  expect(await screen.findByRole("alert")).toHaveTextContent("读取失败");
  expect(screen.queryByText("已报告 Token")).not.toBeInTheDocument();
  vi.mocked(fetch).mockImplementation(async () => jsonResponse(fixture(0)));
  fireEvent.click(screen.getByRole("button", { name: "刷新" }));
  expect(await screen.findByText("这段时间还没有模型调用记录")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});


test("old backend HTML shows restart instructions and recovers after retry", async () => {
  vi.mocked(fetch).mockImplementation(async () => new Response("<!doctype html>", {
    headers: { "Content-Type": "text/html" },
  }));
  render(<MemoryRouter><UsagePage/></MemoryRouter>);
  expect(await screen.findByRole("alert")).toHaveTextContent("重启 C2Video Studio");
  expect(screen.getByRole("alert")).toHaveTextContent("/api/usage?days=7");
  expect(screen.queryByText("已报告 Token")).not.toBeInTheDocument();
  vi.mocked(fetch).mockImplementation(async () => jsonResponse(fixture(0)));
  fireEvent.click(screen.getByRole("button", { name: "刷新" }));
  expect(await screen.findByText("这段时间还没有模型调用记录")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
