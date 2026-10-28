import React, { useCallback, useEffect, useState } from "react";
import { api, type BrokerOAuthConnection, type BrokerProvider } from "../../api/client";
import { Button } from "../../components/ui";

export default function BrokerSignIn() {
  const [providers, setProviders] = useState<BrokerProvider[]>([]);
  const [connections, setConnections] = useState<BrokerOAuthConnection[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const result = new URLSearchParams(window.location.search).get("broker_oauth");
  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [catalog, saved] = await Promise.all([
        api.brokerOAuthProviders(), api.brokerOAuthConnections(),
      ]);
      setProviders(catalog.providers); setConnections(saved.connections);
    } catch {
      setProviders([]); setConnections([]);
      setError("Broker sign-in information is unavailable. Retry to check your connections.");
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const connect = async (provider: string) => {
    setBusy(provider); setError("");
    try {
      const data = await api.startBrokerOAuth(provider);
      const url = new URL(data.authorization_url);
      // No arbitrary redirects, even if a malformed API response reaches us.
      if (url.origin !== "https://app.alpaca.markets" || url.pathname !== "/oauth/authorize"
          || url.username || url.password || url.searchParams.get("env") !== "paper"
          || url.searchParams.has("scope")) throw new Error();
      window.location.assign(url.href);
    } catch { setError("Could not start broker sign-in. Try again shortly."); setBusy(""); }
  };
  const disconnect = async (id: string) => {
    if (!window.confirm("Disconnect this account from OlbosTrade? You can reconnect through Alpaca.")) return;
    setBusy(id); setError("");
    try { await api.disconnectBrokerOAuth(id); await load(); }
    catch { setError("Could not disconnect. Your connection may still be active; retry."); }
    finally { setBusy(""); }
  };

  return <section aria-label="Broker sign-in" className="instrument-card"
    style={{ maxWidth: 680, padding: 16, marginBottom: 20, lineHeight: 1.6 }}>
    <h2 style={{ fontSize: 18, margin: "0 0 8px" }}>Choose your broker</h2>
    <p style={{ color: "var(--ink-dim)" }}>
      Use your existing brokerage login on the broker’s website. Your brokerage
      password stays with your broker. You can continue using OlbosTrade without connecting.
    </p>
    <p style={{ color: "var(--amber)" }}>Paper accounts · Read-only access · Automated execution unavailable</p>
    {result === "connected" && <p role="status">Broker sign-in completed. Check the connected accounts below. Trading remains off.</p>}
    {result === "failed" && <p role="alert">Broker sign-in was cancelled, expired, or could not be verified. Start again.</p>}
    {loading && <p role="status">Checking broker availability…</p>}
    {error && <div role="alert"><p>{error}</p><Button style={{ minHeight: 44 }} onClick={() => void load()} disabled={loading}>Retry broker sign-in</Button></div>}
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 240px), 1fr))", gap: 12 }}>
      {providers.map(p => <article key={p.id} style={{ border: "1px solid var(--line-dim)", borderRadius: 8, padding: 14 }}>
        <h3 style={{ margin: "0 0 8px", fontSize: 15 }}>{p.name}</h3>
        <p style={{ color: "var(--ink-dim)", fontSize: 13 }}>{p.note}</p>
        <Button style={{ minHeight: 44 }} disabled={!p.available || busy !== ""} onClick={() => void connect(p.id)}>
          {busy === p.id ? "Opening broker…" : p.available ? `Sign in with ${p.name}` : p.id === "alpaca" ? "Setup pending" : "Coming soon"}
        </Button>
      </article>)}
    </div>
    <h3 style={{ fontSize: 15 }}>Accounts connected through broker sign-in</h3>
    {!loading && !error && connections.length === 0 && <p>No accounts connected through broker sign-in.</p>}
    {connections.map(c => <div key={c.id} style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", padding: "12px 0", borderTop: "1px solid var(--line-dim)" }}>
      <div style={{ flex: 1, minWidth: 160 }}>
        <strong>Alpaca · Paper · ••••{c.account_last4}</strong><br />
        <span>Read-only · Trading off</span><br />
        <small>Connected {new Date(c.connected_at).toLocaleString()}. Current account data has not been synced.</small>
      </div>
      <Button style={{ minHeight: 44 }} disabled={busy !== ""} onClick={() => void disconnect(c.id)}>Disconnect broker sign-in</Button>
    </div>)}
    <p style={{ fontSize: 12, color: "var(--ink-dim)" }}>Disconnecting deletes the saved token from OlbosTrade. You can also revoke this application in Alpaca’s settings.</p>
  </section>;
}
