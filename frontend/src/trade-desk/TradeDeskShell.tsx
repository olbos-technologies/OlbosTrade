/**
 * Shared Trade Desk shell — header + tabs + content region.
 * Phase F: landmarks, skip link, mobile bottom tab strip when mobile_trade_desk is on.
 */

import React, { useEffect, useState } from "react";
import TradeDeskHeader from "./TradeDeskHeader";
import TradeDeskTabs, { type TradeDeskTab } from "./TradeDeskTabs";
import { isFlagEnabled } from "./featureFlags";

export default function TradeDeskShell({
  tab,
  onTabChange,
  badges,
  children,
}: {
  tab: TradeDeskTab;
  onTabChange: (tab: TradeDeskTab) => void;
  badges?: Partial<Record<TradeDeskTab, number | string>>;
  children: React.ReactNode;
}) {
  const [narrow, setNarrow] = useState(false);
  const mobileDesk = isFlagEnabled("mobile_trade_desk");

  useEffect(() => {
    if (!mobileDesk) return;
    const mq = window.matchMedia("(max-width: 760px)");
    const apply = () => setNarrow(mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [mobileDesk]);

  const useMobileChrome = mobileDesk && narrow;

  return (
    <div
      className={`trade-desk-shell${useMobileChrome ? " trade-desk-shell--mobile" : ""}`}
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
        background: "var(--bg)",
      }}
    >
      <a href="#trade-desk-main" className="trade-desk-skip">
        Skip to desk content
      </a>
      <header aria-label="Trade Desk status">
        <TradeDeskHeader />
      </header>
      {!useMobileChrome && (
        <TradeDeskTabs active={tab} onChange={onTabChange} badges={badges} />
      )}
      {useMobileChrome && (
        <nav aria-label="Trade Desk mobile workspaces" className="trade-desk-mobile-tabs">
          <TradeDeskTabs
            active={tab}
            onChange={onTabChange}
            badges={badges}
            ensureActiveVisible
          />
        </nav>
      )}
      <main
        id="trade-desk-main"
        tabIndex={-1}
        aria-label="Trade Desk workspace"
        style={{ flex: 1, minHeight: 0, overflow: "auto" }}
      >
        {children}
      </main>
    </div>
  );
}
