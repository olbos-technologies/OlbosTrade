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
import { PLANS } from "../Landing";

const MONO = "var(--mono)";

/**
 * The published plan table, imported rather than copied.
 *
 * This screen originally carried its own hand-written summary with a comment
 * claiming it mirrored tier_limits.py. It did not: it told Pro users they had
 * 25 watchlist symbols when the real limit is uncapped, and Elite users they
 * had full history when the real limit is 5 years. Both wrong, in the one
 * screen whose job is to say what you are paying for, and the comment made it
 * look checked.
 *
 * Reading Landing.tsx's PLANS closes that for good: backend
 * test_tier_limits.py parses that same array and fails if it disagrees with
 * tier_limits.py, so the chain is enforced end to end — API, marketing page
 * and this screen cannot drift apart without a red build.
 */
function limitsForTier(tier: string): Array<{ feature: string; limit: string }> {
  const plan = PLANS.find(p => p.name.toLowerCase() === tier);
  return plan ? plan.limits : [];
}

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
  const limits = limitsForTier(tier);

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

          {limits.length > 0 && (
            <div style={{ maxWidth: 520, marginTop: 16 }}>
              <div className="panel-title" style={{ marginBottom: 8 }}>Your plan includes</div>
              <div className="instrument-card" style={{ padding: "2px 16px" }}>
                {limits.map(row => (
                  <Row key={row.feature} label={row.feature}>{row.limit}</Row>
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
