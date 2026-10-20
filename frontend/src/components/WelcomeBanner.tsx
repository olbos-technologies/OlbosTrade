/**
 * Progressive, educational first-visit guidance. Presentation preferences do NOT
 * change execution mode, broker permissions, or backend risk gates.
 */
import React, { useState } from "react";
import { useTerminalNav } from "./TerminalNavContext";

export const WELCOME_DISMISS_KEY = "olbos.welcome.dismissed";
export const EXPERIENCE_LEVEL_KEY = "olbos.experience.level";
export type ExperienceLevel = "beginner" | "intermediate" | "pro";

const LEVELS: { id: ExperienceLevel; label: string; description: string }[] = [
  { id: "beginner", label: "New to investing", description: "Simple language and guided practice" },
  { id: "intermediate", label: "Some experience", description: "Signals, portfolio and strategy context" },
  { id: "pro", label: "Experienced trader", description: "Advanced analytics and research tools" },
];

export function readExperienceLevel(): ExperienceLevel {
  try {
    const value = window.localStorage.getItem(EXPERIENCE_LEVEL_KEY);
    return value === "beginner" || value === "intermediate" || value === "pro" ? value : "beginner";
  } catch {
    return "beginner";
  }
}

const TERMS: Record<string, string> = {
  "Signal": "A research observation that may indicate a potential market opportunity. It is not a guaranteed prediction.",
  "Paper trading": "Simulated trading used to practice without submitting real-money orders. Paper fills may differ from live fills.",
  "Risk": "How much you could lose and how uncertain the result is. Short-dated options can lose value rapidly.",
  "0DTE": "An option that expires today. These positions can change value quickly and carry expiration and assignment risks.",
  "Autopilot": "A separate execution mode that can submit orders without asking each time. Choosing an experience level never enables it.",
};

export default function WelcomeBanner({ onDismiss }: { onDismiss: () => void }) {
  const onNav = useTerminalNav();
  const [level, setLevel] = useState<ExperienceLevel>(readExperienceLevel);
  const [expanded, setExpanded] = useState<string | null>(null);

  const choose = (next: ExperienceLevel) => {
    setLevel(next);
    try { window.localStorage.setItem(EXPERIENCE_LEVEL_KEY, next); } catch { /* Browsers may block storage. */ }
    window.dispatchEvent(new CustomEvent("olbos:experience-change", { detail: next }));
  };

  const step = (n: string, title: string, detail: string, action?: { label: string; page: string }) => (
    <div key={n} style={{ display: "flex", gap: 12, padding: "12px 0", borderBottom: "1px solid var(--line-dim)" }}>
      <span aria-hidden="true" style={{ width: 26, height: 26, display: "grid", placeItems: "center", borderRadius: 13, flexShrink: 0, background: "var(--cyan-dim)", color: "var(--cyan)", fontWeight: 700 }}>{n}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontFamily: "var(--sans)", fontSize: 14, fontWeight: 650, color: "var(--ink)" }}>{title}</div>
        <p style={{ fontFamily: "var(--sans)", fontSize: 12, lineHeight: 1.6, color: "var(--ink-dim)", margin: "5px 0 0" }}>{detail}</p>
        {action && <button type="button" className="btn-t" onClick={() => onNav(action.page)} style={{ marginTop: 9, minHeight: 36 }}>{action.label} →</button>}
      </div>
    </div>
  );

  return (
    <section aria-label="Getting started with OlbosTrade" style={{ margin: "12px 16px 0", padding: "20px", background: "var(--bg-2)", border: "1px solid var(--line-dim)", borderRadius: 12 }}>
      <div style={{ display: "flex", gap: 14, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div className="kicker" style={{ marginBottom: 6 }}>WELCOME · GETTING STARTED</div>
          <h2 style={{ fontFamily: "var(--sans)", fontSize: 20, lineHeight: 1.3, color: "var(--ink)", margin: 0 }}>Make the terminal work for you</h2>
          <p style={{ color: "var(--ink-dim)", fontSize: 12, lineHeight: 1.6 }}>Tell us how familiar you are with investing. This changes the guidance you see, not your account's trading privileges.</p>
        </div>
        <button type="button" className="btn-t" onClick={onDismiss} aria-label="Dismiss getting started guide" style={{ minHeight: 40 }}>Dismiss guide</button>
      </div>

      <fieldset style={{ border: 0, padding: 0, margin: "12px 0 16px" }}>
        <legend style={{ color: "var(--ink)", fontSize: 13, fontWeight: 700, marginBottom: 9 }}>Choose your experience level</legend>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(165px, 1fr))", gap: 10 }}>
          {LEVELS.map(option => (
            <label key={option.id} style={{ display: "block", padding: 12, borderRadius: 9, border: `1px solid ${level === option.id ? "var(--cyan)" : "var(--line-dim)"}`, background: level === option.id ? "var(--cyan-dim)" : "var(--bg-3)", cursor: "pointer" }}>
              <div style={{ display: "flex", gap: 7, alignItems: "center" }}>
                <input type="radio" name="experience-level" value={option.id} checked={level === option.id} onChange={() => choose(option.id)} />
                <span style={{ fontSize: 13, fontWeight: 650, color: "var(--ink)" }}>{option.label}</span>
              </div>
              <div style={{ fontSize: 11, lineHeight: 1.5, color: "var(--ink-dim)", marginTop: 6 }}>{option.description}</div>
            </label>
          ))}
        </div>
      </fieldset>

      <div role="note" style={{ padding: "10px 12px", background: "var(--bg-3)", borderLeft: "3px solid var(--amber)", borderRadius: 5, color: "var(--ink-dim)", fontSize: 12, lineHeight: 1.6 }}>
        <strong style={{ color: "var(--ink)" }}>Safety first:</strong> This guide cannot enable live trading, Autopilot, or 0DTE execution. Practice trading does not use real money, and simulated results do not guarantee live performance.
      </div>

      {level === "beginner" ? (
        <>
          {step("1", "Start in a practice environment", "Learn the screens using a paper brokerage account. Verify PAPER status before interacting with any order controls.", { label: "Open Broker", page: "system:broker" })}
          {step("2", "Understand the difference between a signal and an order", "A signal is a model-based observation, not a promise of profit. Keep the separate execution mode on Manual.", { label: "Explore Signals", page: "equity" })}
          {step("3", "Review risk before learning options", "Focus on potential losses, expiration dates, spreads, and how much you could lose before practicing complex strategies.", { label: "View Risk", page: "risk" })}
        </>
      ) : level === "intermediate" ? (
        <>
          {step("1", "Confirm paper broker connectivity", "Check whether market data is timely and whether your account is configured for simulated orders.", { label: "Open Broker", page: "system:broker" })}
          {step("2", "Review signal explanations", "Compare model predictions, rejected signals, and downside scenarios. Signal scores are not probabilities of profit.", { label: "Explore Signals", page: "equity" })}
          {step("3", "Analyze portfolio exposure", "Examine position sizing, maximum drawdown, and concentration before reviewing execution controls.", { label: "View Risk", page: "risk" })}
        </>
      ) : (
        <>
          {step("1", "Check the execution environment", "Verify broker status, data freshness, reconciliation, account mode, and the kill switch.", { label: "Open Broker", page: "system:broker" })}
          {step("2", "Inspect model lineage", "Review feature inputs, predicted return-on-risk, uncertainty, and out-of-sample performance.", { label: "Explore Signals", page: "equity" })}
          {step("3", "Stress-test the strategy", "Review exposure, margin, partial fills, expiration behavior, and what happens when a connection fails.", { label: "View Risk", page: "risk" })}
        </>
      )}

      <div style={{ marginTop: 14 }}>
        <h3 style={{ color: "var(--ink)", fontFamily: "var(--sans)", fontSize: 14 }}>Quick financial glossary</h3>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {Object.entries(TERMS).map(([term, meaning]) => (
            <div key={term} style={{ flex: "1 1 150px", minWidth: 0 }}>
              <button type="button" aria-expanded={expanded === term} onClick={() => setExpanded(expanded === term ? null : term)} style={{ width: "100%", textAlign: "left", padding: "10px 12px", borderRadius: 7, border: "1px solid var(--line-dim)", background: "var(--bg-3)", color: "var(--ink)", cursor: "pointer" }}>{term} <span aria-hidden="true">{expanded === term ? "−" : "+"}</span></button>
              {expanded === term && <p style={{ color: "var(--ink-dim)", fontSize: 12, lineHeight: 1.6, margin: "8px 0" }}>{meaning}</p>}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
