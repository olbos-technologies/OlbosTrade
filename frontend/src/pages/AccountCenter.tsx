/**
 * AccountCenter — everything that belongs to the signed-in person.
 *
 * WHY THIS EXISTS. Account management had no home. /api/auth/password,
 * /api/auth/sessions and its revoke route all shipped in #70 with no UI at
 * all, so the only way to change a password was a shell on the server. The
 * only account surface in the whole app was the status-bar menu: email, tier,
 * Sign out.
 *
 * My Brokers moved here from Data & Integrations, where #78 put it. That group
 * is platform operations — the IBKR gateway the operator runs, market data
 * feeds, data quality — and a screen holding YOUR Alpaca keys is not that. It
 * also sat directly beneath an entry called "Broker Connections", which is the
 * platform's own broker status, so the two most confusable things in the app
 * were adjacent and nearly identically named.
 *
 * The split now reads: Data & Integrations is what the platform connects to,
 * Account is what you own.
 */
import React from "react";
import TabBar from "../components/TabBar";
import AccountProfile from "./account/AccountProfile";
import AccountPassword from "./account/AccountPassword";
import AccountSessions from "./account/AccountSessions";
import MyBrokers from "./account/MyBrokers";
import { useTabRoute } from "../hooks/useTabRoute";

export const TABS = [
  { key: "profile",  label: "Profile" },
  { key: "password", label: "Password" },
  { key: "sessions", label: "Sessions" },
  { key: "brokers",  label: "My Brokers" },
];

/** Tab -> the page key that renders it, so a tab change shows up in the URL. */
/** Exported so the route table can be checked against the page registry — see src/hooks/__tests__/tabRouteTable.test.tsx. */
/** The tab the page opens on when the URL names it without one. */
export const DEFAULT_TAB = "profile";

export const TAB_PAGE_KEYS = {
  profile:  "account:profile",
  password: "account:password",
  sessions: "account:sessions",
  brokers:  "account:brokers",
} as const;

export default function AccountCenter({ initialTab = DEFAULT_TAB }: { initialTab?: string }) {
  const [tab, setTab] = useTabRoute(initialTab, TAB_PAGE_KEYS);
  return (
    <div>
      <TabBar tabs={TABS} active={tab} onChange={setTab} label="Account views" />
      <div id={`workspace-panel-${tab}`} role="tabpanel">
        {tab === "profile"  && <AccountProfile />}
        {tab === "password" && <AccountPassword />}
        {tab === "sessions" && <AccountSessions />}
        {tab === "brokers"  && <MyBrokers />}
      </div>
    </div>
  );
}
