import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import { PlatformPage } from "./PlatformPage";
import { SessionGate, useSession } from "./SessionGate";
import { jsonResponse } from "./test/json";

beforeEach(() => { vi.stubGlobal("fetch", vi.fn()); window.localStorage.clear(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function SessionProbe() {
  const session = useSession();
  return <div><p>{session.role}</p><button onClick={() => void session.logout()}>退出</button></div>;
}

test("login hides protected UI, never persists keys, and logout clears the session", async () => {
  vi.mocked(fetch).mockImplementation(async (path) => jsonResponse(
    String(path).endsWith("/status") ? { required: true, role: null } : { role: "operator" }));
  render(<SessionGate><SessionProbe/></SessionGate>);
  expect(await screen.findByRole("heading", { name: "登录工作空间" })).toBeInTheDocument();
  expect(screen.queryByText("operator")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("访问密钥"), { target: { value: "a".repeat(32) } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  expect(await screen.findByText("operator")).toBeInTheDocument();
  expect(window.localStorage.length).toBe(0);
  fireEvent.click(screen.getByRole("button", { name: "退出" }));
  expect(await screen.findByLabelText("访问密钥")).toHaveValue("");
});

test("session expiry removes protected UI", async () => {
  vi.mocked(fetch).mockResolvedValue(jsonResponse({ required: true, role: "operator" }));
  render(<SessionGate><SessionProbe/></SessionGate>);
  await screen.findByText("operator");
  act(() => window.dispatchEvent(new Event("c2video:unauthorized")));
  expect(screen.getByRole("heading", { name: "登录工作空间" })).toBeInTheDocument();
  expect(screen.queryByText("operator")).not.toBeInTheDocument();
});

test("viewer cannot submit a new run", async () => {
  vi.mocked(fetch).mockImplementation(async path => jsonResponse(
    String(path).includes("/auth/") ? { required: true, role: "viewer" } : { live_ready: true }));
  render(<MemoryRouter initialEntries={["/new"]}><SessionGate><App/></SessionGate></MemoryRouter>);
  expect(await screen.findByRole("button", { name: "做成片" })).toBeDisabled();
  expect(screen.getByRole("note")).toHaveTextContent("只读");
});

test("new runs default to two gates and retry reuses the idempotency key", async () => {
  const requests: RequestInit[] = [];
  vi.mocked(fetch).mockImplementation(async (_path, init) => {
    if (init?.method === "POST") {
      requests.push(init);
      return jsonResponse({ detail: "temporary" }, 503);
    }
    return jsonResponse({ live_ready: true });
  });
  render(<MemoryRouter initialEntries={["/new"]}><App/></MemoryRouter>);
  expect(screen.getByLabelText("审核方式")).toHaveValue("supervised");
  const submit = screen.getByRole("button", { name: "做成片" });
  await waitFor(() => expect(submit).toBeEnabled());
  fireEvent.click(submit);
  await screen.findByRole("alert");
  await waitFor(() => expect(submit).toBeEnabled());
  fireEvent.click(submit);
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(new Headers(requests[0].headers).get("Idempotency-Key")).toBeTruthy();
  expect(new Headers(requests[1].headers).get("Idempotency-Key")).toBe(
    new Headers(requests[0].headers).get("Idempotency-Key"));
  expect(JSON.parse(String(requests[0].body)).autonomy).toBe("supervised");
});

test("offline worker explains persistent queue instead of claiming execution", async () => {
  vi.mocked(fetch).mockResolvedValue(jsonResponse({
    worker_ready: false, worker_mode: "external", concurrency_limit: 1,
    authentication_enabled: true, environment: "production", version: "0.3.0",
    counts: { queued: 2 }, recent: [],
  }));
  render(<MemoryRouter><PlatformPage/></MemoryRouter>);
  expect(await screen.findByText("离线")).toBeInTheDocument();
  expect(screen.getByText("2 排队中")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("保留在队列中");
});

test("login failure remains recoverable without showing the workspace", async () => {
  vi.mocked(fetch).mockImplementation(async path => String(path).endsWith("/status")
    ? jsonResponse({ required: true, role: null }) : jsonResponse({ detail: "访问密钥不正确" }, 401));
  render(<SessionGate><SessionProbe/></SessionGate>);
  fireEvent.change(await screen.findByLabelText("访问密钥"), { target: { value: "bad" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("访问密钥不正确");
  expect(screen.queryByText("operator")).not.toBeInTheDocument();
});
