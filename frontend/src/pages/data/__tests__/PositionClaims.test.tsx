import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import PositionClaims from "../PositionClaims";
import { api, ApiError } from "../../../api/client";

vi.mock("../../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../../api/client")>("../../../api/client");
  return { ...actual, api: { getPositionClaims: vi.fn(), getPositions: vi.fn(), releasePositionClaim: vi.fn() } };
});

const NOW = Date.parse("2026-11-06T18:00:00Z");
const claim = (symbol: string, token: string, extra: object = {}) => ({
  claim_token: token, scope: "s", underlying: symbol, asset_class: "equity", state: "submitted",
  idempotency_key: `oc-${token}`, dispatch_id: null, unresolved_reason: null,
  claimed_at: "2026-11-06T15:00:00Z", submitted_at: "2026-11-06T15:00:05Z",
  releasing_allows_a_new_entry: true, ...extra,
});

function listOf(...claims: object[]) {
  vi.mocked(api.getPositionClaims).mockResolvedValue({ count: claims.length, claims } as never);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(Date, "now").mockReturnValue(NOW);
  vi.mocked(api.getPositions).mockResolvedValue({ positions: [{ symbol: "CSX", quantity: 381 }] } as never);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

async function openRelease(symbol: string) {
  fireEvent.click(await screen.findByRole("button", { name: `Release claim for ${symbol}` }));
  return screen.getByRole("group", { name: `Release the claim on ${symbol}` });
}

describe("Blocked entries: reading", () => {
  it("lists each blocked ticker with its state, age and the broker's view", async () => {
    listOf(claim("ADP", "t1"), claim("CSX", "t2", { state: "unknown", unresolved_reason: "submit timed out" }));
    render(<PositionClaims />);
    expect(await screen.findByText("2 blocked")).toBeInTheDocument();
    const rows = screen.getAllByRole("row");
    expect(within(rows[1]).getByText("ADP")).toBeInTheDocument();
    expect(within(rows[1]).getByText("3h")).toBeInTheDocument();         // submitted_at 15:00:05, now 18:00
    expect(within(rows[1]).getByText("Not held")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Held (381)")).toBeInTheDocument();
    expect(within(rows[2]).getByText("submit timed out")).toBeInTheDocument();
  });

  it("says there is nothing blocked only after a successful read", async () => {
    listOf();
    render(<PositionClaims />);
    expect(await screen.findByText(/No blocked entries/)).toBeInTheDocument();
  });

  it("a failed read is an error, never an empty list", async () => {
    vi.mocked(api.getPositionClaims).mockRejectedValue(new ApiError(500, "500: boom"));
    render(<PositionClaims />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/Could not load blocked entries/);
    expect(screen.queryByText(/No blocked entries/)).not.toBeInTheDocument();
    expect(screen.queryByText(/0 blocked/)).not.toBeInTheDocument();
  });

  it("keeps the last rows on a failed refresh and marks them as possibly out of date", async () => {
    listOf(claim("ADP", "t1"));
    render(<PositionClaims />);
    await screen.findByText("ADP");
    vi.mocked(api.getPositionClaims).mockRejectedValue(new ApiError(503, "503: down"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/may be out of date/);
    expect(screen.getByText("ADP")).toBeInTheDocument();
  });

  it("after an empty list, a failed refresh no longer claims nothing is blocked", async () => {
    listOf();
    render(<PositionClaims />);
    await screen.findByText(/No blocked entries/);
    vi.mocked(api.getPositionClaims).mockRejectedValue(new ApiError(503, "503: down"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/may be out of date/);
    expect(screen.queryByText(/No blocked entries/)).not.toBeInTheDocument();
  });

  it("an unreadable broker position is shown as unreadable, not as not held", async () => {
    vi.mocked(api.getPositions).mockRejectedValue(new Error("gateway down"));
    listOf(claim("ADP", "t1"));
    render(<PositionClaims />);
    expect(await screen.findByText("Could not be read")).toBeInTheDocument();
    expect(screen.queryByText("Not held")).not.toBeInTheDocument();
  });

  it("offers no bulk release", async () => {
    listOf(claim("ADP", "t1"), claim("TTWO", "t2"));
    render(<PositionClaims />);
    await screen.findByText("2 blocked");
    expect(screen.queryByRole("button", { name: /release all|select all/i })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Release claim for / })).toHaveLength(2);
  });
});

describe("Blocked entries: the release gates", () => {
  it("cannot be submitted without a reason and the IBKR confirmation", async () => {
    listOf(claim("ADP", "t1"));
    render(<PositionClaims />);
    const dlg = await openRelease("ADP");
    const go = within(dlg).getByRole("button", { name: "Release claim" });
    expect(go).toBeDisabled();
    fireEvent.change(within(dlg).getByRole("textbox"), { target: { value: "ab" } });      // too short
    fireEvent.click(within(dlg).getByRole("checkbox"));
    expect(go).toBeDisabled();
    fireEvent.change(within(dlg).getByRole("textbox"), { target: { value: "abc" } });
    expect(go).toBeEnabled();
    fireEvent.click(within(dlg).getByRole("checkbox"));                                   // untick
    expect(go).toBeDisabled();
  });

  it("a whitespace-only reason does not count", async () => {
    listOf(claim("ADP", "t1"));
    render(<PositionClaims />);
    const dlg = await openRelease("ADP");
    fireEvent.change(within(dlg).getByRole("textbox"), { target: { value: "     " } });
    fireEvent.click(within(dlg).getByRole("checkbox"));
    expect(within(dlg).getByRole("button", { name: "Release claim" })).toBeDisabled();
  });

  it("a ticker IBKR still holds also needs its symbol typed", async () => {
    listOf(claim("CSX", "t2"));
    render(<PositionClaims />);
    const dlg = await openRelease("CSX");
    expect(within(dlg).getByRole("alert")).toHaveTextContent(/currently holds CSX/);
    const [reason, typed] = within(dlg).getAllByRole("textbox");
    fireEvent.change(reason, { target: { value: "checked in IBKR" } });
    fireEvent.click(within(dlg).getByRole("checkbox"));
    const go = within(dlg).getByRole("button", { name: "Release claim" });
    expect(go).toBeDisabled();
    fireEvent.change(typed, { target: { value: "csx" } });                                // case-insensitive
    expect(go).toBeEnabled();
  });

  it("an unreadable position read is treated like a held one: the symbol must be typed", async () => {
    vi.mocked(api.getPositions).mockRejectedValue(new Error("down"));
    listOf(claim("ADP", "t1"));
    render(<PositionClaims />);
    const dlg = await openRelease("ADP");
    expect(within(dlg).getByRole("alert")).toHaveTextContent(/not known whether IBKR holds ADP/);
    const [reason] = within(dlg).getAllByRole("textbox");
    fireEvent.change(reason, { target: { value: "checked in IBKR" } });
    fireEvent.click(within(dlg).getByRole("checkbox"));
    expect(within(dlg).getByRole("button", { name: "Release claim" })).toBeDisabled();
  });

  it("only one release panel is open at a time", async () => {
    listOf(claim("ADP", "t1"), claim("TTWO", "t2"));
    render(<PositionClaims />);
    await openRelease("ADP");
    fireEvent.click(screen.getByRole("button", { name: "Release claim for TTWO" }));
    expect(screen.queryByRole("group", { name: "Release the claim on ADP" })).not.toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Release the claim on TTWO" })).toBeInTheDocument();
  });
});

describe("Blocked entries: releasing", () => {
  async function fillAndSubmit(symbol: string, reasonText = "  no order or position in IBKR  ") {
    const dlg = await openRelease(symbol);
    fireEvent.change(within(dlg).getAllByRole("textbox")[0], { target: { value: reasonText } });
    fireEvent.click(within(dlg).getByRole("checkbox"));
    fireEvent.click(within(dlg).getByRole("button", { name: "Release claim" }));
  }

  it("sends that one claim's token with the trimmed reason, then reloads the list", async () => {
    listOf(claim("ADP", "t1"), claim("TTWO", "t2"));
    vi.mocked(api.releasePositionClaim).mockResolvedValue({ released: true, claim_token: "t1", operator: "x" });
    render(<PositionClaims />);
    await fillAndSubmit("ADP");
    await waitFor(() => expect(api.releasePositionClaim).toHaveBeenCalledWith("t1", "no order or position in IBKR"));
    expect(api.releasePositionClaim).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("status")).toHaveTextContent(/Released the claim on ADP/);
    expect(vi.mocked(api.getPositionClaims).mock.calls.length).toBeGreaterThanOrEqual(2);   // reloaded
  });

  it("a failed release says nothing changed and keeps the panel open", async () => {
    listOf(claim("ADP", "t1"));
    vi.mocked(api.releasePositionClaim).mockRejectedValue(new ApiError(403, "403: needs an authenticated operator"));
    render(<PositionClaims />);
    await fillAndSubmit("ADP");
    expect(await screen.findByText(/Release failed, nothing was changed: 403/)).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Release the claim on ADP" })).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("a 404 says the claim is gone and refreshes", async () => {
    listOf(claim("ADP", "t1"));
    vi.mocked(api.releasePositionClaim).mockRejectedValue(new ApiError(404, "404: No such claim."));
    render(<PositionClaims />);
    await fillAndSubmit("ADP");
    expect(await screen.findByText(/no longer exists/)).toBeInTheDocument();
    await waitFor(() => expect(vi.mocked(api.getPositionClaims).mock.calls.length).toBeGreaterThanOrEqual(2));
  });
});
