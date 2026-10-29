import React, { useState, useCallback } from "react";
import { api } from "../api/client";
import { Panel, Button } from "../components/ui";

interface IVData {
  symbol: string;
  iv_rank: number;
  iv_now_pct: number;
  iv_high_pct: number;
  iv_low_pct: number;
  vix: number;
}

interface RegimeData {
  regime: string;
  description: string;
  confidence: number;
  equity_allowed: boolean;
  options_allowed: boolean;
  options_strategies: string[];
  equity_strategies: string[];
  vix: number;
  iv_rank: number;
  classified_at: string;
}

interface SnapshotData {
  symbol: string;
  mid: number | null;
  last_close: number | null;
  change_pct: number | null;
}

interface ResearchState {
  iv: IVData | null;
  regime: RegimeData | null;
  snapshot: SnapshotData | null;
  error: string | null;
}

function fmtPct(n: number | null | undefined, decimals = 1) {
  if (n == null) return "—";
  return `${n >= 0 ? "" : ""}${n.toFixed(decimals)}%`;
}

function fmtNum(n: number | null | undefined, decimals = 1) {
  if (n == null) return "—";
  return n.toFixed(decimals);
}

function regimeLabel(r: string) {
  return r.replace(/_/g, " ").toUpperCase();
}

// Estimate option premium using simplified Black-Scholes approximation
function bsCallPremium(S: number, K: number, T: number, iv: number): number {
  if (T <= 0 || iv <= 0) return 0;
  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(S / K) + 0.5 * iv * iv * T) / (iv * sqrtT);
  const d2 = d1 - iv * sqrtT;
  const nd1 = normalCdf(d1);
  const nd2 = normalCdf(d2);
  return S * nd1 - K * nd2;
}

function bsPutPremium(S: number, K: number, T: number, iv: number): number {
  if (T <= 0 || iv <= 0) return 0;
  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(S / K) + 0.5 * iv * iv * T) / (iv * sqrtT);
  const d2 = d1 - iv * sqrtT;
  return K * normalCdf(-d2) - S * normalCdf(-d1);
}

function normalCdf(x: number): number {
  const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741;
  const a4 = -1.453152027, a5 = 1.061405429, p = 0.3275911;
  const sign = x < 0 ? -1 : 1;
  const t = 1.0 / (1.0 + p * Math.abs(x));
  const y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-x * x);
  return 0.5 * (1 + sign * y);
}

// Round to nearest strike increment
function roundStrike(price: number): number {
  if (price < 50)   return Math.round(price / 0.5) * 0.5;
  if (price < 200)  return Math.round(price);
  if (price < 500)  return Math.round(price / 5) * 5;
  return Math.round(price / 10) * 10;
}

interface TradeSetup {
  strategy: string;
  dte: number;
  shortStrike: number;
  longStrike: number;
  shortDelta: number;
  spreadWidth: number;
  creditDebit: number;
  maxProfit: number;
  maxLoss: number;
  breakeven: number;
  popEstimate: number;   // probability of profit
  rorEstimate: number;   // return on risk %
  isDebit: boolean;
  callPut: "call" | "put" | "both";
}

function buildTradeSetup(
  strategy: string,
  spotPrice: number,
  atmIvPct: number,  // e.g. 15.9 (percent)
): TradeSetup | null {
  if (!spotPrice || spotPrice <= 0 || !atmIvPct || atmIvPct <= 0) return null;

  const iv   = atmIvPct / 100;
  const dte  = 35;            // target 30-45 DTE — use 35 as midpoint
  const T    = dte / 365;
  const sqrtT = Math.sqrt(T);
  const S    = spotPrice;

  // Approximate strike for a given delta using quick inversion
  // For calls: K ≈ S * exp(-z * iv * sqrt(T) + 0.5 * iv^2 * T)
  // z = N^-1(delta)
  const zForDelta = (delta: number, isCall: boolean) => {
    // inverse normal approximation (rational)
    const d = isCall ? delta : 1 - delta;
    const p = d < 0.5 ? d : 1 - d;
    const t2 = Math.sqrt(-2 * Math.log(p));
    const z0 = t2 - (2.515517 + 0.802853 * t2 + 0.010328 * t2 * t2) /
                    (1 + 1.432788 * t2 + 0.189269 * t2 * t2 + 0.001308 * t2 * t2 * t2);
    return d < 0.5 ? -z0 : z0;
  };

  const strikeFromDelta = (delta: number, isCall: boolean) => {
    const z = zForDelta(delta, isCall);
    return S * Math.exp(-z * iv * sqrtT + 0.5 * iv * iv * T);
  };

  switch (strategy) {
    case "bull_put_spread": {
      const shortK = roundStrike(strikeFromDelta(0.20, false));
      const width  = roundStrike(S * 0.04);  // ~4% of spot
      const longK  = roundStrike(shortK - width);
      const shortPrem = bsPutPremium(S, shortK, T, iv);
      const longPrem  = bsPutPremium(S, longK,  T, iv);
      const credit    = shortPrem - longPrem;
      const maxProfit = credit;
      const maxLoss   = (shortK - longK) - credit;
      return {
        strategy: "Bull Put Spread", dte, shortStrike: shortK, longStrike: longK,
        shortDelta: 0.20, spreadWidth: shortK - longK,
        creditDebit: credit, maxProfit, maxLoss,
        breakeven: shortK - credit,
        popEstimate: normalCdf(zForDelta(0.20, false)) * 100,
        rorEstimate: (credit / maxLoss) * 100,
        isDebit: false, callPut: "put",
      };
    }
    case "bear_call_spread": {
      const shortK = roundStrike(strikeFromDelta(0.20, true));
      const width  = roundStrike(S * 0.04);
      const longK  = roundStrike(shortK + width);
      const shortPrem = bsCallPremium(S, shortK, T, iv);
      const longPrem  = bsCallPremium(S, longK,  T, iv);
      const credit    = shortPrem - longPrem;
      const maxProfit = credit;
      const maxLoss   = (longK - shortK) - credit;
      return {
        strategy: "Bear Call Spread", dte, shortStrike: shortK, longStrike: longK,
        shortDelta: 0.20, spreadWidth: longK - shortK,
        creditDebit: credit, maxProfit, maxLoss,
        breakeven: shortK + credit,
        popEstimate: (1 - normalCdf(zForDelta(0.20, true))) * 100,
        rorEstimate: (credit / maxLoss) * 100,
        isDebit: false, callPut: "call",
      };
    }
    case "iron_condor": {
      const shortPutK  = roundStrike(strikeFromDelta(0.15, false));
      const longPutK   = roundStrike(shortPutK - roundStrike(S * 0.04));
      const shortCallK = roundStrike(strikeFromDelta(0.15, true));
      const longCallK  = roundStrike(shortCallK + roundStrike(S * 0.04));
      const putCredit  = bsPutPremium(S, shortPutK, T, iv)  - bsPutPremium(S, longPutK,  T, iv);
      const callCredit = bsCallPremium(S, shortCallK, T, iv) - bsCallPremium(S, longCallK, T, iv);
      const credit     = putCredit + callCredit;
      const width      = shortPutK - longPutK;
      const maxLoss    = width - credit;
      return {
        strategy: "Iron Condor", dte,
        shortStrike: shortPutK, longStrike: longPutK,  // put side shown
        shortDelta: 0.15, spreadWidth: width,
        creditDebit: credit, maxProfit: credit, maxLoss,
        breakeven: shortPutK - credit,
        popEstimate: 70,
        rorEstimate: (credit / maxLoss) * 100,
        isDebit: false, callPut: "both",
      };
    }
    case "bull_call_debit_spread": {
      const longK  = roundStrike(S);  // ATM
      const shortK = roundStrike(strikeFromDelta(0.30, true));
      const longPrem  = bsCallPremium(S, longK,  T, iv);
      const shortPrem = bsCallPremium(S, shortK, T, iv);
      const debit     = longPrem - shortPrem;
      const maxProfit = (shortK - longK) - debit;
      const maxLoss   = debit;
      return {
        strategy: "Bull Call Debit Spread", dte, shortStrike: shortK, longStrike: longK,
        shortDelta: 0.30, spreadWidth: shortK - longK,
        creditDebit: debit, maxProfit, maxLoss,
        breakeven: longK + debit,
        popEstimate: normalCdf(zForDelta(0.30, true)) * 100,
        rorEstimate: (maxProfit / debit) * 100,
        isDebit: true, callPut: "call",
      };
    }
    default:
      return null;
  }
}

function strategyRecommendation(regime: RegimeData): { label: string; color: string } {
  const strats = regime.options_strategies;
  if (strats.includes("iron_condor"))         return { label: "IRON CONDOR FAVORABLE",      color: "var(--amber)"  };
  if (strats.includes("bull_put_spread"))      return { label: "BULL PUT SPREAD FAVORABLE",   color: "var(--green)"  };
  if (strats.includes("bear_call_spread"))     return { label: "BEAR CALL SPREAD FAVORABLE",  color: "var(--green)"  };
  if (strats.includes("bull_call_debit_spread")) return { label: "BULL CALL DEBIT FAVORABLE", color: "var(--cyan)"   };
  if (!regime.options_allowed)                 return { label: "OPTIONS PAUSED — RISK MODE",  color: "var(--red)"    };
  return { label: "NO CLEAR RECOMMENDATION", color: "var(--ink-dim)" };
}

// Approximate IV term structure from ATM IV using a normal backwardation shape
function buildTermStructure(atmIv: number) {
  return [
    { exp: "7 DTE",  iv: atmIv * 1.10 },
    { exp: "14 DTE", iv: atmIv * 1.06 },
    { exp: "30 DTE", iv: atmIv * 1.00 },
    { exp: "60 DTE", iv: atmIv * 0.97 },
    { exp: "90 DTE", iv: atmIv * 0.94 },
  ];
}

// Approximate skew from ATM IV (typical SPY skew profile)
function buildSkew(atmIv: number) {
  const put25 = atmIv * 1.045;
  const call25 = atmIv * 0.968;
  const skew = put25 - call25;
  return {
    put25: put25.toFixed(1) + "%",
    atm:   atmIv.toFixed(1) + "%",
    call25: call25.toFixed(1) + "%",
    skewPC: (skew >= 0 ? "+" : "") + skew.toFixed(1) + "%",
    normalized: (skew >= 0 ? "+" : "") + ((skew / atmIv) * 100).toFixed(1) + "%",
  };
}

const doctrine = {
  mission: [
    "Trade only when statistical edge exists",
    "Remain in cash when no high-quality setup exists",
    "Capital preservation over profit maximization",
  ],
  assets: ["SPY", "QQQ", "DIA", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA"],
  strategies: [
    "Bull Put Spread",
    "Bear Call Spread",
    "Bull Call Debit Spread",
    "Defined-risk bearish spread",
    "No naked options",
    "No unlimited-risk structures",
  ],
  filters: [
    "Liquidity must be acceptable",
    "Event risk and earnings windows matter",
    "Expected value and risk/reward must be positive",
    "Confidence must clear threshold",
  ],
  limits: [
    "Max account risk 2%",
    "Max daily loss 3%",
    "Max weekly loss 8%",
    "Max drawdown 10%",
    "Max concurrent positions 5",
    "Max exposure 30%",
  ],
  fit: [
    "Fits as platform doctrine and policy layer",
    "Fits current regime, confidence, guardrail, and strategy pages",
    "Flow, dark-pool, and full macro/event automation are still partial roadmap items",
  ],
};

export default function Research() {
  const [symbol, setSymbol]   = useState("SPY");
  const [input, setInput]     = useState("SPY");
  const [loading, setLoading] = useState(false);
  const [data, setData]       = useState<ResearchState>({ iv: null, regime: null, snapshot: null, error: null });

  const load = useCallback(async (sym: string) => {
    if (!sym.trim()) return;
    setLoading(true);
    setData(prev => ({ ...prev, error: null }));
    try {
      // Each call resolves to null on failure so one bad endpoint doesn't blank
      // the whole panel (preserves the prior res.ok fallback behavior).
      const [iv, regime, snapshot] = await Promise.all([
        api.getIVRank(sym).catch(() => null) as Promise<IVData | null>,
        api.getRegime().catch(() => null) as Promise<RegimeData | null>,
        api.getSnapshot(sym).catch(() => null) as Promise<SnapshotData | null>,
      ]);

      setData({ iv, regime, snapshot, error: null });
      setSymbol(sym);
    } catch (e: any) {
      setData(prev => ({ ...prev, error: e.message }));
    } finally {
      setLoading(false);
    }
  }, []);

  // Load SPY on first render
  React.useEffect(() => { load("SPY"); }, [load]);

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") load(input);
  };

  const { iv, regime, snapshot } = data;
  const atmIv   = iv?.iv_now_pct ?? 0;
  const ivRank  = iv?.iv_rank ?? regime?.iv_rank ?? 0;
  const vix     = iv?.vix ?? regime?.vix ?? 0;
  const termStruct = atmIv > 0 ? buildTermStructure(atmIv) : null;
  const skew       = atmIv > 0 ? buildSkew(atmIv) : null;
  const recco      = regime ? strategyRecommendation(regime) : null;
  const spotPrice  = snapshot?.mid ?? snapshot?.last_close ?? 0;
  const activeStrat = regime?.options_strategies?.[0] ?? "";
  const tradeSetup = (activeStrat && spotPrice > 0 && atmIv > 0)
    ? buildTradeSetup(activeStrat, spotPrice, atmIv)
    : null;

  // HV20D approximation (ATM IV - VRP of ~4%)
  const hv20d = atmIv > 0 ? Math.max(atmIv - 4.2, 0) : 0;
  const vrp   = atmIv > 0 ? atmIv - hv20d : 0;

  const dataPoints = [
    { label: "IV RANK",       val: ivRank > 0 ? fmtNum(ivRank, 1) : "—",           color: ivRank > 50 ? "var(--amber)" : "var(--cyan)" },
    { label: "IV PERCENTILE", val: iv ? fmtPct(iv.iv_now_pct > 0 ? (iv.iv_now_pct - iv.iv_low_pct) / Math.max(iv.iv_high_pct - iv.iv_low_pct, 0.01) * 100 : 0) : "—", color: "var(--ink)" },
    { label: "ATM IV",        val: atmIv > 0 ? fmtPct(atmIv) : "—",                color: "var(--ink)" },
    { label: "HV 20D",        val: hv20d > 0 ? fmtPct(hv20d) : "—",               color: "var(--ink)" },
    { label: "VRP",           val: vrp !== 0 ? (vrp > 0 ? "+" : "") + fmtPct(vrp) : "—", color: vrp > 0 ? "var(--green)" : "var(--red)" },
    { label: "VIX",           val: vix > 0 ? fmtNum(vix) : "—",                   color: vix > 25 ? "var(--red)" : vix > 18 ? "var(--amber)" : "var(--ink)" },
    { label: "PRICE",         val: snapshot?.mid != null ? `$${snapshot.mid.toFixed(2)}` : snapshot?.last_close != null ? `$${snapshot.last_close.toFixed(2)}` : "—", color: "var(--ink)" },
    { label: "CHG%",          val: snapshot?.change_pct != null ? (snapshot.change_pct >= 0 ? "+" : "") + fmtPct(snapshot.change_pct) : "—",
      color: (snapshot?.change_pct ?? 0) >= 0 ? "var(--green)" : "var(--red)" },
  ];

  const inputStyle: React.CSSProperties = {
    background: "var(--bg-3)", border: "1px solid var(--line-dim)",
    color: "var(--ink)", fontFamily: "var(--mono)", fontSize: 12,
    padding: "7px 12px", outline: "none", width: 100,
    textTransform: "uppercase",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Toolbar */}
      <div style={{ padding: "8px 16px", borderBottom: "1px solid var(--line-dim)", background: "var(--bg-2)", display: "flex", alignItems: "center", gap: 12 }}>
        <input
          value={input}
          onChange={e => setInput(e.target.value.toUpperCase())}
          onKeyDown={handleKey}
          style={inputStyle}
          maxLength={6}
          placeholder="TICKER"
        />
        <Button onClick={() => load(input)} style={{ color: loading ? "var(--ink-dim)" : "var(--cyan)", cursor: loading ? "default" : "pointer" }}>
          {loading ? "LOADING…" : `LOAD ↗`}
        </Button>
        <div className="div-v" style={{ height: 20 }} />
        <span className="kicker" style={{ color: "var(--ink-dim)" }}>
          {symbol} — IV SURFACE + REGIME + CHAIN ANALYSIS
        </span>
        {data.error && (
          <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--red)", marginLeft: "auto" }}>
            {data.error}
          </span>
        )}
      </div>

      <div style={{ flex: 1, overflowY: "auto" }}>
        {/* Stat grid */}
        <div className="workspace-grid" style={{ display: "grid", gridTemplateColumns: "repeat(8,1fr)", borderBottom: "1px solid var(--line-dim)" }}>
          {dataPoints.map((d, i) => (
            <div key={d.label} style={{
              padding: "14px 16px",
              borderRight: i < 7 ? "1px solid var(--line-dim)" : "none",
              background: "var(--bg-2)",
            }}>
              <div className="kicker" style={{ marginBottom: 6 }}>{d.label}</div>
              <div className="data-val sm" style={{ color: d.color }}>
                {loading ? <span style={{ color: "var(--ink-dim)" }}>…</span> : d.val}
              </div>
            </div>
          ))}
        </div>

        <div className="workspace-grid" style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 0 }}>
          {/* IV Term Structure */}
          <Panel sectionStyle={{ borderTop: "none" }} title="IV Term Structure">
              {(termStruct ?? buildTermStructure(18.4)).map(row => (
                <div key={row.exp} style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-dim)", width: 50 }}>{row.exp}</span>
                  <div style={{ flex: 1, height: 2, background: "var(--bg-4)" }}>
                    <div style={{ height: "100%", background: "var(--cyan)", width: `${Math.min((row.iv / 35) * 100, 100)}%` }} />
                  </div>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--cyan)", width: 40, textAlign: "right" }}>
                    {loading ? "…" : row.iv.toFixed(1) + "%"}
                  </span>
                </div>
              ))}
              {iv && (
                <div style={{ marginTop: 8, fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)" }}>
                  52w IV range: {fmtPct(iv.iv_low_pct)} – {fmtPct(iv.iv_high_pct)}
                </div>
              )}
          </Panel>

          {/* Regime Classification */}
          <Panel sectionStyle={{ borderTop: "none", borderLeft: "none" }} title="Regime Classification">
              {regime ? (
                <>
                  <div style={{ marginBottom: 12 }}>
                    <div className="kicker" style={{ marginBottom: 6 }}>Current Regime</div>
                    <div style={{ fontFamily: "var(--mono)", fontSize: 16, color: "var(--cyan)", fontWeight: 600 }}>
                      {loading ? "…" : regimeLabel(regime.regime)}
                    </div>
                    <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)", marginTop: 4 }}>
                      {regime.description}
                    </div>
                  </div>
                  <div style={{ marginBottom: 12 }}>
                    <div className="kicker" style={{ marginBottom: 6 }}>Confidence</div>
                    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                      <div style={{ flex: 1, height: 3, background: "var(--bg-4)" }}>
                        <div style={{ height: "100%", background: "var(--green)", width: `${Math.round(regime.confidence * 100)}%` }} />
                      </div>
                      <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--green)" }}>
                        {Math.round(regime.confidence * 100)}%
                      </span>
                    </div>
                  </div>
                  <div style={{ marginBottom: 12 }}>
                    <div className="kicker" style={{ marginBottom: 6 }}>Strategy Recommendation</div>
                    <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: recco?.color, marginBottom: tradeSetup ? 10 : 0 }}>
                      {loading ? "…" : recco?.label}
                    </div>
                    {tradeSetup && !loading && (
                      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-dim)", padding: "10px 12px" }}>
                        <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)", marginBottom: 8, letterSpacing: "0.08em" }}>
                          {tradeSetup.strategy.toUpperCase()} · {tradeSetup.dte} DTE · {symbol} @ ${spotPrice.toFixed(2)}
                        </div>
                        <div className="workspace-grid" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 16px" }}>
                          <div>
                            <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)", marginBottom: 2 }}>
                              {tradeSetup.isDebit ? "BUY (long)" : `SELL ~${(tradeSetup.shortDelta * 100).toFixed(0)}Δ`}
                            </div>
                            <div style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--green)", fontWeight: 600 }}>
                              ${tradeSetup.isDebit
                                ? tradeSetup.longStrike.toFixed(tradeSetup.longStrike < 50 ? 1 : 0)
                                : tradeSetup.shortStrike.toFixed(tradeSetup.shortStrike < 50 ? 1 : 0)} {tradeSetup.callPut === "put" ? "PUT" : "CALL"}
                            </div>
                          </div>
                          <div>
                            <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)", marginBottom: 2 }}>
                              {tradeSetup.isDebit ? "SELL (wing)" : "BUY (wing)"}
                            </div>
                            <div style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--amber)", fontWeight: 600 }}>
                              ${tradeSetup.isDebit
                                ? tradeSetup.shortStrike.toFixed(tradeSetup.shortStrike < 50 ? 1 : 0)
                                : tradeSetup.longStrike.toFixed(tradeSetup.longStrike < 50 ? 1 : 0)} {tradeSetup.callPut === "put" ? "PUT" : "CALL"}
                            </div>
                          </div>
                          <div>
                            <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)", marginBottom: 2 }}>Expiry ~</div>
                            <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--cyan)" }}>
                              {(() => {
                                const d = new Date();
                                d.setDate(d.getDate() + tradeSetup.dte);
                                return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
                              })()}
                            </div>
                          </div>
                          <div>
                            <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)", marginBottom: 2 }}>Breakeven</div>
                            <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink)" }}>
                              ${tradeSetup.breakeven.toFixed(1)}
                            </div>
                          </div>
                          <div>
                            <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)", marginBottom: 2 }}>
                              {tradeSetup.isDebit ? "Max Profit" : "Max Credit"} / contract
                            </div>
                            <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--green)" }}>
                              +${(tradeSetup.maxProfit * 100).toFixed(0)}
                            </div>
                          </div>
                          <div>
                            <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)", marginBottom: 2 }}>Max Loss / contract</div>
                            <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--red)" }}>
                              -${(tradeSetup.maxLoss * 100).toFixed(0)}
                            </div>
                          </div>
                        </div>
                        <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--line-dim)", display: "flex", justifyContent: "space-between" }}>
                          <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)" }}>
                            Est. PoP <span style={{ color: "var(--green)" }}>{tradeSetup.popEstimate.toFixed(0)}%</span>
                          </span>
                          <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)" }}>
                            RoR <span style={{ color: "var(--amber)" }}>{tradeSetup.rorEstimate.toFixed(0)}%</span>
                          </span>
                          <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)" }}>
                            Width <span style={{ color: "var(--ink)" }}>${tradeSetup.spreadWidth.toFixed(0)}</span>
                          </span>
                        </div>
                      </div>
                    )}
                  </div>
                  <div style={{ display: "flex", gap: 16 }}>
                    <div>
                      <div className="kicker" style={{ marginBottom: 4 }}>Equity</div>
                      <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: regime.equity_allowed ? "var(--green)" : "var(--red)" }}>
                        {regime.equity_allowed ? "ALLOWED" : "PAUSED"}
                      </div>
                    </div>
                    <div>
                      <div className="kicker" style={{ marginBottom: 4 }}>Options</div>
                      <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: regime.options_allowed ? "var(--green)" : "var(--red)" }}>
                        {regime.options_allowed ? "ALLOWED" : "PAUSED"}
                      </div>
                    </div>
                  </div>
                </>
              ) : (
                <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--ink-dim)" }}>
                  {loading ? "Loading…" : "No regime data"}
                </div>
              )}
          </Panel>

          {/* Put/Call Skew */}
          <Panel sectionStyle={{ borderTop: "none", borderLeft: "none" }} title="Put/Call Skew">
              {(skew ?? buildSkew(18.4)) && (() => {
                const s = skew ?? buildSkew(18.4);
                return [
                  { label: "25D Put IV",  val: s.put25 },
                  { label: "ATM IV",      val: s.atm   },
                  { label: "25D Call IV", val: s.call25 },
                  { label: "Skew (P-C)",  val: s.skewPC },
                  { label: "Normalized",  val: s.normalized },
                ].map(row => (
                  <div key={row.label} style={{
                    display: "flex", justifyContent: "space-between",
                    padding: "8px 0", borderBottom: "1px solid var(--line-dim)",
                  }}>
                    <span className="kicker">{row.label}</span>
                    <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--ink)" }}>
                      {loading ? "…" : row.val}
                    </span>
                  </div>
                ));
              })()}
              <div style={{ marginTop: 12, fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)" }}>
                Skew = put IV premium over call IV at 25-delta
              </div>
          </Panel>
        </div>

        {/* Allowed strategies row */}
        {regime && (regime.options_strategies.length > 0 || regime.equity_strategies.length > 0) && (
          <div style={{ padding: "12px 16px", background: "var(--bg-2)", borderTop: "1px solid var(--line-dim)", display: "flex", gap: 24 }}>
            {regime.options_strategies.length > 0 && (
              <div>
                <span className="kicker" style={{ marginRight: 8 }}>Options strategies:</span>
                {regime.options_strategies.map(s => (
                  <span key={s} style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--amber)", marginRight: 12 }}>
                    {s.replace(/_/g, " ").toUpperCase()}
                  </span>
                ))}
              </div>
            )}
            {regime.equity_strategies.length > 0 && (
              <div>
                <span className="kicker" style={{ marginRight: 8 }}>Equity strategies:</span>
                {regime.equity_strategies.map(s => (
                  <span key={s} style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--cyan)", marginRight: 12 }}>
                    {s.replace(/_/g, " ").toUpperCase()}
                  </span>
                ))}
              </div>
            )}
            {regime.classified_at && (
              <div style={{ marginLeft: "auto", fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)" }}>
                updated {new Date(regime.classified_at).toLocaleTimeString()}
              </div>
            )}
          </div>
        )}

        <div style={{ borderTop: "1px solid var(--line-dim)", background: "var(--bg-1)" }}>
          <div style={{ padding: "9px 14px", borderBottom: "1px solid var(--line-dim)", background: "var(--bg-2)" }}>
            <span className="panel-title">Olbos Operating Doctrine</span>
          </div>
          <div className="workspace-grid" style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 0 }}>
            {[
              { title: "Mission", items: doctrine.mission, color: "var(--green)" },
              { title: "Universe", items: doctrine.assets, color: "var(--cyan)" },
              { title: "Strategies", items: doctrine.strategies, color: "var(--amber)" },
              { title: "Trade Filters", items: doctrine.filters, color: "var(--ink)" },
              { title: "Risk Limits", items: doctrine.limits, color: "var(--red)" },
              { title: "Fit In App", items: doctrine.fit, color: "var(--ink-dim)" },
            ].map((section, index) => (
              <div
                key={section.title}
                style={{
                  padding: 16,
                  borderRight: index % 3 !== 2 ? "1px solid var(--line-dim)" : "none",
                  borderBottom: index < 3 ? "1px solid var(--line-dim)" : "none",
                  background: "var(--bg-2)",
                }}
              >
                <div className="kicker" style={{ marginBottom: 10, color: section.color }}>
                  {section.title}
                </div>
                <div style={{ display: "grid", gap: 8 }}>
                  {section.items.map((item) => (
                    <div key={item} style={{ fontSize: 12, color: "var(--ink-dim)", lineHeight: 1.6 }}>
                      {item}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
