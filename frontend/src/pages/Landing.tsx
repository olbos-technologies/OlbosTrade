/**
 * Landing — public marketing page. Distinct layout from the terminal shell
 * (TerminalLayout.tsx), same design tokens (index.css). Fetches no trading
 * data and calls no authenticated endpoint — safe for unauthenticated
 * visitors, since this repository has no authentication system at all (see
 * CHANGELOG for that finding).
 *
 * Every claim below is grounded in repository evidence (README.md,
 * TRADING_POLICY.md, ARCHITECTURE_MEMO.md, and the routes/services they
 * describe) rather than the original task's specification defaults —
 * several spec-provided claims (Tradier, an XGBoost regime classifier) do
 * not match the codebase and are intentionally not repeated here. See the
 * implementation report for the full claim-verification table.
 */
import React, { useState } from "react";
import { Link } from "react-router-dom";
import "../landing.css";

function NavLink({ href, children }: { href: string; children: React.ReactNode }) {
  return <a href={href}>{children}</a>;
}

function PipelineStage({ n, title, detail }: { n: string; title: string; detail: string }) {
  return (
    <div className="pipeline-stage">
      <div className="pipeline-stage-num">{n}</div>
      <div className="pipeline-stage-title">{title}</div>
      <div className="pipeline-stage-detail">{detail}</div>
    </div>
  );
}

function Cell({ title, body }: { title: string; body: string }) {
  return (
    <div className="landing-cell">
      <div className="landing-cell-title">{title}</div>
      <div className="landing-cell-body">{body}</div>
    </div>
  );
}

function Metric({ label, value, placeholder = true }: { label: string; value: string; placeholder?: boolean }) {
  return (
    <div className="landing-cell">
      <div className="landing-metric-value">{value}</div>
      <div className="landing-metric-label">{label}</div>
      {placeholder && <div className="landing-placeholder-tag">Awaiting verified history</div>}
    </div>
  );
}

export type Plan = {
  name: string;
  price: string;
  period: string;
  tagline: string;
  capabilities: { label: string; included: boolean }[];
  limits: { feature: string; limit: string }[];
  cta: { label: string; href: string; internal?: boolean };
  featured?: boolean;
};

// Every plan CTA points at /request-access, not at /terminal.
//
// They used to open the terminal directly, which was the honest thing while
// the terminal was open to anyone. With AUTH_ENABLED on it stopped being
// honest: the button sent a visitor to a sign-in screen for an account they
// have no way to obtain, which is a dead end dressed as a call to action.
//
// Tier is still assigned by the operator — there is no billing in this
// repository — so all three cards lead to the same queue. The limits below
// ARE enforced now: backend/app/services/tier_limits.py is the table the
// API reads, and test_tier_limits_match_the_landing_page parses this file
// and fails if the two disagree. Editing a number here without editing it
// there breaks the build, which is the point — a pricing card that
// overstates what the API grants is a product defect, not a typo.
// EXPORTED so the account Profile screen renders these same rows rather than a
// copy of them. A copy is what drifted: #79 shipped a hand-written summary
// claiming Pro gets 25 watchlist symbols and Elite gets full history, when the
// table below says Full watchlist and 5 years for both. Wrong about what
// someone is paying for, in the one screen that exists to tell them.
//
// Keep the array HERE. backend/tests/test_tier_limits.py parses this file by
// path and fails if these numbers disagree with tier_limits.py, so this stays
// the single verified source and Profile inherits that guarantee.
export const PLANS: Plan[] = [
  {
    name: "Free",
    price: "$0",
    period: "/mo",
    tagline: "Delayed signals and the trading journal — see what the system does before paying.",
    capabilities: [
      { label: "Delayed / limited signal feed", included: true },
      { label: "Signal attribution (source, timeframe, confidence)", included: true },
      { label: "Trading journal for your own manual trades", included: true },
      { label: "Full live signal feed & backtesting", included: false },
      { label: "Connect your broker (Copilot execution)", included: false },
    ],
    limits: [
      { feature: "Watchlist coverage", limit: "1 ticker" },
      { feature: "Signal delay", limit: "End of day" },
      { feature: "Historical data", limit: "1 year" },
      { feature: "Broker connections", limit: "None" },
    ],
    cta: { label: "Request access", href: "/request-access", internal: true },
  },
  {
    name: "Pro",
    price: "$29",
    period: "/mo",
    tagline: "Full live signal feed, regime dashboard, backtesting, and performance analytics.",
    capabilities: [
      { label: "Full live equity + options signal feed", included: true },
      { label: "Signal attribution (source, timeframe, confidence)", included: true },
      { label: "Regime classification & backtesting / strategy comparison", included: true },
      { label: "Trading journal & performance analytics", included: true },
      { label: "Connect your broker (Copilot execution)", included: false },
    ],
    limits: [
      { feature: "Watchlist coverage", limit: "Full watchlist" },
      { feature: "Signal delay", limit: "Live" },
      { feature: "Historical data", limit: "5 years" },
      { feature: "Broker connections", limit: "None" },
    ],
    cta: { label: "Request access", href: "/request-access", internal: true },
    featured: true,
  },
  {
    name: "Elite",
    price: "$99",
    period: "/mo",
    tagline: "Everything in Pro, plus connect your own broker — you approve every trade before it executes.",
    capabilities: [
      { label: "Everything in Pro", included: true },
      { label: "Connect your own broker (IBKR or Alpaca)", included: true },
      { label: "Copilot — approve every signal before it trades", included: true },
      { label: "Fully unattended Autopilot execution", included: false },
    ],
    limits: [
      { feature: "Watchlist coverage", limit: "Full watchlist" },
      { feature: "Signal delay", limit: "Live" },
      { feature: "Historical data", limit: "5 years" },
      { feature: "Broker connections", limit: "Your own broker" },
    ],
    cta: { label: "Request access", href: "/request-access", internal: true },
  },
];

export default function Landing() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="landing">
      <header className="landing-nav">
        <div className="landing-container landing-nav-row">
          <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
            <img src="/olbos-mark-sm.webp" alt="" width={55} height={24} />
            <span className="landing-wordmark">OLBOS</span>
          </span>
          <nav
            id="landing-nav-links"
            className={`landing-nav-links${menuOpen ? " open" : ""}`}
            aria-label="Primary"
          >
            <NavLink href="#product">Product</NavLink>
            <NavLink href="#how">How It Works</NavLink>
            <NavLink href="#risk">Risk Controls</NavLink>
            <NavLink href="#track-record">Track Record</NavLink>
            <NavLink href="#pricing">Pricing</NavLink>
          </nav>
          <div className="landing-nav-actions">
            <Link className="landing-signin" to="/terminal">Sign In</Link>
            {/* One label now. This carried two — "Start Paper Trading" and a
                compact "Start Free" swapped by CSS — because the full label
                measures 173px, most of the phone nav row's budget. "Sign Up"
                is about a third of that, so the swap has nothing left to do
                and the markup, the CSS and three tests that existed only to
                keep it working all go away with it. */}
            <Link className="landing-cta-btn landing-nav-cta" to="/request-access">
              Sign Up
            </Link>
            <button
              type="button"
              className="landing-nav-toggle"
              aria-expanded={menuOpen}
              aria-controls="landing-nav-links"
              aria-label={menuOpen ? "Close menu" : "Open menu"}
              onClick={() => setMenuOpen((o) => !o)}
            >
              {menuOpen ? "✕" : "☰"}
            </button>
          </div>
        </div>
      </header>

      <main>
        {/* ── Hero ──────────────────────────────────────────────────────── */}
        <section className="landing-section landing-hero" id="product">
          <div className="landing-container">
            {/* WebP, not PNG: the gold gradients band visibly under palette
                quantisation (200 colours cost 270 KB and looked striped), while
                WebP q90 is visually identical to the 688 KB PNG at 124 KB — less
                than half the 274 KB this replaced. No PNG fallback: tsconfig
                targets ES2020 and the app is React 18, so any browser that can
                run this page has supported WebP with alpha for years.

                width/height are the asset's true 2.27:1, not the old 1.83:1, so
                the reserved box matches and the hero does not shift on load. */}
            <img
              className="landing-hero-banner"
              src="/olbos-mark.webp"
              alt="Olbos Trade — Algorithmic Trading System"
              width={1400}
              height={616}
            />
            <div className="landing-eyebrow">Systematic Options &amp; Equity Execution</div>
            <h1 className="landing-h1">
              Systematic options and equity execution with risk controls built into every decision.
            </h1>
            {/* Crypto is named as a ROADMAP item and nowhere else. There is no
                crypto support in this codebase — no venue, no data feed, no
                instrument model — so anything that reads as a current
                capability would be false. The same rule the track-record
                numbers follow: say what exists, label what does not. */}
            <p className="landing-lede">
              Olbos Trading System runs a rules-based options and equity workflow — regime
              detection, a trained signal model, and a fail-closed risk gate — in front of every
              trade decision. Nothing executes without passing guardrails, and every signal shows
              where it came from. Crypto is planned and not yet supported. Currently in
              paper-trading evaluation; live capital requires a validated track record first.
            </p>
            <div className="landing-hero-ctas">
              <Link className="landing-cta-btn" to="/request-access">Start Paper Trading</Link>
              <a className="landing-cta-btn secondary" href="#risk">See risk controls</a>
            </div>

            <div className="pipeline" role="img" aria-label="Execution pipeline: market inputs, regime detection, risk-gated evaluation, controlled execution and audit">
              <PipelineStage
                n="01"
                title="Market inputs"
                detail="SPY/QQQ/IWM price/volatility data and account state, decoupled from execution."
              />
              <PipelineStage
                n="02"
                title="Regime detection"
                detail="Rule-based VIX/ADX classification into four regimes; strategy eligibility follows the regime."
              />
              <PipelineStage
                n="03"
                title="Risk-gated evaluation"
                detail="Trained signal model, POP/EV thresholds, Kelly-informed sizing, and hard loss limits — all must pass."
              />
              <PipelineStage
                n="04"
                title="Controlled execution & audit"
                detail="Manual, Copilot, or Autopilot dispatch, with every decision written to a trade journal and execution log."
              />
            </div>
          </div>
        </section>

        {/* ── How it works ─────────────────────────────────────────────── */}
        <section className="landing-section" id="how">
          <div className="landing-container">
            <div className="landing-eyebrow">How It Works</div>
            <h2 className="landing-h2">From market data to a gated decision</h2>
            <p className="landing-lede">
              Every step below exists in the running system today — described at the level the
              codebase actually implements it, not aspirationally.
            </p>
            <div className="landing-grid-4">
              <Cell title="1. Market inputs" body="SPY/QQQ/IWM and related market data feed the scanners and regime classifier, independent of the broker connection used for order execution." />
              <Cell title="2. Regime detection" body="A VIX/ADX-based classifier buckets the market into four regimes every scan cycle; each regime allows or restricts specific strategies." />
              <Cell title="3. Risk-gated evaluation" body="A trained scoring model ranks candidates; guardrails (daily/weekly/monthly loss limits, position caps, cooling-off periods) and Kelly-informed sizing run before anything is eligible." />
              <Cell title="4. Controlled execution" body="Manual mode only generates signals. Copilot queues them for human approval. Autopilot executes only signals that clear every gate above — and a kill switch halts new orders at any time." />
            </div>
          </div>
        </section>

        {/* ── Risk controls ─────────────────────────────────────────────── */}
        <section className="landing-section" id="risk">
          <div className="landing-container">
            <div className="landing-eyebrow">Risk Controls</div>
            <h2 className="landing-h2">Automation should increase discipline, not remove oversight.</h2>
            <div className="landing-grid-3">
              <Cell title="Position sizing" body="Fractional-Kelly and volatility-based sizing, applied per trade before submission." />
              <Cell title="Drawdown limits" body="Hard daily, weekly, and monthly loss limits, enforced by the guardrail service — not a UI suggestion." />
              <Cell title="Kill switch" body="One control halts new order submission immediately and asks the broker layer to cancel open orders and flatten positions." />
              <Cell title="Live vs. paper visibility" body="The operator terminal always shows which broker and environment (paper or live) is active — never inferred silently." />
              <Cell title="Signal attribution" body="Every directional signal in the terminal shows its source, timeframe, confidence, and freshness — a bare BUY/SELL label is treated as a defect." />
              <Cell title="Divergence disclosure" body="On the equity scan panel, when the scan engine and the background scanner disagree for the same symbol, the terminal shows the disagreement explicitly instead of resolving it visually." />
              <Cell title="Manual oversight" body="Autopilot can be turned off at any time; Manual and Copilot modes keep a human in the approval loop." />
              <Cell title="Explainability" body="The signal model's training pipeline runs a SHAP-based economic-direction check at train time and on live signals, to catch backwards feature logic." />
              <Cell title="No validated track record yet" body="Olbos Trading System has not completed a validated paper-trading evaluation period. We say so plainly instead of implying otherwise." />
            </div>
          </div>
        </section>

        {/* ── Metrics ───────────────────────────────────────────────────── */}
        <section className="landing-section" id="track-record">
          <div className="landing-container">
            <div className="landing-eyebrow">Track Record</div>
            <h2 className="landing-h2">Published only once verified</h2>
            <p className="landing-lede">
              We do not publish performance numbers that mix backtest, paper, and live results,
              and we do not publish anything before it is verifiable. The fields below are
              placeholders until a track record exists.
            </p>
            <div className="landing-grid-4">
              <Metric label="Live since" value="Not published" />
              <Metric label="Trades evaluated" value="Demo data" />
              <Metric label="Maximum drawdown" value="Awaiting verified history" placeholder={false} />
              <Metric label="Risk-gate rejection rate" value="Awaiting verified history" placeholder={false} />
            </div>
          </div>
        </section>

        {/* ── Pricing ───────────────────────────────────────────────────── */}
        <section className="landing-section" id="pricing">
          <div className="landing-container">
            <div className="landing-eyebrow">Pricing</div>
            <h2 className="landing-h2">Free to explore. Pro and Elite when billing ships.</h2>
            <p className="landing-lede">
              The limits below are enforced by the API today. What is not wired up is
              billing: there is no card to enter, and your tier is set by hand when your
              access request is approved. The Pro ($29) and Elite ($99) prices are the
              intended future pricing, and nothing charges you for anything yet.
            </p>
            <div className="pricing-grid">
              {PLANS.map((plan) => (
                <div className={`pricing-card${plan.featured ? " featured" : ""}`} key={plan.name}>
                  <div className="pricing-card-head">
                    <div className="pricing-card-name">{plan.name}</div>
                    <div className="pricing-card-price">
                      <span className="pricing-card-amount">{plan.price}</span>
                      <span className="pricing-card-period">{plan.period}</span>
                    </div>
                  </div>
                  <p className="pricing-card-tagline">{plan.tagline}</p>
                  <div className="pricing-card-capabilities">
                    {plan.capabilities.map((c) => (
                      <div
                        className={`pricing-card-capability${c.included ? "" : " excluded"}`}
                        key={c.label}
                      >
                        <span className="pricing-card-capability-mark" aria-hidden="true">
                          {c.included ? "✓" : "—"}
                        </span>
                        {c.label}
                      </div>
                    ))}
                  </div>
                  <div className="pricing-card-limits">
                    {plan.limits.map((l) => (
                      <div className="pricing-card-limit" key={l.feature}>
                        <span className="landing-cell-body">{l.feature}</span>
                        <span className="pricing-card-limit-value">{l.limit}</span>
                      </div>
                    ))}
                  </div>
                  {plan.cta.internal ? (
                    <Link className="landing-cta-btn" to={plan.cta.href}>{plan.cta.label}</Link>
                  ) : (
                    <a className="landing-cta-btn" href={plan.cta.href}>{plan.cta.label}</a>
                  )}
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ── Trust & transparency ──────────────────────────────────────── */}
        <section className="landing-section">
          <div className="landing-container">
            <div className="landing-eyebrow">Trust &amp; Transparency</div>
            <h2 className="landing-h2">What the terminal always shows you</h2>
            <div className="landing-trust-list">
              <div className="landing-trust-item">
                <span className="landing-trust-mark">&rarr;</span>
                <span className="landing-cell-body">Every signal's source system, timeframe, and confidence — or an explicit "unknown" if the frontend can't verify it.</span>
              </div>
              <div className="landing-trust-item">
                <span className="landing-trust-mark">&rarr;</span>
                <span className="landing-cell-body">On the equity scan results, when the scan engine and the background scanner disagree for the same symbol, that disagreement is surfaced — never silently resolved.</span>
              </div>
              <div className="landing-trust-item">
                <span className="landing-trust-mark">&rarr;</span>
                <span className="landing-cell-body">Live vs. paper environment, broker connection, AUTOPILOT state, kill-switch state, and drawdown — visible from any screen in the terminal.</span>
              </div>
              <div className="landing-trust-item">
                <span className="landing-trust-mark">&rarr;</span>
                <span className="landing-cell-body">Unknown risk state is shown as unknown. It is never replaced with a default that looks safe.</span>
              </div>
              <div className="landing-trust-item">
                <span className="landing-trust-mark">&rarr;</span>
                <span className="landing-cell-body">Performance is only published once it is verified and clearly sourced — backtest, paper, and live results are never mixed.</span>
              </div>
              <div className="landing-trust-item">
                <span className="landing-trust-mark">&rarr;</span>
                <span className="landing-cell-body">Automation can be turned off at any time — Manual mode and the kill switch always take priority over Autopilot.</span>
              </div>
            </div>
          </div>
        </section>

        {/* ── Final CTA ─────────────────────────────────────────────────── */}
        <section className="landing-section" style={{ textAlign: "center" }}>
          <div className="landing-container">
            <h2 className="landing-h2">Start in paper. Prove the edge before it's live.</h2>
            <p className="landing-lede" style={{ margin: "0 auto 28px" }}>
              Accounts open in paper trading — no live order can be placed until the account and
              environment are explicitly switched to live. Access is invite-only; a person reviews
              every request.
            </p>
            <Link className="landing-cta-btn" to="/request-access">Start Paper Trading</Link>
          </div>
        </section>
      </main>

      <footer className="landing-footer">
        <div className="landing-container">
          <div className="landing-footer-row">
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <img src="/olbos-mark-sm.webp" alt="" width={41} height={18} />
              <span className="landing-wordmark" style={{ fontSize: 15 }}>OLBOS</span>
              <span style={{ width: 1, height: 12, background: "var(--line-dim)" }} />
              <span style={{
                fontFamily: "var(--mono)", fontSize: 9, fontWeight: 500,
                letterSpacing: "0.14em", color: "var(--ink-faint)", lineHeight: 1, whiteSpace: "nowrap",
              }}>TRADING SYSTEM</span>
            </div>
            <div className="landing-footer-links">
              <span className="landing-cell-body" style={{ fontSize: 12, color: "var(--ink-faint)" }}>
                Disclosures coming soon
              </span>
              <Link to="/terminal">Sign In</Link>
            </div>
          </div>
          <p className="landing-disclosure">
            Options and equity trading involves substantial risk of loss and is not suitable for
            all investors. Olbos Trading System is currently in paper-trading evaluation with no
            verified live performance history. Nothing on this page is investment advice, and past
            or simulated results, once published, will not guarantee future returns.
          </p>
        </div>
      </footer>
    </div>
  );
}
