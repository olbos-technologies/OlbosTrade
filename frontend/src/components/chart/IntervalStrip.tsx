import { Button } from "../ui";
import { INTERVAL_OPTIONS, type IntervalKey } from "./timeframes";

/** Bar interval picker, at the top-left of the chart like TradingView's toolbar. */
export default function IntervalStrip({ value, onChange }: { value: IntervalKey; onChange: (k: IntervalKey) => void }) {
  return (
    <div className="interval-strip" role="group" aria-label="Chart timeframe">
      {INTERVAL_OPTIONS.map((o) => (
        <Button selector key={o.key} active={value === o.key} title={o.title} aria-label={o.title}
          aria-pressed={value === o.key} onClick={() => onChange(o.key)}>
          {o.label}
        </Button>
      ))}
    </div>
  );
}
