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
    <div className="acct-page">
      <div className="acct-layout">
        <div className="acct-main">

          <section className="acct-card">
            <div className="acct-card__body">
              <div className="acct-heading">
                <div>
                  <span className="acct-eyebrow">Account</span>
                  <h1>Profile</h1>
                  <p>The identity you sign in with, and the plan it carries.</p>
                </div>
              </div>

              {loading && <div style={{ marginTop: 14, fontFamily: MONO, fontSize: 11, color: "var(--ink-faint)" }}>loading…</div>}
              {error && <div role="alert" style={{ marginTop: 14, fontFamily: MONO, fontSize: 11, color: "var(--red)" }}>{error}</div>}

              {user && (
                <div style={{ marginTop: 14 }}>
                  <Row label="Email">{user.email}</Row>
                  <Row label="Plan">
                    <span style={{ color: tier === "free" ? "var(--ink-dim)" : "var(--brand)",
                                   textTransform: "uppercase", letterSpacing: "0.06em" }}>
                      {user.tier || "unknown"}
                    </span>
                  </Row>
                </div>
              )}
            </div>
          </section>

          {user && limits.length > 0 && (
            <section className="acct-card">
              <div className="acct-card__body">
                <span className="acct-eyebrow">Your plan includes</span>
                <div style={{ marginTop: 10 }}>
                  {limits.map(row => (
                    <Row key={row.feature} label={row.feature}>{row.limit}</Row>
                  ))}
                </div>
              </div>
            </section>
          )}
        </div>

        <aside className="acct-rail" aria-label="Plan">
          <div className="acct-rail__block">
            <span className="acct-eyebrow">Plan</span>
            <div className="acct-rail__row">
              <span>Tier</span>
              <b style={{ textTransform: "uppercase", letterSpacing: "0.06em" }}>
                {loading ? "—" : (user?.tier || "unknown")}
              </b>
            </div>
            <div className="acct-rail__row">
              <span>Entitlements</span>
              <b className="tnum">{loading ? "—" : String(limits.length)}</b>
            </div>
          </div>

          <div className="acct-rail__block">
            <span className="acct-eyebrow">Billing</span>
            <div className="acct-rail__item">
              <i style={{ background: "var(--ink-faint)" }} />
              <span>Plan changes are made by hand — there is no billing in the
                app. Ask the operator to move you between plans.</span>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
