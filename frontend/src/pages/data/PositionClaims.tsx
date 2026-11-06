/**
 * Blocked Entries — the unresolved position claims that stop the desk entering a ticker.
 *
 * Before an order goes out the desk records a claim on that ticker so it cannot open the
 * same position twice. A claim left `submitted` or `unknown` means the order's fate was
 * never confirmed, and it blocks new entries on that ticker until something establishes
 * what became of the order. IBKR cannot answer for equity orders (the desk sends no
 * client order id with them), so those never clear on their own.
 *
 * Releasing a claim re-opens the ticker to a new entry. That is only safe when no order
 * and no position exists at the broker, so the release here is deliberately slow:
 *
 *   - ONE claim at a time. There is no "release all".
 *   - A reason is required (the server records it with who released it).
 *   - The operator must confirm they checked IBKR's Orders and Portfolio.
 *   - If IBKR currently holds the ticker, or the position read failed, the operator must
 *     also type the ticker. A failed read is never treated as "not held".
 *
 * A failed read never looks like an empty list: if the claims cannot be loaded this says
 * so, and after a refresh failure the last rows stay on screen marked stale.
 */
import React, { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "../../api/client";
import { Button, Panel } from "../../components/ui";
import type { PositionClaim, PositionClaimsResponse } from "../../types/positionClaims";

const MIN_REASON = 3;
const MAX_REASON = 200;

type BrokerView = "held" | "not-held" | "unknown";

function ageOf(iso: string | null, now: number): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

function ageOfMs(ms: number | null, now: number): string {
  return ms === null ? "—" : ageOf(new Date(ms).toISOString(), now);
}

function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "unknown error";
}

export default function PositionClaims() {
  const [claims, setClaims] = useState<PositionClaim[] | null>(null);   // null = never loaded
  const [loadedAt, setLoadedAt] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // symbol -> signed quantity at the broker; null = the position read failed
  const [held, setHeld] = useState<Map<string, number> | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [loading, setLoading] = useState(false);

  const [openToken, setOpenToken] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [checked, setChecked] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [releaseError, setReleaseError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const [claimsRes, posRes] = await Promise.allSettled([
      api.getPositionClaims() as Promise<PositionClaimsResponse>,
      api.getPositions() as Promise<{ positions?: Array<{ symbol?: string; quantity?: number }> }>,
    ]);
    if (claimsRes.status === "fulfilled" && Array.isArray(claimsRes.value?.claims)) {
      setClaims(claimsRes.value.claims);
      setLoadedAt(Date.now());
      setLoadError(null);
    } else {
      setLoadError(describeError(claimsRes.status === "rejected" ? claimsRes.reason : "unexpected response"));
    }
    if (posRes.status === "fulfilled" && Array.isArray(posRes.value?.positions)) {
      const m = new Map<string, number>();
      for (const p of posRes.value.positions) {
        if (p.symbol) m.set(String(p.symbol).toUpperCase(), Number(p.quantity ?? 0));
      }
      setHeld(m);
    } else {
      setHeld(null);
    }
    setNow(Date.now());
    setLoading(false);
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);

  const brokerView = (symbol: string): BrokerView => {
    if (held === null) return "unknown";
    const q = held.get(symbol.toUpperCase());
    return q !== undefined && q !== 0 ? "held" : "not-held";
  };

  const open = (token: string) => {
    setOpenToken(token);
    setReason("");
    setChecked(false);
    setTyped("");
    setReleaseError(null);
    setNotice(null);
  };
  const close = () => { setOpenToken(null); setReleaseError(null); };

  const release = async (claim: PositionClaim) => {
    setBusy(true);
    setReleaseError(null);
    try {
      await api.releasePositionClaim(claim.claim_token, reason.trim());
      setNotice(`Released the claim on ${claim.underlying}. The desk may enter it again.`);
      setOpenToken(null);
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setReleaseError("That claim no longer exists (already released?). Refreshing the list.");
        await load();
      } else {
        setReleaseError(`Release failed, nothing was changed: ${describeError(err)}`);
      }
    } finally {
      setBusy(false);
    }
  };

  const stale = loadError !== null && claims !== null;

  return (
    <Panel title="Blocked entries">
      <p style={{ fontSize: 12, color: "var(--ink-dim)", margin: "0 0 12px", lineHeight: 1.5 }}>
        Each row below stops the desk from entering that ticker until it is known what happened to the
        order it sent. IBKR cannot answer for equity orders, so these do not clear on their own.
        Release one only after checking IBKR: <strong>Orders and Portfolio</strong> must show no open
        order and no position for it. Releasing lets the desk enter the ticker again.
      </p>

      {notice && (
        <div role="status" style={{ padding: "8px 10px", marginBottom: 10, border: "1px solid var(--green)", color: "var(--green)", fontSize: 12 }}>
          {notice}
        </div>
      )}

      {loadError !== null && (
        <div role="alert" style={{ padding: "8px 10px", marginBottom: 10, border: "1px solid var(--amber)", color: "var(--amber)", fontSize: 12 }}>
          {stale
            ? `Could not refresh (${loadError}). Showing the list as it was ${ageOfMs(loadedAt, now)} ago, which may be out of date.`
            : `Could not load blocked entries: ${loadError}. This is not an empty list; the read failed.`}
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
          {claims === null ? (loading ? "Loading…" : "—") : `${claims.length} blocked`}
        </span>
        {loadedAt !== null && (
          <span style={{ fontSize: 11, color: "var(--ink-faint)" }}>Read {ageOfMs(loadedAt, now)} ago</span>
        )}
        <Button size="sm" type="button" onClick={() => void load()} disabled={loading} style={{ marginLeft: "auto" }}>
          {loading ? "Refreshing…" : "Refresh"}
        </Button>
      </div>

      {claims !== null && claims.length === 0 && loadError === null && (
        <p style={{ fontSize: 12, color: "var(--ink-dim)" }}>No blocked entries. Nothing is waiting on a release.</p>
      )}

      {claims !== null && claims.length > 0 && (
        <table aria-label="Blocked entries" style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ textAlign: "left", color: "var(--ink-dim)" }}>
              {["Symbol", "Class", "State", "Age", "Reason", "IBKR position", ""].map((h, i) => (
                <th key={i} style={{ padding: "6px 8px", fontWeight: 600 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {claims.map((c) => {
              const view = brokerView(c.underlying);
              const isOpen = openToken === c.claim_token;
              const needTyped = view !== "not-held";
              const typedOk = !needTyped || typed.trim().toUpperCase() === c.underlying.toUpperCase();
              const canRelease = reason.trim().length >= MIN_REASON && checked && typedOk && !busy;
              return (
                <React.Fragment key={c.claim_token}>
                  <tr style={{ borderTop: "1px solid var(--line-dim)" }}>
                    <td style={{ padding: "8px", fontFamily: "var(--mono)", fontWeight: 700 }}>{c.underlying}</td>
                    <td style={{ padding: "8px" }}>{c.asset_class}</td>
                    <td style={{ padding: "8px", fontFamily: "var(--mono)" }}>{c.state}</td>
                    <td style={{ padding: "8px" }}>{ageOf(c.submitted_at || c.claimed_at, now)}</td>
                    <td style={{ padding: "8px", color: "var(--ink-dim)" }}>{c.unresolved_reason || "—"}</td>
                    <td style={{ padding: "8px", color: view === "held" ? "var(--amber)" : "var(--ink)" }}>
                      {view === "held" ? `Held (${held?.get(c.underlying.toUpperCase())})`
                        : view === "not-held" ? "Not held" : "Could not be read"}
                    </td>
                    <td style={{ padding: "8px", textAlign: "right" }}>
                      <Button size="sm" type="button" aria-label={`Release claim for ${c.underlying}`}
                              onClick={() => (isOpen ? close() : open(c.claim_token))}>
                        {isOpen ? "Cancel" : "Release…"}
                      </Button>
                    </td>
                  </tr>
                  {isOpen && (
                    <tr>
                      <td colSpan={7} style={{ padding: "10px 8px 14px", background: "var(--bg-3)" }}>
                        <div role="group" aria-label={`Release the claim on ${c.underlying}`} style={{ display: "grid", gap: 10, maxWidth: 560 }}>
                          <strong>Release the claim on {c.underlying} ({c.asset_class})?</strong>
                          <span style={{ color: "var(--ink-dim)" }}>
                            The desk will be allowed to enter {c.underlying} again. Do this only if IBKR shows no open
                            order and no position for it. This page cannot see open orders, only positions.
                          </span>
                          {view === "held" && (
                            <span role="alert" style={{ color: "var(--amber)" }}>
                              IBKR currently holds {c.underlying} ({held?.get(c.underlying.toUpperCase())}). Releasing is
                              usually wrong while a position exists.
                            </span>
                          )}
                          {view === "unknown" && (
                            <span role="alert" style={{ color: "var(--amber)" }}>
                              The position read failed, so it is not known whether IBKR holds {c.underlying}. Check IBKR directly.
                            </span>
                          )}
                          <label style={{ display: "grid", gap: 4 }}>
                            Why is it safe to release? (recorded with your name, {MIN_REASON}–{MAX_REASON} characters)
                            <input type="text" value={reason} maxLength={MAX_REASON}
                                   onChange={(e) => setReason(e.target.value)} />
                          </label>
                          <label style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                            <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
                            <span>I checked IBKR Orders and Portfolio for {c.underlying}: no open order, no position.</span>
                          </label>
                          {needTyped && (
                            <label style={{ display: "grid", gap: 4 }}>
                              Type {c.underlying} to confirm
                              <input type="text" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
                            </label>
                          )}
                          {releaseError && <span role="alert" style={{ color: "var(--red)" }}>{releaseError}</span>}
                          <div style={{ display: "flex", gap: 8 }}>
                            <Button danger type="button" disabled={!canRelease} onClick={() => void release(c)}>
                              {busy ? "Releasing…" : "Release claim"}
                            </Button>
                            <Button type="button" onClick={close} disabled={busy}>Cancel</Button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
