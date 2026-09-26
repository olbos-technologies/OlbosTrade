/**
 * CryptoSignals — read-only crypto signal feed (phase 1).
 *
 * The most important thing on this page is the banner saying nothing here
 * trades. The cards look exactly like the equity signal cards next door, and
 * those DO feed an execution path — so without the banner the visual similarity
 * would itself be the misleading part. The backend reports `execution` on both
 * endpoints so this claim is read from the API rather than hardcoded in the UI:
 * if execution is ever enabled, the banner changes with it instead of going
 * stale and lying.
 *
 * Built as cards rather than a table on purpose. The mobile work in #82 landed
 * because the position table was unreadable on a phone; starting a new surface
 * as a table would have re-created the problem it just fixed.
 */

import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { CryptoScanSummary, CryptoSignal, CryptoWatchlist } from "../api/client";
import { Badge, Button, EmptyState, Panel, StatTile } from "../components/ui";

function pct(n: number | undefined): string {
  return n == null ? "—" : `${(n * 100).toFixed(0)}%`;
}

/**
 * Crypto spans four orders of magnitude of price (BTC ~110,000, DOGE ~0.15), so
 * a fixed decimal count is wrong at one end or the other: 2dp erases DOGE's
 * moves, 4dp makes BTC unreadable. Scale the precision to the magnitude.
 */
function price(n: number | undefined): string {
  if (n == null) return "—";
  if (n >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (n >= 1) return n.toFixed(2);
  return n.toFixed(4);
}

function tone(action: string): "bullish" | "bearish" | "neutral" {
  if (action === "BUY") return "bullish";
  if (action === "SELL") return "bearish";
  return "neutral";
}

export default function CryptoSignals() {
  const [signals, setSignals] = useState<CryptoSignal[]>([]);
  const [watchlist, setWatchlist] = useState<CryptoWatchlist | null>(null);
  const [scanning, setScanning] = useState(false);
  const [summary, setSummary] = useState<CryptoScanSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    try {
      const [feed, wl] = await Promise.all([
        api.getCryptoSignals({ limit: 60 }),
        api.getCryptoWatchlist(),
      ]);
      setSignals(feed.signals || []);
      setWatchlist(wl);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load crypto signals");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function scanNow() {
    setScanning(true);
    try {
      setSummary(await api.runCryptoScan());
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Scan failed");
    } finally {
      setScanning(false);
    }
  }

  const routable = signals.filter((s) => s.routable).length;
  const executes = watchlist ? watchlist.execution !== "disabled" : false;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* Read from the API, not hardcoded — see the file docstring. */}
      <div
        style={{
          border: `1px solid ${executes ? "var(--danger)" : "var(--accent)"}`,
          background: "var(--bg-2)",
          padding: "10px 12px",
          fontFamily: "var(--mono)",
          fontSize: 11,
          lineHeight: 1.6,
        }}
      >
        <strong style={{ letterSpacing: "0.06em" }}>
          {executes ? "CRYPTO EXECUTION ENABLED" : "SIGNALS ONLY — NO EXECUTION"}
        </strong>
        <div style={{ opacity: 0.8, marginTop: 4 }}>
          {executes
            ? "Crypto signals can reach the order layer. Check your risk limits."
            : "Crypto signals are recorded and tracked for forward outcomes. Nothing here " +
              "places, sizes or closes an order — the scan has no path to the order layer."}
        </div>
      </div>

      <Panel
        title="Crypto Signals"
        action={
          <Button size="sm" onClick={scanNow} disabled={scanning}>
            {scanning ? "Scanning…" : "Scan now"}
          </Button>
        }
      >
        <div
          style={{
            display: "grid",
            // minmax(0, …) not 1fr: a grid track's automatic minimum is its
            // min-content width, which is what pushed these tiles off-screen on
            // a phone before #82.
            gridTemplateColumns: "repeat(auto-fit, minmax(min(140px, 100%), 1fr))",
            gap: 8,
          }}
        >
          <StatTile label="Signals" value={String(signals.length)} />
          <StatTile label="Actionable" value={String(routable)} />
          <StatTile label="Symbols" value={String(watchlist?.count ?? "—")} />
          <StatTile label="Min confidence" value={pct(watchlist?.min_confidence)} />
        </div>

        {summary && (
          <div
            style={{
              marginTop: 10,
              fontFamily: "var(--mono)",
              fontSize: 10,
              opacity: 0.75,
              lineHeight: 1.7,
            }}
          >
            Last scan — scanned {summary.scanned}, signals {summary.signals}, actionable{" "}
            {summary.routable}, recorded {summary.recorded}
            {summary.skipped_insufficient_bars > 0 &&
              `, skipped (bars) ${summary.skipped_insufficient_bars}`}
            {summary.skipped_unrepresentable_price > 0 &&
              `, skipped (price precision) ${summary.skipped_unrepresentable_price}`}
            {summary.errors > 0 && `, errors ${summary.errors}`}
          </div>
        )}

        {watchlist && (
          <div style={{ marginTop: 10, fontFamily: "var(--mono)", fontSize: 10, opacity: 0.6 }}>
            {watchlist.data_source} · engine v{watchlist.engine_version} · every{" "}
            {watchlist.scan_interval_minutes} min
          </div>
        )}
      </Panel>

      {error && (
        <div style={{ color: "var(--danger)", fontFamily: "var(--mono)", fontSize: 11 }}>
          {error}
        </div>
      )}

      {loaded && signals.length === 0 && !error && (
        <EmptyState>
          No crypto signals yet. The background scan runs every{" "}
          {watchlist?.scan_interval_minutes ?? 30} minutes — or press “Scan now”.
        </EmptyState>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(min(280px, 100%), 1fr))",
          gap: 10,
        }}
      >
        {signals.map((s) => (
          <Panel key={s.id} padding={12}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                flexWrap: "wrap",
                marginBottom: 8,
              }}
            >
              <span style={{ fontFamily: "var(--mono)", fontWeight: 600, fontSize: 13 }}>
                {s.ticker}
              </span>
              <Badge kind="signal" tone={tone(s.action)}>
                {s.action}
              </Badge>
              <span style={{ fontFamily: "var(--mono)", fontSize: 10, opacity: 0.7 }}>
                {pct(s.confidence)}
              </span>
              {s.routable ? (
                <Badge kind="tag" tone="var(--accent)">
                  TRACKED
                </Badge>
              ) : (
                <Badge kind="tag" tone="var(--text-3)">
                  BELOW FLOOR
                </Badge>
              )}
            </div>

            {s.trade_plan?.entry_price != null ? (
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
                  gap: 6,
                  fontFamily: "var(--mono)",
                  fontSize: 10,
                }}
              >
                <div>
                  <div style={{ opacity: 0.55 }}>ENTRY</div>
                  <div>{price(s.trade_plan.entry_price)}</div>
                </div>
                <div>
                  <div style={{ opacity: 0.55 }}>STOP</div>
                  <div style={{ color: "var(--danger)" }}>{price(s.trade_plan.stop_price)}</div>
                </div>
                <div>
                  <div style={{ opacity: 0.55 }}>TARGET</div>
                  <div style={{ color: "var(--success)" }}>
                    {price(s.trade_plan.target_price)}
                  </div>
                </div>
              </div>
            ) : (
              <div style={{ fontFamily: "var(--mono)", fontSize: 10, opacity: 0.55 }}>
                No trade plan — below the confidence floor, so none was computed.
              </div>
            )}

            <div
              style={{
                marginTop: 8,
                display: "flex",
                gap: 10,
                flexWrap: "wrap",
                fontFamily: "var(--mono)",
                fontSize: 9,
                opacity: 0.65,
              }}
            >
              {s.indicators?.rsi != null && <span>RSI {s.indicators.rsi.toFixed(0)}</span>}
              {s.indicators?.volume_ratio != null && (
                <span>VOL {s.indicators.volume_ratio.toFixed(2)}x</span>
              )}
              {s.opportunity_score && <span>OPP {s.opportunity_score.score}</span>}
              <span>{new Date(s.generated_at).toLocaleTimeString()}</span>
            </div>
          </Panel>
        ))}
      </div>
    </div>
  );
}
