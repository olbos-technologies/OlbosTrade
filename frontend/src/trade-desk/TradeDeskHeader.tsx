/**
 * Trade Desk header — environment, mode, risk profile, broker, P&L, kill switch.
 * Read-only display + mode switch via existing APIs. Paper/Live always labeled (not color-only).
 */

import React, { useEffect, useState } from "react";
import { api } from "../api/client";
import KillSwitchButton from "../components/KillSwitchButton";

type Snap = {
  label: string;
  value: string;
  tone?: "ok" | "warn" | "crit" | "muted";
  /**
   * What happens to this chip on a phone.
   *
   * "always" stays on the collapsed line; "expand" is revealed by the toggle.
   * Expressed as data on the chip rather than :nth-child in the stylesheet,
   * because the rail is a hand-ordered list and an nth-child rule silently
   * hides the wrong metric the moment somebody reorders it.
   */
  priority?: "always" | "expand";
};

function Chip({ label, value, tone = "muted", priority = "expand" }: Snap) {
  const color =
    tone === "ok" ? "var(--green)" :
    tone === "warn" ? "var(--amber)" :
    tone === "crit" ? "var(--red)" :
    "var(--ink)";
  return (
    <div className="instrument-chip" data-priority={priority}>
      <span className="instrument-chip-label">{label}</span>
      <span className="instrument-chip-value" style={{ color }}>{value}</span>
    </div>
  );
}

export default function TradeDeskHeader() {
  const [env, setEnv] = useState<"Paper" | "Live" | "Unknown">("Unknown");
  const [broker, setBroker] = useState("—");
  const [brokerStatus, setBrokerStatus] = useState("—");
  const [execMode, setExecMode] = useState("—");
  const [riskProfile, setRiskProfile] = useState("—");
  const [dayPnl, setDayPnl] = useState<string>("—");
  const [dayPnlTone, setDayPnlTone] = useState<"ok" | "crit" | "muted">("muted");
  const [heat, setHeat] = useState("—");
  const [drawdown, setDrawdown] = useState("—");
  const [ks, setKs] = useState<"Engaged" | "Clear" | "Unknown">("Unknown");
  const [regime, setRegime] = useState("—");
  /** Phone only: the rail collapses to three facts plus the kill switch. */
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () => {
      fetch("/api/market/broker")
        .then((r) => r.json())
        .then((d) => {
          if (!alive) return;
          setBroker((d.broker || "ibkr").toUpperCase());
          setBrokerStatus(d.status || "unknown");
          setEnv(d.paper_mode === false ? "Live" : d.paper_mode === true ? "Paper" : "Unknown");
        })
        .catch(() => {
          if (alive) {
            setBroker("Unavailable");
            setEnv("Unknown");
          }
        });

      api.getExecutionMode()
        .then((d: any) => { if (alive) setExecMode((d.mode || "—").toUpperCase()); })
        .catch(() => { if (alive) setExecMode("Unavailable"); });

      api.getCurrentMode()
        .then((d: any) => { if (alive) setRiskProfile((d.mode || "—").toUpperCase()); })
        .catch(() => { if (alive) setRiskProfile("Unavailable"); });

      api.getPortfolioState()
        .then((d: any) => {
          if (!alive) return;
          const s = d.state || d;
          if (typeof s.daily_pnl === "number") {
            // Format as +$120 / -$1615 so the sign is never hidden behind `$`.
            const n = s.daily_pnl;
            setDayPnl(`${n >= 0 ? "+" : "-"}$${Math.abs(n).toFixed(0)}`);
            setDayPnlTone(n >= 0 ? "ok" : "crit");
          } else if (typeof s.daily_loss_pct === "number") {
            // daily_loss_pct is a signed P&L ratio — don't hardcode "loss"
            // on what could be a gain.
            const v = s.daily_loss_pct * 100;
            setDayPnl(`${v >= 0 ? "+" : ""}${v.toFixed(2)}%`);
            setDayPnlTone(v >= 0 ? "ok" : "crit");
          } else {
            setDayPnl("—");
            setDayPnlTone("muted");
          }
          if (typeof s.daily_loss_pct === "number" && typeof s.max_daily_loss_pct === "number") {
            // Clamp to the negative portion only — a gain uses 0% of the
            // loss budget, not its magnitude (see GlobalRiskStatus.tsx for
            // the same fix and the reasoning behind it).
            const lossUsed = Math.max(0, -s.daily_loss_pct);
            const rem = Math.max(0, s.max_daily_loss_pct - lossUsed);
            setHeat(`${(rem * 100).toFixed(1)}% budget`);
            setDrawdown(`${(lossUsed * 100).toFixed(2)}% day`);
          } else {
            setHeat("—");
            setDrawdown("—");
          }
        })
        .catch(() => {
          if (alive) {
            setDayPnl("Unavailable");
            setDayPnlTone("muted");
            setHeat("Unavailable");
            setDrawdown("Unavailable");
          }
        });

      api.getTradeDeskKillSwitch()
        .then((d: any) => {
          if (!alive) return;
          setKs(d.engaged ? "Engaged" : "Clear");
        })
        .catch(() => {
          api.getKillSwitchStatus()
            .then((d: any) => {
              if (!alive) return;
              setKs(d.engaged || d.is_engaged ? "Engaged" : "Clear");
            })
            .catch(() => { if (alive) setKs("Unknown"); });
        });

      api.getRegime()
        .then((d: any) => {
          if (!alive) return;
          const r = d.regime || d.regime_type || d.type;
          setRegime(typeof r === "string" ? r.replace(/_/g, " ") : "—");
        })
        .catch(() => { if (alive) setRegime("Unavailable"); });
    };

    load();
    const t = setInterval(load, 15000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const envTone = env === "Live" ? "crit" : env === "Paper" ? "ok" : "warn";
  const ksTone = ks === "Engaged" ? "crit" : ks === "Clear" ? "ok" : "warn";
  const brokerTone =
    brokerStatus === "connected" || brokerStatus === "ok" ? "ok" :
    brokerStatus === "disconnected" ? "warn" : "muted";

  // #7 — one instrument label: PAPER · AGGRESSIVE · AUTOPILOT (never color-only).
  const stylePart =
    riskProfile === "—" || riskProfile === "Unavailable" ? "—" : riskProfile;
  const execPart =
    execMode === "—" || execMode === "Unavailable" ? "—" : execMode;
  const sessionLabel = `${env.toUpperCase()} · ${stylePart} · ${execPart}`;

  return (
    <header
      className={`instrument-rail${expanded ? " instrument-rail--expanded" : ""}`}
      style={{
        display: "flex",
        alignItems: "stretch",
        flexWrap: "wrap",
        flexShrink: 0,
      }}
      aria-label="Trade Desk status"
    >
      {/*
        PRIORITY IS THE MOBILE STORY. On a phone this rail was 124px of a
        844px screen and the positions table underneath got 133px — the data
        the page exists for was 16% of the display. Three facts stay on the
        collapsed line and the rest are a tap away.

        Session is the one worth keeping because it already carries three
        things at once: environment, risk style and execution mode.
      */}
      <Chip
        label="Session"
        value={sessionLabel}
        tone={env === "Live" ? "crit" : envTone}
        priority="always"
      />
      <Chip label="Day P&L" value={dayPnl} tone={dayPnlTone} priority="always" />
      <Chip label="Broker" value={`${broker} · ${brokerStatus}`} tone={brokerTone} />
      <Chip label="Regime" value={regime} />
      <Chip label="Risk budget" value={heat} />
      <Chip label="Drawdown" value={drawdown} />
      <Chip label="Kill switch" value={ks} tone={ksTone} />
      <button
        type="button"
        className="instrument-rail-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded(v => !v)}
      >
        {expanded ? "Less" : "More"}
      </button>
      <div style={{ flex: 1, minWidth: 8 }} />
      {/*
        The kill switch is NEVER collapsed. It is the control that stops
        trading, and hiding a safety control behind a disclosure toggle to
        save 40px is the wrong trade at any screen size — the same reasoning
        that keeps it out of the tier gate.
      */}
      <div className="instrument-rail-kill">
        <KillSwitchButton variant="panel" />
      </div>
    </header>
  );
}
