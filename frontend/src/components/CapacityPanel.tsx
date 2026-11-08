export type Capacity = {
  global_cap: number;
  style: { name: string; cap: number };
  base_cap: number;
  limited_by: "global" | "style";
  regime: { name: string; known: boolean; multiplier: number; tightened: boolean };
  effective_cap: number;
  open_positions: number | null;
  room: number | null;
  steps: string[];
  recent_blocks: { reason: string; count: number }[];
  blocks_available: boolean;
  blocks_window_hours: number;
};

export const readCapacity = async (): Promise<Capacity> => {
  const r = await fetch("/api/trade-desk/capacity", { credentials: "same-origin" });
  if (!r.ok) throw new Error("Capacity unavailable");
  const d = await r.json();
  if (!d || typeof d.effective_cap !== "number" || typeof d.global_cap !== "number") throw new Error("Capacity unreadable");
  return d as Capacity;
};

type Props = { data: Capacity | null; loading: boolean };

/**
 * Why the desk can hold N positions: the cap and each step that set it, how many
 * are open, and what blocked entries in the last day. Unreadable numbers show as
 * unavailable, never as zero.
 */
export default function CapacityPanel({ data, loading }: Props) {
  if (!data) {
    return <p className="capacity-warn" data-testid="capacity-headline">{loading ? "Reading capacity…" : "Capacity unavailable. The limit and open count could not be confirmed."}</p>;
  }
  const open = data.open_positions;
  const headline = open === null
    ? `Up to ${data.effective_cap} positions · open count unavailable`
    : data.room === 0
      ? `At capacity: ${open} of ${data.effective_cap} open`
      : `${open} of ${data.effective_cap} open · room for ${data.room} more`;
  return (
    <div className="capacity-panel">
      <p className="capacity-headline" data-testid="capacity-headline">{headline}</p>
      <ol className="capacity-steps" aria-label="How the limit is set">
        {data.steps.map(s => <li key={s}>{s}</li>)}
        <li><strong>Limit today: {data.effective_cap}</strong></li>
      </ol>
      {!data.regime.known && (
        <p className="capacity-warn">Market regime is unclassified, so the cautious setting applies.</p>
      )}
      <div className="capacity-blocks">
        <span className="capacity-label">Blocked in the last {data.blocks_window_hours}h</span>
        {!data.blocks_available ? <p className="capacity-warn">Blocked-entry history unavailable.</p>
          : data.recent_blocks.length === 0 ? <p className="capacity-muted">Nothing blocked.</p>
          : <ul>{data.recent_blocks.map(b => <li key={b.reason}>{b.reason} <strong>×{b.count}</strong></li>)}</ul>}
      </div>
    </div>
  );
}
