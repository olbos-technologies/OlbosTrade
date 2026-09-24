import React, { useEffect, useState } from "react";
import { api } from "../api/client";

type Health = {
  scanner?: { alive?: boolean; last_tick_age_seconds?: number | null };
  kill_switch?: { engaged?: boolean };
  database?: { connected?: boolean };
  ibkr?: { connected?: boolean; account_values_age_seconds?: number | null; account_values_stale?: boolean };
  regime?: string | null;
};

function age(value?: number | null) {
  return typeof value === "number" ? `${Math.round(value)}s ago` : "not reported";
}

export default function OperationsSnapshot() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const load = () => {
    setError(null);
    (api.getHealthDetail() as Promise<Health>)
      .then((data) => { setHealth(data); setUpdatedAt(new Date()); })
      .catch(() => setError("Operations status is unavailable. Verify the service connection, then retry."));
  };

  useEffect(() => {
    load();
    const id = window.setInterval(load, 15000);
    return () => window.clearInterval(id);
  }, []);

  const items = health ? [
    ["Broker", health.ibkr?.connected ? "connected" : "unavailable", health.ibkr?.connected],
    ["Scanner", health.scanner?.alive ? `live · ${age(health.scanner.last_tick_age_seconds)}` : "heartbeat missing", health.scanner?.alive],
    ["Market data", health.ibkr?.account_values_stale ? "stale" : age(health.ibkr?.account_values_age_seconds), !health.ibkr?.account_values_stale],
    ["Database", health.database?.connected ? "connected" : "unavailable", health.database?.connected],
    ["Kill switch", health.kill_switch?.engaged ? "engaged" : "not engaged", !health.kill_switch?.engaged],
    ["Regime", health.regime || "not classified", Boolean(health.regime)],
  ] as const : [];

  return (
    <section className="operations-snapshot" aria-label="Operations snapshot">
      <div className="operations-snapshot__header">
        <div><div className="kicker">Operational observability</div><strong>Live system status</strong></div>
        <button type="button" className="btn-ghost" onClick={load}>Refresh</button>
      </div>
      {error ? <div className="operations-snapshot__error" role="alert">{error}</div> : (
        <div className="operations-snapshot__grid">
          {items.map(([label, value, ok]) => <div key={label} className="operations-snapshot__item">
            <span>{label}</span><strong className={ok ? "is-ok" : "is-alert"}>{value}</strong>
          </div>)}
        </div>
      )}
      <p>{updatedAt ? `Updated ${updatedAt.toLocaleTimeString()} · refreshes every 15 seconds` : "Checking service status…"}</p>
    </section>
  );
}
