import React from "react";

export function Panel({
  title,
  action,
  children,
  padding = 14,
  bodyStyle,
  sectionStyle,
  className,
  interactive = false,
}: {
  title?: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
  padding?: number | string;
  bodyStyle?: React.CSSProperties;
  sectionStyle?: React.CSSProperties;
  className?: string;
  /** Opt-in hover-lift (border/shadow/translateY) for panels that are genuine
   * navigation affordances. Off by default — never apply to static/inert
   * data panels (e.g. StatTile grids), only to panels a user actually clicks. */
  interactive?: boolean;
}) {
  return (
    <section className={`panel${interactive ? " panel-interactive" : ""}${className ? ` ${className}` : ""}`} style={sectionStyle}>
      {(title != null || action != null) && (
        <div className="panel-head">
          {title != null && <div className="panel-title">{title}</div>}
          {action}
        </div>
      )}
      <div style={{ padding, ...bodyStyle }}>{children}</div>
    </section>
  );
}

export function EmptyState({ children }: { children: React.ReactNode }) {
  return <div className="empty-state">{children}</div>;
}

// ── StatPill ─────────────────────────────────────────────────────────────────
// Exact port of the identical local component previously duplicated in
// EquitySignals.tsx and OptionsSignals.tsx.
export function StatPill({ label, value }: { label: string; value: string }) {
  return (
    <div style={{
      background: "var(--bg-3)", borderRadius: 3, padding: "3px 8px",
      fontFamily: "var(--mono)", fontSize: 9, display: "flex", gap: 5, alignItems: "center",
    }}>
      <span style={{ color: "var(--ink-dim)", letterSpacing: "0.08em" }}>{label}</span>
      <span style={{ color: "var(--ink)", fontWeight: 600 }}>{value}</span>
    </div>
  );
}

// ── StatTile ─────────────────────────────────────────────────────────────────
// Unifies two previously-duplicated local patterns: Dashboard.tsx's StatCell
// (divider-row layout) and ChartWorkstation.tsx's inline stat-grid tile
// (boxed layout) — same kicker+data-val shape, two container styles.
export function StatTile({
  label,
  value,
  sub,
  tone,
  hint,
  variant = "boxed",
  size = "sm",
  spark,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
  tone?: string;
  hint?: React.ReactNode;
  variant?: "boxed" | "divider";
  size?: "default" | "sm";
  /** Optional short numeric history rendered as a trend sparkline below the
   * value. Omit for no sparkline (default) — real data only, never decorative. */
  spark?: number[];
}) {
  const wrap: React.CSSProperties = variant === "boxed"
    ? { padding: "10px 12px" }
    : { padding: "14px 16px", borderRight: "1px solid var(--line-dim)" };
  return (
    <div
      className={variant === "boxed" ? "instrument-card" : undefined}
      style={wrap}
    >
      <div className="kicker" style={{ marginBottom: variant === "boxed" ? 8 : 6 }}>{hint ?? label}</div>
      <div className={`data-val${size === "sm" ? " sm" : ""}`} style={{ color: tone || "var(--ink)" }}>{value}</div>
      {sub && <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)", marginTop: 3 }}>{sub}</div>}
      {spark && spark.length > 0 && (
        <div style={{ marginTop: 4 }}><Sparkline values={spark} height={16} barWidth={4} gap={2} /></div>
      )}
    </div>
  );
}

// ── Sparkline ────────────────────────────────────────────────────────────────
// Extracted from ModeAnalytics.tsx's local SparkBars — same visual output
// (green/red bar array, no smoothing). Apply only where real short numeric
// history already exists.
export function Sparkline({
  values,
  height = 20,
  barWidth = 6,
  gap = 2,
  formatTitle,
}: {
  values: number[];
  height?: number;
  barWidth?: number;
  gap?: number;
  /** Optional per-bar hover tooltip formatter (e.g. "+$120"). Omit for none. */
  formatTitle?: (v: number) => string;
}) {
  if (!values?.length) return null;
  const max = Math.max(...values.map(Math.abs), 1);
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap, height }}>
      {values.map((v, i) => (
        <div key={i} style={{
          width: barWidth, minHeight: 3, borderRadius: 1,
          background: v >= 0 ? "var(--green)" : "var(--red)",
          height: `${Math.max((Math.abs(v) / max) * 100, 15)}%`,
        }} title={formatTitle ? formatTitle(v) : undefined} />
      ))}
    </div>
  );
}

// ── Badge ────────────────────────────────────────────────────────────────────
// "mode"/"signal" wrap the existing .mode-badge/.signal-badge CSS classes.
// "tag" is a generic freeform tag for ad hoc inline-styled one-offs — supports
// both existing visual sub-styles found in the app: outline (border, no fill,
// e.g. Dashboard's BACKTEST/LIVE tags) and filled (background tint, e.g.
// Dashboard's UNTRACKED/OPEN position tags) — pass `bg` to get filled, omit
// it for outline.
type BadgeProps =
  | { kind: "mode"; tone: "conservative" | "balanced" | "aggressive" | "scalper"; children: React.ReactNode; style?: React.CSSProperties }
  | { kind: "signal"; tone: "bullish" | "bearish" | "neutral"; children: React.ReactNode; style?: React.CSSProperties }
  | { kind: "tag"; tone: string; bg?: string; children: React.ReactNode; style?: React.CSSProperties };

export function Badge(props: BadgeProps) {
  if (props.kind === "tag") {
    const filled = props.bg != null;
    return (
      <span style={{
        fontFamily: "var(--mono)", fontSize: 9, letterSpacing: "0.06em",
        color: props.tone,
        padding: filled ? "2px 6px" : "1px 6px",
        ...(filled
          ? { background: props.bg }
          : { border: `1px solid ${props.tone}`, opacity: 0.7 }),
        ...props.style,
      }}>
        {props.children}
      </span>
    );
  }
  return (
    <span className={`${props.kind === "mode" ? "mode-badge" : "signal-badge"} ${props.tone}`} style={props.style}>
      {props.children}
    </span>
  );
}

// ── Button ───────────────────────────────────────────────────────────────────
// Component API over the existing .btn-t CSS class (already the most-adopted
// class in the app) — same behavior, no visual change.
export function Button({
  active,
  danger,
  selector,
  size = "default",
  children,
  style,
  ...rest
}: {
  active?: boolean;
  danger?: boolean;
  /** A tab / filter / range picker choice: flat, with an underline when selected,
   *  like the main tab bar. Actions (Close, Run, Save) stay the raised key. */
  selector?: boolean;
  size?: "default" | "sm";
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={`btn-t${selector ? " btn-t--selector" : ""}${active ? " active" : ""}${danger ? " danger" : ""}`}
      style={size === "sm" ? { padding: "2px 8px", fontSize: 10, ...style } : style}
      {...rest}
    >
      {children}
    </button>
  );
}
