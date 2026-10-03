import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api } from "./api";
import { jsonResponse } from "./test/json";

beforeEach(() => { vi.stubGlobal("fetch", vi.fn()); });
afterEach(() => { vi.unstubAllGlobals(); });

test("reads JSON responses and requests JSON explicitly", async () => {
  vi.mocked(fetch).mockImplementation(async () => jsonResponse({ ok: true }));
  await expect(api.health()).resolves.toEqual({ ok: true });
  expect(fetch).toHaveBeenCalledWith("/api/health", expect.objectContaining({
    headers: expect.objectContaining({ Accept: "application/json" }),
  }));
});

test.each([200, 404, 502])("explains HTML responses even with HTTP %s", async status => {
  vi.mocked(fetch).mockImplementation(async () => new Response("<!doctype html><title>PRIVATE</title>", {
    status, headers: { "Content-Type": "text/html; charset=utf-8" },
  }));
  const error = await api.usage(7).catch(error => error);
  expect(error).toBeInstanceOf(Error);
  expect(error.message).toContain("/api/usage?days=7");
  expect(error.message).toContain(`HTTP ${status}`);
  expect(error.message).toContain("重启 C2Video Studio");
  expect(error.message).not.toContain("SyntaxError");
  expect(error.message).not.toContain("PRIVATE");
});

test("explains missing JSON content type without blindly parsing the body", async () => {
  vi.mocked(fetch).mockImplementation(async () => new Response("{}"));
  await expect(api.health()).rejects.toThrow("非 JSON");
});

test("explains invalid JSON even if the server labels it as JSON", async () => {
  vi.mocked(fetch).mockImplementation(async () => new Response("<!doctype html>", {
    headers: { "Content-Type": "application/json" },
  }));
  await expect(api.health()).rejects.toThrow("不是有效的 JSON");
});

test("preserves JSON HTTP error messages", async () => {
  vi.mocked(fetch).mockImplementation(async () => jsonResponse({ detail: "接口不存在" }, 404));
  await expect(api.usage(7)).rejects.toThrow("接口不存在");
});

test.each([null, { detail: [{ msg: "invalid" }] }])("handles non-string HTTP error details: %j", async body => {
  vi.mocked(fetch).mockImplementation(async () => jsonResponse(body, 422));
  await expect(api.health()).rejects.toThrow("HTTP 422");
});

test("accepts application problem+json error responses", async () => {
  vi.mocked(fetch).mockImplementation(async () => new Response('{"detail":"暂时不可用"}', {
    status: 503, headers: { "Content-Type": "application/problem+json; charset=utf-8" },
  }));
  await expect(api.health()).rejects.toThrow("暂时不可用");
});

test("does not retry failed writes automatically", async () => {
  vi.mocked(fetch).mockImplementation(async () => new Response("<!doctype html>", {
    headers: { "Content-Type": "text/html" },
  }));
  await expect(api.feedback("run_test", "以后保留出处")).rejects.toThrow("重启");
  expect(fetch).toHaveBeenCalledTimes(1);
});
