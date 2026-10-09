/**
 * What a failed mutate call tells the caller.
 *
 * This exists because of a support question that took a code read to answer:
 * "hold to close is not functioning". The hold was fine and the POST was fine;
 * it came back 403 because the operator API key lives in sessionStorage and
 * sessionStorage dies with the tab. The client turned that into
 * `Error("API error 403: Forbidden")` — a string with no status field, so no
 * call site could branch on it, and wording that names neither the cause nor
 * the fix. The UI printed it in a banner above a long table, off-screen.
 *
 * So the invariant is not "errors are thrown". It is:
 *   - the STATUS survives as a field, because the 403 branch is what makes the
 *     message actionable, and
 *   - the server's own `detail` wins over res.statusText, because "Forbidden"
 *     is not something an operator can act on.
 *
 * The X-Api-Key case at the bottom is the actual root cause, and nothing
 * covered it: the header is sent only when the key is present, which is
 * exactly how a reopened tab starts 403ing every close.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { api, ApiError, setOperatorApiKey } from "../client";

const okJson = (body: unknown) =>
  ({ ok: true, status: 200, json: async () => body }) as unknown as Response;

const failJson = (status: number, body: unknown, statusText = "") =>
  ({ ok: false, status, statusText, json: async () => body }) as unknown as Response;

const failNonJson = (status: number, statusText: string) =>
  ({
    ok: false,
    status,
    statusText,
    json: async () => { throw new SyntaxError("Unexpected token <"); },
  }) as unknown as Response;

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  sessionStorage.clear();
});
afterEach(() => { vi.unstubAllGlobals(); });

describe("a failed call keeps the status as a field", () => {
  it("carries status 403 and the server's detail, not 'Forbidden'", async () => {
    fetchMock.mockResolvedValue(
      failJson(403, { detail: "Invalid or missing API key" }, "Forbidden"),
    );

    const err: any = await api.closePosition("trade-1").then(
      () => { throw new Error("expected closePosition to reject"); },
      (e) => e as any,
    );

    expect(err).toBeInstanceOf(ApiError);
    // The field is the point: a message-substring check would pass for a
    // plain Error and leave every call site unable to branch.
    expect(err.status).toBe(403);
    // Detail AND the status. The status stays in the text because callers that
    // classify by substring (RotationReviewPanel's 403/423/409/404 branches,
    // TerminalLayout's 403) would otherwise silently fall through to generic
    // failure text — a regression this refactor introduced and review caught.
    expect(err.message).toBe("403: Invalid or missing API key");
  });

  it("falls back to statusText when the body is not JSON (nginx 502 HTML)", async () => {
    fetchMock.mockResolvedValue(failNonJson(502, "Bad Gateway"));

    const err: any = await api.closePosition("trade-1").catch((e) => e as any);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(502);
    expect(err.message).toBe("502: Bad Gateway");
    // A parse failure must not surface as a SyntaxError in place of the status.
    expect(err.name).toBe("ApiError");
  });

  it("stringifies a non-string detail rather than rendering [object Object]", async () => {
    fetchMock.mockResolvedValue(failJson(422, { detail: [{ msg: "bad id" }] }));

    const err: any = await api.closePosition("trade-1").catch((e) => e as any);

    expect(err.status).toBe(422);
    expect(err.message).toContain("bad id");
    expect(err.message).toContain("422");
    expect(err.message).not.toContain("[object Object]");
  });

  it("still returns the parsed body on success", async () => {
    fetchMock.mockResolvedValue(okJson({ status: "filled" }));
    await expect(api.closePosition("trade-1")).resolves.toEqual({ status: "filled" });
  });
});

describe("X-Api-Key is sent only when the operator key is set", () => {
  const headersOfLastCall = () =>
    new Headers(fetchMock.mock.calls[0][1].headers as HeadersInit);

  it("omits the header entirely when no key is stored — the 403 path", async () => {
    fetchMock.mockResolvedValue(okJson({ status: "filled" }));

    await api.closePosition("trade-1");

    expect(headersOfLastCall().has("X-Api-Key")).toBe(false);
  });

  it("sends the stored key when one is set", async () => {
    setOperatorApiKey("s3cret");
    fetchMock.mockResolvedValue(okJson({ status: "filled" }));

    await api.closePosition("trade-1");

    expect(headersOfLastCall().get("X-Api-Key")).toBe("s3cret");
  });

  it("stops sending it once the key is cleared", async () => {
    setOperatorApiKey("s3cret");
    setOperatorApiKey("");
    fetchMock.mockResolvedValue(okJson({ status: "filled" }));

    await api.closePosition("trade-1");

    expect(headersOfLastCall().has("X-Api-Key")).toBe(false);
  });
});
