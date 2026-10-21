/**
 * Unified, read-only Command Center navigation and experience guidance.
 * This preference never changes authorization, broker mode, risk or execution.
 */
import React, { useState } from "react";
import { useTerminalNav } from "./TerminalNavContext";
import { EXPERIENCE_LEVEL_KEY, readExperienceLevel, type ExperienceLevel } from "./WelcomeBanner";

const DESTINATIONS = [
  { page: "markets:chart", label: "Markets", detail: "Charts and market context" },
  { page: "equity", label: "Signals", detail: "Ideas and supporting evidence" },
  { page: "trade:overview", label: "Trade Desk", detail: "Review trading activity" },
  { page: "trade:positions", label: "Positions", detail: "Holdings and open exposure" },
  { page: "risk:heat", label: "Risk", detail: "Concentration and limits" },
  { page: "lab:models", label: "Research", detail: "Model health and analytics" },
] as const;

const GUIDANCE: Record<ExperienceLevel, { title: string; description: string }> = {
  beginner: { title: "Start with the essentials", description: "Review your account, understand risk, and explore signals before practicing. A signal is not a recommendation or guaranteed outcome." },
  intermediate: { title: "Explore performance and risk", description: "Compare portfolio exposure, strategy evidence, and account activity. Confirm paper or live status separately before taking action." },
  pro: { title: "Professional workstation", description: "Open advanced market, execution and model-health workspaces. Data freshness and broker reconciliation remain essential." },
};

export default function CommandCenterWorkspace() {
  const onNav = useTerminalNav();
  const [level, setLevel] = useState<ExperienceLevel>(readExperienceLevel);
  const chooseLevel = (next: ExperienceLevel) => {
    setLevel(next);
    try { window.localStorage.setItem(EXPERIENCE_LEVEL_KEY, next); } catch { /* private mode */ }
    window.dispatchEvent(new CustomEvent("olbos:experience-change", { detail: next }));
  };

  return (
    <section aria-label="Command Center workspaces" style={{ margin: "12px 12px 14px", padding: 16, border: "1px solid var(--line-dim)", borderRadius: 12, background: "var(--bg-2)" }}>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", justifyContent: "space-between", alignItems: "start" }}>
        <div style={{ flex: "1 1 250px" }}>
          <div className="kicker" style={{ marginBottom: 6 }}>OLBOSTRADE · COMMAND CENTER</div>
          <h2 style={{ fontFamily: "var(--sans)", fontSize: 19, fontWeight: 700, margin: 0, color: "var(--ink)" }}>{GUIDANCE[level].title}</h2>
          <p style={{ fontSize: 12, lineHeight: 1.6, color: "var(--ink-dim)", maxWidth: 650, margin: "8px 0 0" }}>{GUIDANCE[level].description}</p>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          <label htmlFor="command-center-experience" style={{ fontSize: 11, color: "var(--ink-dim)" }}>Display experience</label>
          <select id="command-center-experience" value={level} onChange={event => chooseLevel(event.target.value as ExperienceLevel)}
            style={{ minHeight: 44, minWidth: 180, padding: "0 12px", borderRadius: 8, color: "var(--ink)", background: "var(--bg-3)", border: "1px solid var(--line-dim)" }}>
            <option value="beginner">Beginner</option>
            <option value="intermediate">Intermediate</option>
            <option value="pro">Pro</option>
          </select>
        </div>
      </div>
      <nav aria-label="Quick access to OlbosTrade workspaces" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 155px), 1fr))", gap: 8, marginTop: 16 }}>
        {DESTINATIONS.map(item => (
          <button key={item.page} type="button" onClick={() => onNav(item.page)}
            style={{ minHeight: 68, textAlign: "left", cursor: "pointer", padding: "12px", border: "1px solid var(--line-dim)", borderRadius: 9, background: "var(--bg-3)", color: "var(--ink)" }}>
            <span style={{ fontFamily: "var(--sans)", fontSize: 13, fontWeight: 650, display: "block" }}>{item.label} →</span>
            <span style={{ fontSize: 11, lineHeight: 1.5, display: "block", color: "var(--ink-dim)", marginTop: 5 }}>{item.detail}</span>
          </button>
        ))}
      </nav>
      <p role="note" style={{ fontSize: 11, lineHeight: 1.5, color: "var(--ink-dim)", margin: "12px 0 0" }}>
        Display experience changes explanations only. Manual, Copilot, Autopilot, paper/live mode, and risk limits are controlled separately.
      </p>
    </section>
  );
}
