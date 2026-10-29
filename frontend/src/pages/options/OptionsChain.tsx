/**
 * Options Chain — live calls/puts for a symbol from the connected broker
 * (/api/market/options-chain/{symbol}). Requires a broker connection (IBKR);
 * shows the underlying error if the gateway is down.
 */
import React, { useEffect, useState } from "react";
import { Badge } from "../../components/ui";

interface Contract {
  strike: number; bid: number; ask: number; last: number;
  volume: number; open_interest: number;
  delta: number | null; gamma: number | null; theta: number | null; vega: number | null;
  iv: number | null;
}
type DataStatus = "LIVE" | "DEGRADED" | "STALE";
interface ChainResponse {
  symbol: string; expiry: string; underlying_price?: number;
  calls?: Contract[]; puts?: Contract[]; error?: string;
  data_status?: DataStatus;
}

const DATA_STATUS_COLOR: Record<DataStatus, string> = {
  LIVE: "var(--green)", DEGRADED: "var(--amber)", STALE: "var(--red)",
};

function ContractRow({ c, atm }: { c: Contract; atm: boolean }) {
  return (
    <tr style={atm ? { background: "var(--bg-3)" } : undefined}>
      <td className="mono" style={{ fontWeight: atm ? 600 : 400 }}>{c.strike}</td>
      <td className="mono">{c.bid.toFixed(2)}</td>
      <td className="mono">{c.ask.toFixed(2)}</td>
      <td className="mono">{c.last.toFixed(2)}</td>
      <td className="mono" style={{ color: "var(--ink-dim)" }}>{c.volume.toLocaleString("en-US")}</td>
      <td className="mono" style={{ color: "var(--ink-dim)" }}>{c.open_interest.toLocaleString("en-US")}</td>
      <td className="mono" style={{ color: "var(--cyan)" }}>{c.delta != null ? c.delta.toFixed(2) : "—"}</td>
      <td className="mono" style={{ color: "var(--ink-dim)" }}>{c.gamma != null ? c.gamma.toFixed(3) : "—"}</td>
      <td className="mono" style={{ color: c.theta != null && c.theta < 0 ? "var(--red)" : "var(--ink-dim)" }}>{c.theta != null ? c.theta.toFixed(3) : "—"}</td>
      <td className="mono" style={{ color: "var(--ink-dim)" }}>{c.vega != null ? c.vega.toFixed(3) : "—"}</td>
      <td className="mono" style={{ color: "var(--ink-dim)" }}>{c.iv != null ? `${(c.iv * 100).toFixed(0)}%` : "—"}</td>
    </tr>
  );
}

export default function OptionsChain({
  symbol: controlledSymbol,
  onSymbolChange,
}: {
  symbol?: string;
  onSymbolChange?: (symbol: string) => void;
} = {}) {
  const [internalSymbol, setInternalSymbol] = useState(controlledSymbol || "SPY");
  const symbol = controlledSymbol ?? internalSymbol;
  const setSymbol = (sym: string) => {
    if (controlledSymbol === undefined) setInternalSymbol(sym);
    onSymbolChange?.(sym);
  };
  const [input, setInput] = useState(symbol);
  const [data, setData] = useState<ChainResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setInput(symbol);
  }, [symbol]);

  const load = (sym: string) => {
    setLoading(true);
    fetch(`/api/market/options-chain/${encodeURIComponent(sym)}`)
      .then(r => r.json())
      .then(setData)
      .catch(() => setData({ symbol: sym, expiry: "", error: "unreachable" }))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(symbol); }, [symbol]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const sym = input.trim().toUpperCase();
    if (sym) setSymbol(sym);
  };

  const spot = data?.underlying_price ?? null;
  const nearestStrike = (rows: Contract[] | undefined) =>
    !rows || spot == null ? null
      : rows.reduce((a, b) => Math.abs(b.strike - spot) < Math.abs(a.strike - spot) ? b : a).strike;
  const atmCallStrike = nearestStrike(data?.calls);
  const atmPutStrike  = nearestStrike(data?.puts);

  const cols = ["Strike", "Bid", "Ask", "Last", "Vol", "OI", "Delta", "Gamma", "Theta", "Vega", "IV"];

  return (
    <div className="page-shell" style={{ height: "100%", overflowY: "auto" }}>
      <div className="instrument-card page-header">
        <div>
          <div className="page-header__title">Options Chain</div>
          <p className="page-header__sub">Live broker chain · calls & puts</p>
        </div>
        <form onSubmit={submit} style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder="Symbol"
            className="control-input"
            style={{ width: 90, textTransform: "uppercase" }}
          />
          <button type="submit" className="btn-primary" style={{ padding: "6px 12px" }}>Load</button>
        </form>
        {spot != null && (
          <span className="mono" style={{ fontSize: 12, color: "var(--accent)" }}>
            {data?.symbol} spot {spot.toFixed(2)}
          </span>
        )}
        {data?.expiry && (
          <span className="mono" style={{ fontSize: 10, color: "var(--ink-faint)" }}>
            exp {data.expiry}
          </span>
        )}
        {data?.data_status && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, marginLeft: "auto" }}>
            <span className="kicker">Options data</span>
            <Badge kind="tag" tone={DATA_STATUS_COLOR[data.data_status]}>{data.data_status}</Badge>
          </span>
        )}
      </div>

      {loading ? (
        <div className="instrument-card instrument-card--flat empty-chassis">
          <p className="empty-chassis__title">Loading chain…</p>
        </div>
      ) : data?.error ? (
        <div className="instrument-card instrument-card--flat empty-chassis">
          <p className="empty-chassis__title" style={{ color: "var(--red)" }}>{data.error}</p>
          <p className="empty-chassis__hint">Check broker connection in Data & Integrations → Broker Gateway.</p>
        </div>
      ) : (
        <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
          <div className="instrument-card" style={{ flex: 1, minWidth: 340, padding: 12 }}>
            <div className="kicker" style={{ color: "var(--green)", marginBottom: 8, letterSpacing: "0.1em", textTransform: "uppercase" }}>Calls</div>
            <div className="workspace-table-scroll"><table className="t-table">
              <thead><tr>{cols.map(h => <th key={h}>{h}</th>)}</tr></thead>
              <tbody>
                {(data?.calls ?? []).map(c => <ContractRow key={c.strike} c={c} atm={c.strike === atmCallStrike} />)}
              </tbody>
            </table></div>
          </div>
          <div className="instrument-card" style={{ flex: 1, minWidth: 340, padding: 12 }}>
            <div className="kicker" style={{ color: "var(--red)", marginBottom: 8, letterSpacing: "0.1em", textTransform: "uppercase" }}>Puts</div>
            <div className="workspace-table-scroll"><table className="t-table">
              <thead><tr>{cols.map(h => <th key={h}>{h}</th>)}</tr></thead>
              <tbody>
                {(data?.puts ?? []).map(p => <ContractRow key={p.strike} c={p} atm={p.strike === atmPutStrike} />)}
              </tbody>
            </table></div>
          </div>
        </div>
      )}
    </div>
  );
}
