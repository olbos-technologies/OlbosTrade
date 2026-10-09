/**
 * Every browser currently signed in as you, and a way to kick one out.
 *
 * The routes shipped in #70 with no UI. Without this screen a session you
 * cannot reach — a shared machine, a phone you no longer have — stays valid
 * until it expires, and the only lever was changing your password to revoke
 * everything at once.
 *
 * THE CURRENT SESSION HAS NO REVOKE BUTTON. Revoking it server-side would
 * leave the cookie in place and the tab in a state where every request 401s
 * while the UI still looks signed in. Sign out is the control for that, and it
 * clears the cookie too.
 */
import React, { useCallback, useEffect, useState } from "react";
import { api, ApiError, type AccountSession } from "../../api/client";
import { Badge, Button } from "../../components/ui";

const MONO = "var(--mono)";

function when(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

/**
 * A readable name for a browser.
 *
 * Deliberately crude: a full UA parser is a dependency and a maintenance
 * burden for a label nobody automates against. The raw string is kept in the
 * title attribute so nothing is lost when the guess is wrong.
 */
function describe(ua: string | null): string {
  if (!ua) return "Unknown browser";
  const browser =
    /Edg\//.test(ua) ? "Edge" :
    /OPR\//.test(ua) ? "Opera" :
    /Chrome\//.test(ua) ? "Chrome" :
    /Safari\//.test(ua) ? "Safari" :
    /Firefox\//.test(ua) ? "Firefox" : "Browser";
  const os =
    /iPhone|iPad/.test(ua) ? "iOS" :
    /Android/.test(ua) ? "Android" :
    /Mac OS X/.test(ua) ? "macOS" :
    /Windows/.test(ua) ? "Windows" :
    /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser} on ${os}` : browser;
}

export default function AccountSessions() {
  const [sessions, setSessions] = useState<AccountSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");

  const load = useCallback(async () => {
    try {
      const d = await api.listSessions();
      setSessions(d.sessions || []);
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load your sessions.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const revoke = async (s: AccountSession) => {
    if (!window.confirm(`Sign out ${describe(s.user_agent)}? That browser will need to log in again.`)) return;
    setBusyId(s.id);
    try {
      await api.revokeSession(s.id);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not sign that session out.");
    } finally {
      setBusyId("");
    }
  };

  return (
    <div style={{ padding: 16, height: "100%", overflowY: "auto" }}>
      <div className="panel-title" style={{ marginBottom: 10 }}>Active sessions</div>

      <div style={{ maxWidth: 560, marginBottom: 14, fontFamily: MONO, fontSize: 11,
                    color: "var(--ink-faint)", lineHeight: 1.65 }}>
        Each row is a browser signed in as you. If you do not recognise one,
        sign it out and change your password.
      </div>

      <div className="instrument-card" style={{ maxWidth: 560, padding: "4px 16px" }}>
        {loading && (
          <div style={{ padding: "12px 0", fontFamily: MONO, fontSize: 11,
                        color: "var(--ink-faint)" }}>loading…</div>
        )}
        {!loading && sessions.length === 0 && !error && (
          <div style={{ padding: "12px 0", fontFamily: MONO, fontSize: 11,
                        color: "var(--ink-faint)" }}>No active sessions.</div>
        )}
        {sessions.map(s => (
          <div key={s.id} style={{
            display: "flex", alignItems: "center", gap: 12, padding: "12px 0",
            borderBottom: "1px solid var(--line-dim)", flexWrap: "wrap",
          }}>
            <span className={`dot ${s.current ? "live" : "dead"}`} />
            <div style={{ flex: 1, minWidth: 190 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <span title={s.user_agent || ""}
                      style={{ fontFamily: MONO, fontSize: 12, fontWeight: 700 }}>
                  {describe(s.user_agent)}
                </span>
                {s.current && (
                  <Badge kind="tag" tone="var(--green)"
                         style={{ border: "1px solid rgba(34,197,94,0.4)", padding: "1px 6px",
                                  textTransform: "uppercase", letterSpacing: "0.06em", opacity: 1 }}>
                    this browser
                  </Badge>
                )}
              </div>
              <div style={{ fontFamily: MONO, fontSize: 10.5, color: "var(--ink-faint)", marginTop: 3 }}>
                {s.ip ? `${s.ip} · ` : ""}signed in {when(s.created_at)} · last seen {when(s.last_seen_at)}
              </div>
            </div>
            {!s.current && (
              <Button onClick={() => revoke(s)} disabled={busyId !== ""}>
                {busyId === s.id ? "…" : "Sign out"}
              </Button>
            )}
          </div>
        ))}
      </div>

      {error && (
        <div role="alert" style={{ marginTop: 10, maxWidth: 560, fontFamily: MONO,
                                   fontSize: 11, color: "var(--red)" }}>{error}</div>
      )}
    </div>
  );
}
