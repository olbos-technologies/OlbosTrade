import React from "react";
import TabBar from "../components/TabBar";
import BrokerGateway from "./data/BrokerGateway";
import MyBrokers from "./data/MyBrokers";
import MarketData from "./data/MarketData";
import DataQuality from "./data/DataQuality";
import { useTabRoute } from "../hooks/useTabRoute";

export const TABS = [
  { key: "broker", label: "Broker" },
  { key: "connections", label: "My Brokers" },
  { key: "market", label: "Market Data" },
  { key: "quality", label: "Data Quality" },
];

/** Tab -> the page key that renders it, so a tab change shows up in the URL. */
/** Exported so the route table can be checked against the page registry — see src/hooks/__tests__/tabRouteTable.test.tsx. */
/** The tab the page opens on when the URL names it without one. */
export const DEFAULT_TAB = "broker";

export const TAB_PAGE_KEYS = {
  broker: "system:broker",
  connections: "system:connections",
  market: "system:market",
  quality: "system:quality",
} as const;

export default function SystemCenter({ initialTab = DEFAULT_TAB }: { initialTab?: string }) {
  const [tab, setTab] = useTabRoute(initialTab, TAB_PAGE_KEYS);
  return <div>
    <TabBar tabs={TABS} active={tab} onChange={setTab} label="System and integration views" />
    <div id={`workspace-panel-${tab}`} role="tabpanel">
      {tab === "broker" && <BrokerGateway />}
      {tab === "connections" && <MyBrokers />}
      {tab === "market" && <MarketData />}
      {tab === "quality" && <DataQuality />}
    </div>
  </div>;
}
