/**
 * SignalAttribution — the one place a directional signal (BUY/SELL/HOLD/
 * BULLISH/BEARISH/NEUTRAL) is allowed to render in this app. Every visible
 * directional badge must carry its source, timeframe, confidence, freshness,
 * and execution authority — or explicitly say which of those are unknown.
 *
 * Renders a compact row (only the attributes that are actually available,
 * joined with " · ") plus a native <details> disclosure with the full
 * breakdown, including explicit "unknown"/"unavailable" rows for anything
 * missing. Never infers or fabricates a missing field.
 */
import { CONFIDENCE_NOTE, confidenceScore, confidenceScoreText } from "../utils/confidenceScore";
import React from "react";

import type { SignalAttributionData, SignalAuthority } from "../types/signal";
import { tint } from "../utils/tint";

const DEFAULT_STALE_AFTER_MS = 15 * 60 * 1000; // 15 minutes

function directionTone(direction: string): string {
  const d = direction.toUpperCase();
  if (d === "BUY" || d === "BUY_SPREAD" || d === "BULLISH") return "var(--green)";
  if (d === "SELL" || d === "SELL_SPREAD" || d === "BEARISH") return "var(--red)";
  if (d === "HOLD" || d === "NEUTRAL") return "var(--ink-dim)";
  return "var(--amber)"; // unrecognized direction string — flag, don't hide
}

function authorityLabel(a: SignalAuthority | undefined): string {
  if (a === "execution") return "Execution-authoritative";
  if (a === "advisory") return "Advisory only";
  return "Execution authority is not available from the current frontend data.";
}

function isStale(data: SignalAttributionData, now: number): boolean {
  if (typeof data.stale === "boolean") return data.stale;
  if (!data.updatedAt) return false; // unknown freshness is reported separately, not treated as stale
  const t = Date.parse(data.updatedAt);
  if (Number.isNaN(t)) return false;
  return now - t > (data.staleAfterMs ?? DEFAULT_STALE_AFTER_MS);
}

function relativeAge(updatedAt: string | null | undefined, now: number): string | null {
  if (!updatedAt) return null;
  const t = Date.parse(updatedAt);
  if (Number.isNaN(t)) return null;
  const deltaS = Math.max(0, Math.round((now - t) / 1000));
  if (deltaS < 60) return `${deltaS}s ago`;
  if (deltaS < 3600) return `${Math.round(deltaS / 60)}m ago`;
  if (deltaS < 86400) return `${Math.round(deltaS / 3600)}h ago`;
  return `${Math.round(deltaS / 86400)}d ago`;
}

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "3px 0" }}>
      <span style={{ color: "var(--ink-faint)", fontSize: 11, flexShrink: 0 }}>{label}</span>
      <span style={{ color: "var(--ink)", fontSize: 11, textAlign: "right", wordBreak: "break-word" }}>{value}</span>
    </div>
  );
}

/** "credit_to_width_ratio" -> "credit to width ratio". No friendly-name
 * dictionary — this codebase doesn't have one, and inventing labels for
 * internal feature names risks misrepresenting what they actually measure. */
function formatFeatureName(feature: string): string {
  return feature.replace(/_/g, " ");
}

function FactorList({
  label,
  factors,
  tone,
}: {
  label: string;
  factors: { feature: string; value?: number; impact: number }[];
  tone: string;
}) {
  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ color: "var(--ink-faint)", fontSize: 11, marginBottom: 2 }}>{label}</div>
      {factors.map((f, i) => (
        <div
          key={`${f.feature}-${i}`}
          style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "2px 0" }}
        >
          <span style={{ color: "var(--ink)", fontSize: 11 }}>{formatFeatureName(f.feature)}</span>
          <span style={{ color: tone, fontSize: 11 }}>{f.impact >= 0 ? "+" : ""}{f.impact.toFixed(3)}</span>
        </div>
      ))}
    </div>
  );
}

export default function SignalAttribution({
  data,
  loading = false,
  size = "md",
}: {
  data: SignalAttributionData | null;
  loading?: boolean;
  size?: "sm" | "md";
}) {
  const now = Date.now();

  if (loading) {
    return (
      <span className="mono" style={{ color: "var(--ink-faint)", fontSize: size === "sm" ? 10 : 11 }}>
        Loading signal…
      </span>
    );
  }

  if (!data) {
    return (
      <span
        className="mono"
        style={{ color: "var(--ink-faint)", fontSize: size === "sm" ? 10 : 11, fontStyle: "italic" }}
      >
        No signal available
      </span>
    );
  }

  const tone = directionTone(data.direction);
  const stale = isStale(data, now);
  const age = relativeAge(data.updatedAt, now);

  const compactParts: string[] = [data.direction];
  if (data.timeframe) compactParts.push(data.timeframe);
  if (typeof data.confidence === "number") compactParts.push(`score ${confidenceScore(data.confidence)}`);
  if (data.source && !data.timeframe && typeof data.confidence !== "number") compactParts.push(data.source);

  const fontSize = size === "sm" ? 10.5 : 11.5;

  return (
    <details
      style={{ display: "inline-block", position: "relative" }}
      onClick={(e) => e.stopPropagation()}
    >
      <summary
        aria-label={`Signal ${data.direction}. Expand for source, timeframe, confidence, and authority details.`}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          cursor: "pointer",
          listStyle: "none",
          border: `1px solid ${tint(tone, 0.4)}`,
          borderRadius: 3,
          padding: "2px 8px",
          fontFamily: "var(--mono)",
          fontSize,
          fontWeight: 700,
          letterSpacing: "0.04em",
          color: tone,
          background: "var(--bg-3)",
          whiteSpace: "nowrap",
        }}
      >
        <span aria-hidden="true" style={{ display: "inline-block" }}>&#9656;</span>
        {compactParts.join(" · ")}
        {stale && (
          <span
            style={{
              color: "var(--amber)",
              border: "1px solid var(--amber)",
              borderRadius: 2,
              padding: "0 4px",
              fontSize: fontSize - 1.5,
            }}
          >
            STALE
          </span>
        )}
      </summary>

      <div
        role="group"
        aria-label={`Full attribution for ${data.direction} signal`}
        style={{
          position: "absolute",
          zIndex: 40,
          marginTop: 4,
          minWidth: 240,
          maxWidth: 320,
          background: "var(--bg-4)",
          border: "1px solid var(--line-dim)",
          borderRadius: 4,
          padding: 10,
          boxShadow: "0 8px 24px rgba(0,0,0,0.45)",
        }}
      >
        <DetailRow label="Direction" value={<span style={{ color: tone, fontWeight: 700 }}>{data.direction}</span>} />
        <DetailRow label="Source" value={data.source || "Source unavailable"} />
        <DetailRow label="Timeframe" value={data.timeframe || "Timeframe unknown"} />
        <DetailRow
          label="Confidence score"
          value={typeof data.confidence === "number" ? confidenceScoreText(data.confidence) : "Not provided"}
        />
        {typeof data.confidence === "number" && (
          <DetailRow label="Meaning" value={CONFIDENCE_NOTE} />
        )}
        <DetailRow label="Updated" value={data.updatedAt ? `${age ?? data.updatedAt}` : "Timestamp unavailable"} />
        <DetailRow label="Freshness" value={data.updatedAt ? (stale ? "Stale" : "Fresh") : "Unknown"} />
        <DetailRow label="Authority" value={authorityLabel(data.authority)} />
        {!!data.topPositiveFactors?.length && (
          <FactorList label="Supporting factors" factors={data.topPositiveFactors} tone="var(--green)" />
        )}
        {!!data.topNegativeFactors?.length && (
          <FactorList label="Deteriorating factors" factors={data.topNegativeFactors} tone="var(--red)" />
        )}
      </div>
    </details>
  );
}
