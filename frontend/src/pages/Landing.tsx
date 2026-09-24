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

function TerminalPreview() {
  return (
    <div className="landing-terminal-preview" aria-label="Illustrative paper-trading terminal preview">
      <div className="landing-terminal-preview__bar"><span>OLBOS TERMINAL</span><span>PAPER · MANUAL</span></div>
      <div className="landing-terminal-preview__body">
        <div className="landing-terminal-preview__status"><span>ENV&nbsp; PAPER</span><span>KILL&nbsp; NOT ARMED</span><span>RISK&nbsp; AVAILABLE</span></div>
        <div className="landing-terminal-preview__main">
          <div><div className="landing-terminal-preview__label">SIGNAL DECISION</div><strong>SPY&nbsp; · &nbsp;BUY SPREAD</strong><p>Source, confidence, freshness, and risk context are visible before a decision.</p></div>
          <div className="landing-terminal-preview__score"><span>POP</span><strong>72%</strong><small>within guardrails</small></div>
        </div>
        <div className="landing-terminal-preview__footer"><span>01&nbsp; Market inputs</span><span>02&nbsp; Regime</span><span>03&nbsp; Guardrails</span><span>04&nbsp; Human approval</span></div>
      </div>
    </div>
  );
}

type Plan = {
  name: string;
  price: string;
  period: string;
  tagline: string;
  capabilities: { label: string; included: boolean }[];
  limits: { feature: string; limit: string }[];
  cta: { label: string; href: string; internal?: boolean };
  featured?: boolean;
};

// All plans currently open the same unauthenticated /terminal — there is no
// billing or Free/Pro/Elite gate in this repository yet. Capability lists
// below describe the intended tier split (signals-only vs. broker-connected
// execution), shown honestly and marked as planned / not enforced until
// auth + billing ship.
const PLANS: Plan[] = [
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
      { feature: "Watchlist coverage", limit: "1 ticker (planned)" },
      { feature: "Signal delay", limit: "End of day (planned)" },
      { feature: "Historical data", limit: "1 year (planned)" },
      { feature: "Broker connections", limit: "None" },
    ],
    cta: { label: "Open paper terminal", href: "/terminal", internal: true },
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
      { feature: "Watchlist coverage", limit: "Full watchlist (planned)" },
      { feature: "Signal delay", limit: "Live (planned)" },
      { feature: "Historical data", limit: "5 years (planned)" },
      { feature: "Broker connections", limit: "None" },
    ],
    cta: { label: "Open paper terminal", href: "/terminal", internal: true },
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
      { feature: "Watchlist coverage", limit: "Full watchlist (planned)" },
      { feature: "Signal delay", limit: "Live (planned)" },
      { feature: "Historical data", limit: "5 years (planned)" },
      { feature: "Broker connections", limit: "1 (planned)" },
    ],
    cta: { label: "Open paper terminal", href: "/terminal", internal: true },
  },
];

export default function Landing() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="landing">
      <header className="landing-nav">
        <div className="landing-container landing-nav-row">
          <span className="landing-wordmark">OLBOS</span>
          <nav
            id="landing-nav-links"
            className={`landing-nav-links${menuOpen ? " open" : ""}`}
            aria-label="Primary"
          >
            <NavLink href="#product">Product</NavLink>
            <NavLink href="#how">How it decides</NavLink>
            <NavLink href="#risk">Controls</NavLink>
            <NavLink href="#track-record">Verification</NavLink>
            <NavLink href="#pricing">Access</NavLink>
          </nav>
          <div className="landing-nav-actions">
            <Link className="landing-signin" to="/terminal">Sign In</Link>
            <Link className="landing-cta-btn" to="/terminal">Start Paper Trading</Link>
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
            <div className="landing-eyebrow">Paper-trading evaluation · options &amp; equities</div>
            <h1 className="landing-h1">
              Make every trade decision explainable, risk-gated, and reviewable.
            </h1>
            <p className="landing-lede">
              Olbos is an operator terminal for systematic options and equity workflows. It keeps
              signal provenance, paper/live environment, guardrails, and human approval in view
              before execution. It is currently in paper-trading evaluation; no verified live
              performance is presented here.
            </p>
            <div className="landing-hero-ctas">
              <Link className="landing-cta-btn" to="/terminal">Start Paper Trading</Link>
              <a className="landing-cta-btn secondary" href="#how">See how decisions are gated</a>
            </div>
            <div className="landing-hero-layout">
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
              <TerminalPreview />
            </div>
            <p className="landing-hero-note">Illustrative workflow preview — not a live market signal or performance report.</p>
          </div>
        </section>

        {/* ── How it works ─────────────────────────────────────────────── */}
        <section className="landing-section" id="how">
          <div className="landing-container">
            <div className="landing-eyebrow">Decision workflow</div>
            <h2 className="landing-h2">One visible path from data to decision.</h2>
            <p className="landing-lede">
              The terminal is designed so an operator can understand what changed, what is
              allowed, and who must approve the next step without reconstructing it from logs.
            </p>
            <div className="landing-grid-4">
              <Cell title="Understand the setup" body="Signals identify their source, timeframe, confidence, and freshness instead of presenting a bare directional label." />
              <Cell title="Check the regime" body="Regime classification determines which strategies can be considered before a trade is eligible." />
              <Cell title="Apply the guardrails" body="Loss limits, position caps, cooling-off periods, and sizing controls gate the decision before execution." />
              <Cell title="Choose the authority" body="Manual, Copilot, and Autopilot make the approval boundary explicit. The kill switch takes priority." />
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
              <Cell title="Decision trace" body="The terminal records the decision path and makes source disagreements visible rather than silently resolving them." />
              <Cell title="Human authority" body="Manual and Copilot modes keep an operator in the approval loop; Autopilot can be disabled at any time." />
              <Cell title="Operational visibility" body="Broker connection, environment, risk state, scanner heartbeat, and kill-switch state stay visible in the workspace." />
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
            <div className="landing-eyebrow">Planned access tiers</div>
            <h2 className="landing-h2">Explore the paper terminal today. Paid access is not available yet.</h2>
            <p className="landing-lede">
              Billing and tier enforcement are not available. The future tiers below are product
              planning, not an offer to sell access. Every button opens the same paper terminal.
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

        {/* ── Proof without performance claims ───────────────────────────── */}
        <section className="landing-section">
          <div className="landing-container">
            <div className="landing-eyebrow">Built for review</div>
            <h2 className="landing-h2">Verify the workflow without relying on performance claims.</h2>
            <div className="landing-trust-list">
              <div className="landing-trust-item">
                <span className="landing-trust-mark">&rarr;</span>
                <span className="landing-cell-body">Signal provenance, timeframe, confidence, and freshness are presented with every directional decision.</span>
              </div>
              <div className="landing-trust-item">
                <span className="landing-trust-mark">&rarr;</span>
                <span className="landing-cell-body">The paper/live environment, broker status, execution mode, kill-switch state, and risk availability are persistent workspace context.</span>
              </div>
              <div className="landing-trust-item">
                <span className="landing-trust-mark">&rarr;</span>
                <span className="landing-cell-body">Manual mode, Copilot approval, and guarded Autopilot make the level of automation explicit before execution.</span>
              </div>
              <div className="landing-trust-item">
                <span className="landing-trust-mark">&rarr;</span>
                <span className="landing-cell-body">Unknown and stale states are called out as unavailable; the interface does not replace them with safe-looking defaults.</span>
              </div>
              <div className="landing-trust-item">
                <span className="landing-trust-mark">&rarr;</span>
                <span className="landing-cell-body">Paper, backtest, and live results are intentionally separated. No verified live track record is currently published.</span>
              </div>
              <div className="landing-trust-item">
                <span className="landing-trust-mark">&rarr;</span>
                <span className="landing-cell-body">The Operations view exposes service health, scanner heartbeat, and data freshness in one compact status surface.</span>
              </div>
            </div>
          </div>
        </section>

        <section className="landing-section landing-disclosure-center" id="disclosures">
          <div className="landing-container">
            <div className="landing-eyebrow">Disclosures &amp; methodology</div>
            <h2 className="landing-h2">Clear scope before you enter the terminal.</h2>
            <div className="landing-grid-3">
              <Cell title="Evaluation status" body="Olbos is currently operated in paper-trading evaluation. The site does not present verified live results or imply that a live record exists." />
              <Cell title="Not investment advice" body="The terminal provides software workflows and market analysis. It does not provide individualized investment advice or a recommendation for any person." />
              <Cell title="Legal review required" body="This public disclosure center is product-facing context, not a substitute for jurisdiction-specific terms, privacy, or regulatory disclosures. Those materials require legal review before public distribution." />
            </div>
          </div>
        </section>

        {/* ── Final CTA ─────────────────────────────────────────────────── */}
        <section className="landing-section" style={{ textAlign: "center" }}>
          <div className="landing-container">
            <h2 className="landing-h2">Start in paper. Prove the edge before it's live.</h2>
            <p className="landing-lede" style={{ margin: "0 auto 28px" }}>
              The terminal opens directly into paper trading — no live order can be placed until
              the account and environment are explicitly switched to live.
            </p>
            <Link className="landing-cta-btn" to="/terminal">Start Paper Trading</Link>
          </div>
        </section>
      </main>

      <footer className="landing-footer">
        <div className="landing-container">
          <div className="landing-footer-row">
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span className="landing-wordmark" style={{ fontSize: 15 }}>OLBOS</span>
              <span style={{ width: 1, height: 12, background: "var(--line-dim)" }} />
              <span style={{
                fontFamily: "var(--mono)", fontSize: 9, fontWeight: 500,
                letterSpacing: "0.14em", color: "var(--ink-faint)", lineHeight: 1, whiteSpace: "nowrap",
              }}>TRADING SYSTEM</span>
            </div>
            <div className="landing-footer-links">
              <a href="#disclosures">Disclosures</a>
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
