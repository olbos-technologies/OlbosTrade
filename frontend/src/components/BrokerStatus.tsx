/**
 * BrokerStatus — shows active broker name, connection status, paper vs live.
 */

import React, { useEffect, useState } from "react";

interface BrokerInfo {
  broker: string;
  supports_options: boolean;
  supports_equities: boolean;
  paper_mode: boolean;
  status: string;
  error?: string;
}

export default function BrokerStatus() {
  const [unavailable, setUnavailable] = useState(false);
  const [info, setInfo] = useState<BrokerInfo | null>(null);

  useEffect(() => {
    const load = () =>
      fetch("/api/market/broker")
        .then(r => { if (!r.ok) throw new Error("Broker unavailable"); return r.json(); })
        .then(d => { if (typeof d?.broker !== "string" || typeof d.paper_mode !== "boolean" || typeof d.status !== "string") throw new Error("Broker unavailable"); setInfo(d); setUnavailable(false); })
        .catch(() => { setInfo(null); setUnavailable(true); });
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, []);

  if (!info) return unavailable ? <p role="status">Broker connection unavailable</p> : null;

  const connected = info.status === "connected";
  const modeLabel = info.paper_mode ? "paper" : "live";
  const brokerLabel = info.broker.toUpperCase();

  return (
    <div
      className="instrument-card"
      style={{
        padding: "12px 16px",
        fontFamily: "var(--mono)",
        fontSize: 11,
        display: "flex",
        alignItems: "center",
        gap: 12,
        flexWrap: "wrap",
      }}
    >
      <span className={`dot ${connected ? "live" : "dead"}`} />
      <span style={{ color: "var(--cyan)", fontWeight: 600 }}>
        {brokerLabel}
      </span>
      <span style={{
        color: info.paper_mode ? "var(--amber)" : "var(--green)",
        border: `1px solid ${info.paper_mode ? "var(--amber)" : "var(--green)"}`,
        borderRadius: 2,
        padding: "1px 6px",
        fontSize: 9,
        letterSpacing: "0.1em",
        textTransform: "uppercase",
      }}>
        {modeLabel}
      </span>
      <span style={{ color: "var(--ink-dim)" }}>
        {connected ? "connected" : info.error || "disconnected"}
      </span>
    </div>
  );
}
