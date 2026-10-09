import React from "react";
import TabBar from "../components/TabBar";
// My Brokers used to live here. It moved to AccountCenter: this group is
// platform operations, and a screen holding a user's own Alpaca keys sat
// directly beneath "Broker Connections" — the platform's own broker — which
// made the two most confusable things in the app adjacent and near-identically
// named.
import BrokerGateway from "./data/BrokerGateway";
import MarketData from "./data/MarketData";
import DataQuality from "./data/DataQuality";
import { useTabRoute } from "../hooks/useTabRoute";

export const TABS = [
  { key: "broker", label: "Broker" },
  { key: "market", label: "Market Data" },
  { key: "quality", label: "Data Quality" },
];

/** Tab -> the page key that renders it, so a tab change shows up in the URL. */
/** Exported so the route table can be checked against the page registry — see src/hooks/__tests__/tabRouteTable.test.tsx. */
/** The tab the page opens on when the URL names it without one. */
export const DEFAULT_TAB = "broker";

export const TAB_PAGE_KEYS = {
  broker: "system:broker",
  market: "system:market",
  quality: "system:quality",
} as const;

export default function SystemCenter({ initialTab = DEFAULT_TAB }: { initialTab?: string }) {
  const [tab, setTab] = useTabRoute(initialTab, TAB_PAGE_KEYS);
  return <div>
    <TabBar tabs={TABS} active={tab} onChange={setTab} label="System and integration views" />
    <div id={`workspace-panel-${tab}`} role="tabpanel">
      {tab === "broker" && <BrokerGateway />}
      {tab === "market" && <MarketData />}
      {tab === "quality" && <DataQuality />}
    </div>
  </div>;
}
