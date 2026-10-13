/**
 * Trade Desk 2.0 page entry — shell + tab workspace.
 * Phases B–F: desks, Copilot/Orders/Execution, Trade Replay.
 * Positions reuse legacy TradeDesk. Money path: Step 8 portfolio gate only.
 */

import React, { useEffect, useState } from "react";
import TradeDesk from "./TradeDesk";
import TradeDeskShell from "../trade-desk/TradeDeskShell";
import CommandOverview from "../trade-desk/CommandOverview";
import DeskSettings from "../trade-desk/DeskSettings";
import EquityDesk from "../trade-desk/equity/EquityDesk";
import OptionsDesk from "../trade-desk/options/OptionsDesk";
import CopilotQueue from "../trade-desk/copilot/CopilotQueue";
import OrdersWorkspace from "../trade-desk/orders/OrdersWorkspace";
import ExecutionMonitor from "../trade-desk/execution/ExecutionMonitor";
import TradeReplay from "../trade-desk/replay/TradeReplay";
import {
  tabFromNavKey,
  navKeyFromTab,
  type TradeDeskTab,
} from "../trade-desk/TradeDeskTabs";
import { api } from "../api/client";
import { useTerminalNav } from "../components/TerminalNavContext";

export default function TradeDeskV2({ initialTab }: { initialTab?: string }) {
  const onNav = useTerminalNav();
  const [tab, setTab] = useState<TradeDeskTab>(() =>
    tabFromNavKey(initialTab || "trade:overview"),
  );
  const [pendingCount, setPendingCount] = useState(0);

  useEffect(() => {
    if (initialTab) setTab(tabFromNavKey(initialTab));
  }, [initialTab]);

  useEffect(() => {
    let alive = true;
    const load = () => {
      api.getPendingApprovals()
        .then((d: any) => {
          if (alive) setPendingCount(d.count ?? (d.pending || []).length);
        })
        .catch(() => {});
    };
    load();
    const t = setInterval(load, 20000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const changeTab = (next: TradeDeskTab) => {
    setTab(next);
    onNav(navKeyFromTab(next));
  };

  let body: React.ReactNode;
  switch (tab) {
    case "overview":
      body = <CommandOverview onNavigateTab={changeTab} />;
      break;
    case "equities":
      body = <EquityDesk />;
      break;
    case "options":
      body = <OptionsDesk />;
      break;
    case "copilot":
      body = <CopilotQueue />;
      break;
    case "positions":
      body = <TradeDesk initialTab="positions" routeTabsToUrl={false} embedded />;
      break;
    case "orders":
      body = <OrdersWorkspace />;
      break;
    case "execution":
      body = <ExecutionMonitor />;
      break;
    case "replay":
      body = <TradeReplay />;
      break;
    case "settings":
      body = <DeskSettings />;
      break;
    default:
      body = <CommandOverview onNavigateTab={changeTab} />;
  }

  return (
    <TradeDeskShell
      tab={tab}
      onTabChange={changeTab}
      badges={{ copilot: pendingCount > 0 ? pendingCount : undefined }}
    >
      {body}
    </TradeDeskShell>
  );
}
