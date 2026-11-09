import React, { useEffect, useState } from "react";
import { api } from "../api/client";
import { Button } from "./ui";
import { tint } from "../utils/tint";

interface ModeInfo {
  display_name: string; description: string; dte_range: string;
  risk_per_trade: string; requires_monitoring: boolean;
  monitoring_warning: string; ui_color: string; is_active: boolean;
  min_confidence?: number;
  hard_max_per_day?: number;
}

const COLOR: Record<string, string> = {
  green: "var(--green)", blue: "var(--accent)", orange: "var(--orange)", red: "var(--red)",
};

export default function TradingModeSelector() {
  const [modes, setModes]         = useState<Record<string, ModeInfo>>({});
  const [switching, setSwitching] = useState<string | null>(null);
  const [warning, setWarning]     = useState<{ mode: string; text: string } | null>(null);
  const [message, setMessage]     = useState<{ text: string; ok: boolean } | null>(null);

  const load = async () => {
    const d = await (api as any).getAllModes() as Record<string, ModeInfo>;
    setModes(d);
  };
  useEffect(() => { load(); }, []);

  const handleSelect = async (key: string, info: ModeInfo) => {
    if (info.is_active || switching) return;
    if (key === "scalper") { setWarning({ mode: key, text: info.monitoring_warning }); return; }
    await activate(key, false);
  };

  const activate = async (key: string, confirmed: boolean) => {
    setSwitching(key); setMessage(null);
    try {
      await (api as any).setTradingMode({ mode: key, confirmed });
      setMessage({ text: `Switched to ${modes[key]?.display_name}. Takes effect on next signal cycle.`, ok: true });
      await load();
    } catch {
      setMessage({ text: "Failed to switch mode.", ok: false });
    } finally { setSwitching(null); setWarning(null); }
  };

  const ORDER = ["conservative", "balanced", "aggressive", "scalper"];

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 14 }}>
        {ORDER.map(key => {
          const info = modes[key]; if (!info) return null;
          const color = COLOR[info.ui_color] || "var(--accent)";
          const active = info.is_active;
          return (
            <button
              key={key}
              onClick={() => handleSelect(key, info)}
              disabled={!!switching}
              className={`instrument-mode-card${active ? " is-active" : ""}`}
              style={{
                cursor: active ? "default" : "pointer",
                borderLeft: `3px solid ${active ? color : "transparent"}`,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 5 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: active ? color : "var(--ink)" }}>
                  {info.display_name}
                </span>
                {active && (
                  <span style={{ fontSize: 10, padding: "1px 7px", borderRadius: 3,
                    background: tint(color, 0.125), color, border: `1px solid ${tint(color, 0.251)}` }}>
                    Active
                  </span>
                )}
              </div>
              <div className="tnum" style={{ fontSize: 12, color: "var(--ink-dim)", marginBottom: 5 }}>
                {info.dte_range}
              </div>
              <div style={{ fontSize: 12, color: "var(--ink-faint)", lineHeight: 1.6, marginBottom: 8 }}>
                {info.description}
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <span className="tnum" style={{ fontSize: 11, padding: "1px 7px", borderRadius: 3,
                  border: "1px solid var(--line-dim)", color: "var(--ink-dim)" }}>
                  {info.risk_per_trade}/trade
                </span>
                {typeof info.min_confidence === "number" && (
                  <span className="tnum" style={{ fontSize: 11, padding: "1px 7px", borderRadius: 3,
                    border: "1px solid var(--line-dim)", color: "var(--ink-dim)" }}>
                    Min score {(info.min_confidence * 100).toFixed(0)}
                  </span>
                )}
                {typeof info.hard_max_per_day === "number" && (
                  <span className="tnum" style={{ fontSize: 11, padding: "1px 7px", borderRadius: 3,
                    border: "1px solid var(--line-dim)", color: "var(--ink-dim)" }}>
                    Max {info.hard_max_per_day}/day
                  </span>
                )}
                {info.requires_monitoring && (
                  <span style={{ fontSize: 11, padding: "1px 7px", borderRadius: 3,
                    border: "1px solid rgba(239,68,68,0.4)", color: "var(--red)" }}>
                    Monitor
                  </span>
                )}
              </div>
            </button>
          );
        })}
      </div>

      {message && (
        <div style={{ padding: "10px 14px", marginBottom: 10, borderRadius: 4,
          background: message.ok ? "rgba(34,197,94,0.08)" : "rgba(239,68,68,0.08)",
          border: `1px solid ${message.ok ? "rgba(34,197,94,0.3)" : "rgba(239,68,68,0.3)"}`,
          fontSize: 12, color: message.ok ? "var(--green)" : "var(--red)" }}>
          {message.text}
        </div>
      )}

      {warning && (
        <div style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)",
          display: "flex", alignItems: "center", justifyContent: "center", zIndex: 200,
        }}>
          <div style={{ background: "var(--bg-2)", border: "1px solid rgba(239,68,68,0.5)", borderRadius: 8, maxWidth: 440, width: "100%", padding: 24 }}>
            <div style={{ fontSize: 14, color: "var(--red)", fontWeight: 600, marginBottom: 14 }}>
              Scalper mode warning
            </div>
            <div style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", borderRadius: 4, padding: 14, marginBottom: 16 }}>
              <p style={{ fontSize: 12, color: "var(--ink-dim)", whiteSpace: "pre-line", lineHeight: 1.7 }}>
                {warning.text}
              </p>
            </div>
            <div style={{ display: "flex", gap: 10 }}>
              <Button onClick={() => setWarning(null)} style={{ flex: 1 }}>
                Cancel
              </Button>
              <Button danger onClick={() => activate(warning.mode, true)} style={{ flex: 1 }}>
                Confirm scalper
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
