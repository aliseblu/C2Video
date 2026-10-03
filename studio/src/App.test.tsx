import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, test, vi } from "vitest";
import { App } from "./App";
import { jsonResponse } from "./test/json";

vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ items: [] })));

test("renders the editorial control room", async () => {
  window.localStorage.clear();
  render(<MemoryRouter><App /></MemoryRouter>);
  expect(await screen.findByText("今天的生产台")).toBeInTheDocument();
  expect(screen.getByText("C2Video")).toBeInTheDocument();
  expect(screen.getByText("最近")).toBeInTheDocument();
  await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
  fireEvent.click(screen.getByRole("button", { name: "切换到深色模式" }));
  await waitFor(() => expect(document.documentElement.dataset.theme).toBe("dark"));
});

test("shows task creation errors and allows retry", async () => {
  vi.mocked(fetch).mockImplementation(async (_path, init) => {
    if (init?.method === "POST") return jsonResponse({ detail: "暂时无法创建，请重试" }, 503);
    return jsonResponse({ live_ready: true });
  });
  render(<MemoryRouter initialEntries={["/new"]}><App /></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole("button", { name: "做成片" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "做成片" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法创建，请重试");
  expect(screen.getByRole("button", { name: "做成片" })).toBeEnabled();
});

test("missing task has an error and return link instead of endless loading", async () => {
  vi.mocked(fetch).mockImplementation(async () => jsonResponse({ detail: "Run not found" }, 404));
  render(<MemoryRouter initialEntries={["/runs/missing"]}><App /></MemoryRouter>);
  expect(await screen.findByRole("alert")).toHaveTextContent("Run not found");
  expect(screen.getByRole("link", { name: "返回总览" })).toHaveAttribute("href", "/");
  expect(screen.queryByText("载入 Run…")).not.toBeInTheDocument();
});

test("shows account-free public source status", async () => {
  vi.mocked(fetch).mockImplementationOnce(async () => jsonResponse({
      ok: true,
      version: "0.2.0",
      mode: "live",
      live_ready: true,
      source_provider: "public",
      llm_provider: "local",
      source_configured: true,
      checks: { ffmpeg: true, browser: true },
  }));
  render(<MemoryRouter initialEntries={["/settings"]}><App /></MemoryRouter>);
  expect(await screen.findByText("RSS · Hacker News · GitHub")).toBeInTheDocument();
  expect(screen.getByText("本地规则 · 无需密钥")).toBeInTheDocument();
  expect(screen.getByText("内容数据源")).toBeInTheDocument();
});

test("shows missing Zhihu credentials honestly", async () => {
  vi.mocked(fetch).mockImplementationOnce(async () => jsonResponse({
    ok: true, mode: "live", live_ready: false, source_provider: "zhihu", llm_provider: "local",
    source_detail: "知乎官方接口 · 请配置 Access Secret，或在新建页面导入素材",
    checks: { ffmpeg: true, browser: true },
  }));
  render(<MemoryRouter initialEntries={["/settings"]}><App /></MemoryRouter>);
  expect(await screen.findByText(/请配置 Access Secret/)).toBeInTheDocument();
});

test("missing credentials do not silently start a demo", async () => {
  vi.mocked(fetch).mockImplementation(async () => jsonResponse({
    live_ready: false, source_provider: "zhihu", source_detail: "需要密钥或素材",
  }));
  render(<MemoryRouter initialEntries={["/new"]}><App /></MemoryRouter>);
  expect(await screen.findByText("需要密钥或素材")).toBeInTheDocument();
  expect(screen.getByRole("button", {name: "做成片"})).toBeDisabled();
  expect(screen.getByLabelText("导入知乎素材（可选，无需密钥）")).toBeInTheDocument();
});


test("imported materials can submit without credentials and request live mode", async () => {
  let submitted: Record<string, unknown> | null = null;
  vi.mocked(fetch).mockImplementation(async (_path, init) => {
    if (init?.method === "POST") {
      submitted = JSON.parse(String(init.body));
      return jsonResponse({detail: "已收到测试素材"}, 409);
    }
    return jsonResponse({live_ready: false, source_provider: "zhihu"});
  });
  render(<MemoryRouter initialEntries={["/new"]}><App /></MemoryRouter>);
  const items = [{title: "离线测试", content: "测试摘要", url: "https://www.zhihu.com/question/1"}];
  const file = new File([JSON.stringify(items)], "materials.json", {type: "application/json"});
  Object.defineProperty(file, "text", {value: async () => JSON.stringify(items)});
  fireEvent.change(screen.getByLabelText("导入知乎素材（可选，无需密钥）"), {target: {files: [file]}});
  await waitFor(() => expect(screen.getByRole("button", {name: "做成片"})).toBeEnabled());
  fireEvent.click(screen.getByRole("button", {name: "做成片"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("已收到测试素材");
  expect(submitted).toMatchObject({mode: "live", materials: items});
});
