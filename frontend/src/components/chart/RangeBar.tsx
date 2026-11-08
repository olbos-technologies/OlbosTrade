import { Button } from "../ui";
import { RANGE_OPTIONS, type RangeKey } from "./timeframes";

/** How much history to show, at the bottom-right of the chart like TradingView's
 *  range row. `note` says so when the data provider could not cover the range. */
export default function RangeBar({
  value, onChange, note,
}: { value: RangeKey | null; onChange: (k: RangeKey) => void; note?: string | null }) {
  return (
    <div className="range-bar">
      {note && <span className="range-bar__note" role="status">{note}</span>}
      <div className="range-bar__buttons" role="group" aria-label="Chart range">
        {RANGE_OPTIONS.map((o) => (
          <Button selector key={o.key} active={value === o.key} title={o.title} aria-label={o.title}
            aria-pressed={value === o.key} onClick={() => onChange(o.key)}>
            {o.label}
          </Button>
        ))}
      </div>
    </div>
  );
}
