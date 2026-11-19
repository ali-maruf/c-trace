import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "@/lib/api";
import { getSession, setSession } from "@/lib/session";

afterEach(() => { vi.restoreAllMocks(); window.sessionStorage.clear(); });

function respond(status: number, body: unknown) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", "x-request-id": "req-1" } }));
}

describe("api client", () => {
  it("calls the same-origin /api prefix with the session token and org", async () => {
    setSession({ token: "tok", kind: "demo", expiresAt: Date.now() + 60_000, orgId: "org-1" });
    const f = respond(200, { ok: true });
    await expect(api("/v1/me")).resolves.toEqual({ ok: true });
    const [url, init] = f.mock.calls[0]!;
    expect(url).toBe("/api/v1/me");
    const h = new Headers((init as RequestInit).headers);
    expect(h.get("Authorization")).toBe("Bearer tok");
    expect(h.get("X-C-Trace-Org")).toBe("org-1");
  });
  it("surfaces the API's detail message and request id", async () => {
    respond(422, { detail: [{ msg: "period must be YYYY-MM" }, { msg: "quantity must be positive" }] });
    const err = (await api("/v1/x").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(err.message).toBe("period must be YYYY-MM; quantity must be positive");
    expect(err.requestId).toBe("req-1");
  });
  it("drops the session on 401", async () => {
    setSession({ token: "tok", kind: "demo", expiresAt: Date.now() + 60_000 });
    respond(401, { detail: "Token expired" });
    await api("/v1/me").catch(() => undefined);
    expect(getSession()).toBeNull();
  });
  it("treats an expired session as signed out", () => {
    setSession({ token: "tok", kind: "demo", expiresAt: Date.now() - 1 });
    expect(getSession()).toBeNull();
  });
});
