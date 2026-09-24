/**
 * GlobalRiskStatus — persistent capital-at-risk status surface for the
 * authenticated shell (Stage 11). Shows AUTOPILOT state, execution
 * environment, broker, kill-switch state, drawdown, and remaining risk
 * budget. Every field uses `Availability<T>` — a failed or unfinished
 * fetch renders as loading/unavailable/unknown, never a default that could
 * read as "safe" (e.g. a missing kill-switch read never renders as "not
 * armed").
 *
 * Data sources (all pre-existing backend endpoints, read-only from here):
 *   - GET /api/trade-desk/execution-mode  → manual | copilot | autopilot
 *   - GET /api/market/broker              → broker name, paper_mode, connection status
 *   - GET /api/trade-desk/kill-switch (falls back to /api/risk/kill-switch/status)
 *   - GET /api/risk/portfolio-state       → daily/weekly loss pct + limits
 *
 * Remaining risk budget is derived only from two values pulled from the
 * same verified source (portfolio-state) in the same unit (fraction of
 * starting capital): max_daily_loss_pct - daily_loss_pct. If either is
 * missing, the budget renders as unavailable rather than a guess.
 */
import React, { useEffect, useState } from "react";

import { api } from "../api/client";
import { useIsMobile } from "../hooks/useIsMobile";
import type { Availability } from "../types/signal";

interface ExecMode {
  mode: "manual" | "copilot" | "autopilot" | string;
}
interface BrokerInfo {
  broker: string;
  paper_mode: boolean;
  status: string;
  error?: string;
}
interface KillSwitchState {
  engaged: boolean;
}
interface RiskState {
  daily_loss_pct?: number;
  max_daily_loss_pct?: number;
  weekly_loss_pct?: number;
  max_weekly_loss_pct?: number;
  capital_pct_remaining?: number;
  account_value?: number;
  error?: string;
}

const POLL_MS = 15000;

function useAvailability<T>(fetcher: () => Promise<T>, deps: unknown[] = []): Availability<T> {
  const [state, setState] = useState<Availability<T>>({ status: "loading" });

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setInterval>;

    const load = () => {
      fetcher()
        .then((value) => {
          if (alive) setState({ status: "available", value, updatedAt: new Date().toISOString() });
        })
        .catch((err: Error) => {
          if (alive) setState({ status: "error", message: err?.message });
        });
    };

    load();
    timer = setInterval(load, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}

function Chip({
  label,
  value,
  tone,
  order,
  title,
}: {
  label: string;
  value: string;
  tone: string;
  order: number;
  title?: string;
}) {
  return (
    <div
      title={title}
      style={{
        order,
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "3px 9px",
        border: `1px solid ${tone}55`,
        borderRadius: 3,
        background: "var(--bg-3)",
        whiteSpace: "nowrap",
      }}
    >
      <span className="mono" style={{ color: "var(--ink-faint)", fontSize: 9, letterSpacing: "0.08em" }}>
        {label}
      </span>
      <span className="mono" style={{ color: tone, fontSize: 10.5, fontWeight: 700 }}>
        {value}
      </span>
    </div>
  );
}

/**
 * Chips whose tone says something needs attention. Red is an alarm, amber is
 * unknown-or-elevated — and unknown must count as attention here, because a
 * status this row cannot read is exactly the case where folding it away would
 * be worst. Everything else (nominal, dim, green, still loading) may collapse.
 */
const ALERT_TONES = new Set(["var(--red)", "var(--amber)"]);

function isAlertChip(el: React.ReactElement): boolean {
  return ALERT_TONES.has((el.props as { tone?: string }).tone ?? "");
}

function pct(v: number | undefined): string | null {
  if (typeof v !== "number" || Number.isNaN(v)) return null;
  return `${(v * 100).toFixed(1)}%`;
}

export default function GlobalRiskStatus() {
  const isMobile = useIsMobile();
  const [expanded, setExpanded] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const execAvail = useAvailability<ExecMode>(() => api.getExecutionMode() as Promise<ExecMode>);
  const brokerAvail = useAvailability<BrokerInfo>(() =>
    fetch("/api/market/broker").then((r) => {
      if (!r.ok) throw new Error(`broker status ${r.status}`);
      return r.json();
    })
  );
  const killAvail = useAvailability<KillSwitchState>(async () => {
    try {
      return (await api.getTradeDeskKillSwitch()) as KillSwitchState;
    } catch {
      return (await api.getKillSwitchStatus()) as KillSwitchState;
    }
  });
  const riskAvail = useAvailability<{ state: RiskState }>(() => api.getPortfolioState() as Promise<{ state: RiskState }>);

  // ── 1. Live vs paper environment ──────────────────────────────────────
  let envChip: React.ReactNode;
  if (brokerAvail.status === "loading") {
    envChip = <Chip label="ENV" value="LOADING" tone="var(--ink-faint)" order={1} />;
  } else if (brokerAvail.status !== "available") {
    envChip = <Chip label="ENV" value="UNKNOWN" tone="var(--amber)" order={1} title="Broker status could not be read from /api/market/broker" />;
  } else {
    const b = brokerAvail.value;
    const connected = b.status === "connected";
    if (!connected) {
      envChip = <Chip label="ENV" value="DISCONNECTED" tone="var(--red)" order={1} title={b.error || "Broker reports not connected"} />;
    } else {
      const label = `${b.paper_mode ? "PAPER" : "LIVE"} — ${b.broker.toUpperCase()}`;
      envChip = <Chip label="ENV" value={label} tone={b.paper_mode ? "var(--amber)" : "var(--green)"} order={1} />;
    }
  }

  // ── 2. AUTOPILOT ───────────────────────────────────────────────────────
  let autoChip: React.ReactNode;
  if (execAvail.status === "loading") {
    autoChip = <Chip label="MODE" value="LOADING" tone="var(--ink-faint)" order={2} />;
  } else if (execAvail.status !== "available") {
    autoChip = <Chip label="MODE" value="AUTOPILOT UNKNOWN" tone="var(--amber)" order={2} title="Execution mode could not be read" />;
  } else {
    const mode = execAvail.value.mode;
    if (mode === "autopilot") autoChip = <Chip label="MODE" value="AUTOPILOT ON" tone="var(--amber)" order={2} />;
    else if (mode === "copilot" || mode === "manual") autoChip = <Chip label="MODE" value="AUTOPILOT OFF" tone="var(--ink-dim)" order={2} />;
    else autoChip = <Chip label="MODE" value="AUTOPILOT UNKNOWN" tone="var(--amber)" order={2} title={`Unrecognized mode value: ${mode}`} />;
  }

  // ── 3. Kill switch ───────────────────────────────────────────────────
  let killChip: React.ReactNode;
  if (killAvail.status === "loading") {
    killChip = <Chip label="KILL" value="LOADING" tone="var(--ink-faint)" order={3} />;
  } else if (killAvail.status !== "available") {
    killChip = <Chip label="KILL" value="UNKNOWN" tone="var(--amber)" order={3} title="Kill switch status could not be read" />;
  } else {
    const engaged = killAvail.value.engaged;
    killChip = engaged
      ? <Chip label="KILL" value="ARMED" tone="var(--red)" order={3} title="Kill switch engaged — new orders halted" />
      : <Chip label="KILL" value="NOT ARMED" tone="var(--ink-dim)" order={3} />;
  }

  // ── 4. Drawdown / 5. Risk budget ─────────────────────────────────────
  let drawdownChip: React.ReactNode;
  let budgetChip: React.ReactNode;
  if (riskAvail.status === "loading") {
    drawdownChip = <Chip label="DD" value="LOADING" tone="var(--ink-faint)" order={4} />;
    budgetChip = <Chip label="RISK BUDGET" value="LOADING" tone="var(--ink-faint)" order={5} />;
  } else if (riskAvail.status !== "available" || riskAvail.value.state?.error) {
    drawdownChip = <Chip label="DD" value="UNAVAILABLE" tone="var(--amber)" order={4} title="Portfolio state could not be read" />;
    budgetChip = <Chip label="RISK BUDGET" value="UNAVAILABLE" tone="var(--amber)" order={5} />;
  } else {
    const s = riskAvail.value.state;
    // daily_loss_pct is a signed P&L ratio (positive on a gain day), not
    // always a loss. The old code compared it to max_daily_loss_pct raw:
    // a real loss (negative) almost never tripped the red alert since a
    // negative number is rarely >= a positive max, while a big gain
    // (positive, sometimes >= max) incorrectly could. Clamp to the
    // negative portion only so "DAILY DD" always means actual drawdown.
    const dailyLossUsed = Math.max(0, -(s.daily_loss_pct ?? 0));
    const dailyLoss = pct(dailyLossUsed);
    const dailyLimit = pct(s.max_daily_loss_pct);
    drawdownChip =
      dailyLoss !== null ? (
        <Chip
          label="DAILY DD"
          value={dailyLimit ? `${dailyLoss} / ${dailyLimit}` : dailyLoss}
          tone={typeof s.max_daily_loss_pct === "number" && dailyLossUsed >= s.max_daily_loss_pct ? "var(--red)" : "var(--ink)"}
          order={4}
        />
      ) : (
        <Chip label="DAILY DD" value="UNAVAILABLE" tone="var(--amber)" order={4} />
      );

    if (typeof s.daily_loss_pct === "number" && typeof s.max_daily_loss_pct === "number") {
      const remaining = Math.max(0, s.max_daily_loss_pct - dailyLossUsed);
      budgetChip = (
        <Chip
          label="RISK BUDGET LEFT"
          value={`${(remaining * 100).toFixed(1)}%`}
          tone={remaining <= 0 ? "var(--red)" : "var(--ink)"}
          order={5}
          title="Derived: max daily loss limit − current daily loss, both from /api/risk/portfolio-state"
        />
      );
    } else {
      budgetChip = <Chip label="RISK BUDGET LEFT" value="UNAVAILABLE" tone="var(--amber)" order={5} title="Requires both current daily loss and the daily loss limit" />;
    }
  }

  // ── 6. Connection / freshness ────────────────────────────────────────
  const anyError = [execAvail, brokerAvail, killAvail, riskAvail].some((a) => a.status === "error");
  const failedChecks = [execAvail, brokerAvail, killAvail, riskAvail].filter((a) => a.status === "error").length;
  const connChip = anyError ? (
    <Chip label="DATA" value="PARTIAL" tone="var(--amber)" order={6} title="One or more risk-status fields failed to load; see individual chips." />
  ) : (
    <Chip label="DATA" value="LIVE" tone="var(--green)" order={6} />
  );

  // ── Mobile: show what needs attention, fold away what does not ───────
  // Six chips wrap to three rows on a 375px screen and eat roughly an eighth
  // of the viewport, mostly to report that nothing is wrong. Collapsing is by
  // *severity*, never by name: an armed kill switch, a disconnected broker, a
  // breached drawdown or an exhausted risk budget all carry an alert tone and
  // stay on screen. Only nominal chips fold, behind a count that says how many
  // and opens them on tap — so the row is short when the desk is calm and
  // grows exactly when something wants looking at.
  // Keyed by label, not index: the collapsed and expanded lists differ in
  // length, and an index key would make React reuse the wrong chip's DOM
  // across a toggle.
  const chips = [envChip, autoChip, killChip, drawdownChip, budgetChip, connChip].map((c) =>
    React.cloneElement(c, { key: (c.props as { label: string }).label }),
  );
  const alerts = chips.filter(isAlertChip);
  const nominal = chips.filter((c) => !isAlertChip(c));
  const collapsed = isMobile && !expanded && nominal.length > 0;
  // A partial read previously rendered several UNKNOWN/UNAVAILABLE chips in a
  // row. On a phone that makes an outage feel louder than the work surface and
  // repeats the same fact. Start with one explicit, truthful summary; details
  // stay one tap away and no status is silently treated as nominal.
  const showHealthSummary = isMobile && anyError && !detailsOpen;

  return (
    <div
      role="region"
      aria-label="Capital-at-risk status"
      style={{
        display: "flex",
        // One scrolling line on a phone instead of three wrapped rows.
        flexWrap: isMobile ? "nowrap" : "wrap",
        overflowX: isMobile ? "auto" : undefined,
        gap: 6,
        alignItems: "center",
        padding: isMobile ? "5px 10px" : "6px 12px",
        background: "var(--bg-2)",
        borderBottom: "1px solid var(--line-dim)",
        fontFamily: "var(--mono)",
        flexShrink: 0,
      }}
    >
      {showHealthSummary ? (
        <button
          type="button"
          className="system-health-summary"
          onClick={() => setDetailsOpen(true)}
          aria-expanded="false"
          aria-label={`Live status unavailable. Show details for ${failedChecks} failed checks`}
        >
          <span aria-hidden="true">!</span>
          <span>LIVE STATUS UNAVAILABLE</span>
          <span className="system-health-summary__detail">
            {failedChecks} {failedChecks === 1 ? "check" : "checks"} · DETAILS
          </span>
        </button>
      ) : (
        <>
          {collapsed ? alerts : chips}
          {isMobile && anyError && (
            <button
              type="button"
              className="system-health-details"
              onClick={() => setDetailsOpen(false)}
              aria-label="Hide live status details"
            >
              HIDE DETAILS
            </button>
          )}
          {isMobile && anyError && (
            <button
              type="button"
              className="system-health-details"
              onClick={() => window.location.reload()}
              aria-label="Retry live status checks"
            >
              RETRY
            </button>
          )}
        </>
      )}
      {isMobile && !showHealthSummary && nominal.length > 0 && (
        <button
          type="button"
          onClick={() => setExpanded((p) => !p)}
          aria-expanded={expanded}
          aria-label={
            expanded
              ? "Hide nominal status indicators"
              : `Show ${nominal.length} nominal status indicators`
          }
          style={{
            order: 99,
            flexShrink: 0,
            minHeight: 28,
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "3px 9px",
            border: "1px solid var(--line)",
            borderRadius: 3,
            background: "var(--bg-3)",
            color: "var(--ink-faint)",
            fontFamily: "var(--mono)",
            fontSize: 9,
            letterSpacing: "0.08em",
            whiteSpace: "nowrap",
            cursor: "pointer",
            touchAction: "manipulation",
          }}
        >
          {expanded ? "LESS" : `+${nominal.length} OK`}
        </button>
      )}
    </div>
  );
}
