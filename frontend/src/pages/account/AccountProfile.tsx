/**
 * Who you are signed in as, and what your plan includes.
 *
 * Reads /api/auth/me rather than the cached session in the status bar, so a
 * tier changed on the server shows up on reload instead of persisting until
 * the cookie expires. That matters here specifically: tier changes are made by
 * hand (there is no billing), so the gap between "the operator upgraded you"
 * and "the UI admits it" would otherwise be a support question.
 */
import React, { useEffect, useState } from "react";
import { api, ApiError, type AccountUser } from "../../api/client";

const MONO = "var(--mono)";

/** Mirrors backend services/tier_limits.py. Kept as copy rather than fetched:
 *  these are the published pricing-page numbers, and a screen that showed
 *  something different from the marketing page would be the worse bug. */
const PLAN_SUMMARY: Record<string, string[]> = {
  free: [
    "1 watchlist symbol",
    "1 year of history",
    "End-of-day signals",
    "No broker connection",
  ],
  pro: [
    "25 watchlist symbols",
    "5 years of history",
    "Live equity and options signals",
    "No broker connection",
  ],
  elite: [
    "Unlimited watchlist symbols",
    "Full history",
    "Live signals",
    "Connect your own broker",
  ],
};

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", gap: 16,
      padding: "9px 0", borderBottom: "1px solid var(--line-dim)",
    }}>
      <span style={{ fontFamily: MONO, fontSize: 11, color: "var(--ink-dim)",
                     textTransform: "uppercase", letterSpacing: "0.06em" }}>{label}</span>
      <span style={{ fontFamily: MONO, fontSize: 12 }}>{children}</span>
    </div>
  );
}

export default function AccountProfile() {
  const [user, setUser] = useState<AccountUser | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    api.getMe()
      .then(d => { if (live) setUser(d.user); })
      .catch(e => { if (live) setError(e instanceof ApiError ? e.message : "Could not load your account."); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, []);

  const tier = (user?.tier || "").toLowerCase();
  const plan = PLAN_SUMMARY[tier];

  return (
    <div style={{ padding: 16, height: "100%", overflowY: "auto" }}>
      <div className="panel-title" style={{ marginBottom: 10 }}>Profile</div>

      {loading && <div style={{ fontFamily: MONO, fontSize: 11, color: "var(--ink-faint)" }}>loading…</div>}
      {error && <div role="alert" style={{ fontFamily: MONO, fontSize: 11, color: "var(--red)" }}>{error}</div>}

      {user && (
        <>
          <div className="instrument-card" style={{ maxWidth: 520, padding: "12px 16px" }}>
            <Row label="Email">{user.email}</Row>
            <Row label="Plan">
              <span style={{ color: tier === "free" ? "var(--ink-dim)" : "var(--brand)",
                             textTransform: "uppercase", letterSpacing: "0.06em" }}>
                {user.tier || "unknown"}
              </span>
            </Row>
          </div>

          {plan && (
            <div style={{ maxWidth: 520, marginTop: 16 }}>
              <div className="panel-title" style={{ marginBottom: 8 }}>Your plan includes</div>
              <div className="instrument-card" style={{ padding: "10px 16px" }}>
                {plan.map(line => (
                  <div key={line} style={{ fontFamily: MONO, fontSize: 11.5,
                                           color: "var(--ink-dim)", padding: "5px 0" }}>
                    {line}
                  </div>
                ))}
              </div>
              <div style={{ marginTop: 10, fontFamily: MONO, fontSize: 10.5,
                            color: "var(--ink-faint)", lineHeight: 1.6 }}>
                Plan changes are made by hand — there is no billing in the app.
                Ask the operator to move you between plans.
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
