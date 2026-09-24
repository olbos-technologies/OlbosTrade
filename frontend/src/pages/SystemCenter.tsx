import React, { useState } from "react";
import TabBar from "../components/TabBar";
import BrokerGateway from "./data/BrokerGateway";
import MarketData from "./data/MarketData";
import DataQuality from "./data/DataQuality";
import OperationsSnapshot from "../components/OperationsSnapshot";

const TABS = [
  { key: "operations", label: "Operations" },
  { key: "broker", label: "Broker" },
  { key: "market", label: "Market Data" },
  { key: "quality", label: "Data Quality" },
];

export default function SystemCenter({ initialTab = "broker" }: { initialTab?: string }) {
  const [tab, setTab] = useState(initialTab);
  return <div>
    <TabBar tabs={TABS} active={tab} onChange={setTab} label="System and integration views" />
    <div id={`workspace-panel-${tab}`} role="tabpanel">
      {tab === "operations" && <OperationsSnapshot />}
      {tab === "broker" && <BrokerGateway />}
      {tab === "market" && <MarketData />}
      {tab === "quality" && <DataQuality />}
    </div>
  </div>;
}
