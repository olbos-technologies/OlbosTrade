import React from "react";

/** A titled panel beside the chart: a heading with a count, and a scrolling body. */
export default function SidePanel({
  title, count, label, children,
}: { title: string; count?: number | string; label: string; children: React.ReactNode }) {
  return (
    <section className="side-panel" aria-label={label}>
      <h2 className="side-panel__head">{title}{count !== undefined && <span className="side-panel__count">{count}</span>}</h2>
      <div className="side-panel__body">{children}</div>
    </section>
  );
}
