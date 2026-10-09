/**
 * Broker Gateway — live IBKR connection + capabilities from /api/market/broker.
 *
 * This is the PLATFORM's broker, configured by the operator through the
 * backend's environment, and this view is read-only. A user's own broker
 * account is a different thing entirely and lives in MyBrokers.tsx, on the
 * next tab — the line at the bottom of this page used to point at a
 * "Settings → Brokers" screen that did not exist.
 */
import React, { useEffect, useState } from "react";
import { Badge } from "../../components/ui";

interface BrokerStatus {
  broker: string;
  status: "connected" | "disconnected" | "error";
  paper_mode?: boolean;
  supports_options?: boolean;
  supports_equities?: boolean;
  error?: string;
}

function StatusDot({ ok }: { ok: boolean }) {
  return <span className={`dot ${ok ? "live" : "dead"}`} style={{ marginRight: 8 }} />;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid var(--line-dim)" }}>
      <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-dim)", textTransform: "uppercase", letterSpacing: "0.06em" }}>{label}</span>
      <span style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{children}</span>
    </div>
  );
}

interface BrokerOption {
  id: string;
  label: string;
  model: string;
  equities: boolean;
  options: boolean;
}

const SUPPORTED_BROKERS: BrokerOption[] = [
  {
    id: "ibkr",
    label: "Interactive Brokers",
    model: "Persistent Gateway/TWS connection — one running process per account",
    equities: true,
    options: true,
  },
  {
    id: "alpaca",
    label: "Alpaca",
    model: "REST API, no per-account Gateway process — one API key pair per account",
    equities: true,
    options: true,
  },
];

function BrokerOptionRow({ opt, active }: { opt: BrokerOption; active: boolean }) {
  return (
    <div style={{
      display: "flex", alignItems: "flex-start", gap: 10, padding: "10px 0",
      borderBottom: "1px solid var(--line-dim)",
    }}>
      <span className={`dot ${active ? "live" : "dead"}`} style={{ marginTop: 4 }} />
      <div style={{ flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: 12, fontWeight: 700, color: "var(--ink)" }}>
            {opt.label}
          </span>
          {active && (
            <Badge kind="tag" tone="var(--green)" style={{
              border: "1px solid rgba(34,197,94,0.4)", padding: "1px 6px",
              textTransform: "uppercase", letterSpacing: "0.06em", opacity: 1,
            }}>
              active
            </Badge>
          )}
        </div>
        <div style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--ink-faint)", marginTop: 3 }}>
          {opt.model}
        </div>
      </div>
      <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
        {opt.equities && (
          <Badge kind="tag" tone="var(--ink-dim)" style={{ border: "1px solid var(--line-dim)", padding: "1px 6px", opacity: 1 }}>
            equities
          </Badge>
        )}
        {opt.options && (
          <Badge kind="tag" tone="var(--ink-dim)" style={{ border: "1px solid var(--line-dim)", padding: "1px 6px", opacity: 1 }}>
            options
          </Badge>
        )}
      </div>
    </div>
  );
}

export default function BrokerGateway() {
  const [b, setB] = useState<BrokerStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    fetch("/api/market/broker")
      .then(r => r.json())
      .then(d => { setB(d); setLoading(false); })
      .catch(() => { setB({ broker: "unknown", status: "error", error: "unreachable" }); setLoading(false); });
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  const connected = b?.status === "connected";

  return (
    <div style={{ padding: 16, height: "100%", overflowY: "auto" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 14, flexWrap: "wrap" }}>
        <span className="panel-title">Broker Gateway</span>
        <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)" }}>
          {loading ? "checking…" : "polls every 15s"}
        </span>
      </div>

      <div className="instrument-card" style={{ maxWidth: 520, padding: "12px 16px" }}>
        <div style={{ display: "flex", alignItems: "center", marginBottom: 8 }}>
          <StatusDot ok={connected} />
          <span style={{
            fontFamily: "var(--mono)", fontSize: 14, fontWeight: 600,
            color: connected ? "var(--green)" : b?.status === "error" ? "var(--red)" : "var(--amber)",
            textTransform: "uppercase", letterSpacing: "0.06em",
          }}>
            {b?.status ?? "…"}
          </span>
          {b?.paper_mode != null && (
            <Badge kind="tag" tone={b.paper_mode ? "var(--amber)" : "var(--cyan)"} style={{
              marginLeft: "auto", fontSize: 10,
              padding: "2px 7px", borderRadius: 2, textTransform: "uppercase", letterSpacing: "0.08em", opacity: 1,
            }}>
              {b.paper_mode ? "paper" : "live"}
            </Badge>
          )}
        </div>

        <Field label="Broker">{(b?.broker ?? "—").toUpperCase()}</Field>
        <Field label="Equities">{b?.supports_equities ? "supported" : "—"}</Field>
        <Field label="Options">{b?.supports_options ? "supported" : "—"}</Field>
        {b?.error && <Field label="Error"><span style={{ color: "var(--red)" }}>{b.error}</span></Field>}
      </div>

      <div style={{ marginTop: 14, fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)", borderLeft: "2px solid var(--line-dim)", paddingLeft: 10, maxWidth: 520 }}>
        This is the platform's own broker connection. To connect your own account, use the My Brokers tab. IBKR live ports are 4001 (Gateway) / 7496 (TWS); anything else is treated as paper.
      </div>

      <div style={{ marginTop: 24, maxWidth: 520 }}>
        <div className="panel-title" style={{ marginBottom: 10 }}>Supported Brokers</div>
        <div className="instrument-card" style={{ padding: "4px 16px" }}>
          {SUPPORTED_BROKERS.map(opt => (
            <BrokerOptionRow key={opt.id} opt={opt} active={(b?.broker ?? "").toLowerCase() === opt.id} />
          ))}
        </div>
        <div style={{ marginTop: 10, fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--ink-faint)", lineHeight: 1.6 }}>
          Switch broker via BROKER=ibkr|alpaca in the backend's .env, then restart. Alpaca is a
          plain REST API — no per-account Gateway process to run — which is why it's the
          practical choice for connecting many customers' own brokerage accounts.
        </div>
      </div>
    </div>
  );
}
