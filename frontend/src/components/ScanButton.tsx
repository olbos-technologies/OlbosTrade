import React, { useState } from "react";
import { useIsMobile } from "../hooks/useIsMobile";

/** Keeps a potentially long-running scanner invocation intentional on phones. */
export default function ScanButton({ scanning, onScan }: { scanning: boolean; onScan: () => void }) {
  const isMobile = useIsMobile();
  const [confirming, setConfirming] = useState(false);
  const begin = () => isMobile ? setConfirming(true) : onScan();
  const confirm = () => { setConfirming(false); onScan(); };

  return <>
    <button type="button" onClick={begin} disabled={scanning} className="btn-primary">
      {scanning ? "SCANNING…" : "RUN SCAN"}
    </button>
    {confirming && (
      <div className="execution-confirm-overlay" role="dialog" aria-modal="true" aria-labelledby="scan-confirm-title">
        <div className="execution-confirm-card">
          <div className="kicker">Scanner check</div>
          <h2 id="scan-confirm-title">Run a fresh scan?</h2>
          <p>This refreshes market analysis across the watchlist. It does not submit orders or change execution mode.</p>
          <div className="execution-confirm-card__actions">
            <button type="button" className="btn-ghost" onClick={() => setConfirming(false)}>Cancel</button>
            <button type="button" className="btn-primary" onClick={confirm}>Run scan</button>
          </div>
        </div>
      </div>
    )}
  </>;
}
