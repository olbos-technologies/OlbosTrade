import { useId } from "react";

/** Terminal lockup matching the approved silver O / single-line wordmark. */
export default function TerminalWordmark({ height, className = "", markOnly = false }: {
  height: number; className?: string; markOnly?: boolean;
}) {
  const gradient = useId();
  return <span className={`terminal-wordmark ${className}`} role="img" aria-label="Olbos Trade"
    style={{ height, display: "inline-flex", alignItems: "center", flexShrink: 0, gap: height * .22 }}>
    <svg width={height} height={height} viewBox="0 0 48 48" aria-hidden="true">
      <defs><linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
        <stop stopColor="#fbfdff"/><stop offset=".4" stopColor="#b9c8da"/>
        <stop offset=".7" stopColor="#6f829a"/><stop offset="1" stopColor="#e5edf7"/>
      </linearGradient></defs>
      <ellipse cx="24" cy="24" rx="16" ry="20" transform="rotate(27 24 24)"
        fill="none" stroke={`url(#${gradient})`} strokeWidth="7"/>
    </svg>
    {!markOnly && <span style={{ fontFamily: "var(--sans)", fontSize: height * .46, letterSpacing: ".04em", whiteSpace: "nowrap" }}>
      <strong style={{ color: "#eef3fa", fontWeight: 750 }}>OLBOS</strong>{" "}
      <span style={{ color: "#adbed4", fontWeight: 400 }}>TRADE</span>
    </span>}
  </span>;
}
