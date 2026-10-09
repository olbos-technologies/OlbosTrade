/**
 * Connect your own broker account.
 *
 * The read-only ops view next door (BrokerGateway) has told users for a while
 * that "connection is managed in Settings → Brokers", a screen that did not
 * exist. This is it.
 *
 * WHAT THIS SCREEN PROMISES, AND WHAT IT MUST NOT. Storing a key pair is not
 * the same as trading with it: per-user order routing is a separate change,
 * and until it lands the platform still executes through the operator's own
 * account. A user who connects a broker here and assumes their next order goes
 * to their account would have been misled by this page, so the banner is not
 * decoration — it is the honest part. `execution_routing_enabled` comes from
 * the server rather than being hardcoded here, so the day routing ships this
 * page stops claiming otherwise without a frontend deploy.
 *
 * THE SECRET IS WRITE-ONLY. It is sent once and never returned by any
 * endpoint, so there is no "show key" control to build and no decrypted value
 * in any response this page can render. What identifies a connection is
 * key_last4, four characters the server stores in the clear for exactly this
 * purpose. autoComplete="off" and type="password" keep the browser from
 * filing a brokerage secret in a password manager under this site's name.
 */
import React, { useCallback, useEffect, useState } from "react";
import { api, ApiError, type BrokerConnection } from "../../api/client";
import { Badge, Button } from "../../components/ui";

const MONO = "var(--mono)";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "block", marginBottom: 12 }}>
      <span style={{
        display: "block", fontFamily: MONO, fontSize: 10.5, color: "var(--ink-dim)",
        textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 5,
      }}>{label}</span>
      {children}
    </label>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%", boxSizing: "border-box", padding: "8px 10px",
  fontFamily: MONO, fontSize: 12, color: "var(--ink)",
  background: "var(--bg-1)", border: "1px solid var(--line-dim)", borderRadius: 2,
};

function when(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

function ConnectionRow({ conn, onChanged, onError }: {
  conn: BrokerConnection;
  onChanged: () => void;
  onError: (message: string) => void;
}) {
  const [busy, setBusy] = useState<"" | "verify" | "disconnect">("");
  const revoked = conn.status !== "active";

  const verify = async () => {
    setBusy("verify");
    try {
      const r = await api.verifyBrokerConnection(conn.id);
      if (!r.verified) onError(r.detail || "The broker rejected these credentials.");
      onChanged();
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not verify this connection.");
    } finally {
      setBusy("");
    }
  };

  const disconnect = async () => {
    // A confirm(), because this destroys the stored keys — reconnecting means
    // fetching them from the broker again, not clicking undo.
    if (!window.confirm(
      `Disconnect ${conn.broker.toUpperCase()} (${conn.environment})? The stored keys are deleted and you will need to paste them again to reconnect.`
    )) return;
    setBusy("disconnect");
    try {
      await api.disconnectBroker(conn.id);
      onChanged();
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not disconnect.");
    } finally {
      setBusy("");
    }
  };

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 12, padding: "12px 0",
      borderBottom: "1px solid var(--line-dim)", opacity: revoked ? 0.55 : 1,
      flexWrap: "wrap",
    }}>
      <span className={`dot ${!revoked && conn.verified ? "live" : "dead"}`} />
      <div style={{ flex: 1, minWidth: 190 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 700 }}>
            {conn.broker.toUpperCase()}
          </span>
          <Badge kind="tag" tone={conn.environment === "live" ? "var(--cyan)" : "var(--amber)"}
                 style={{ border: "1px solid var(--line-dim)", padding: "1px 6px",
                          textTransform: "uppercase", letterSpacing: "0.06em", opacity: 1 }}>
            {conn.environment}
          </Badge>
          {revoked && (
            <Badge kind="tag" tone="var(--ink-faint)"
                   style={{ border: "1px solid var(--line-dim)", padding: "1px 6px", opacity: 1 }}>
              disconnected
            </Badge>
          )}
          {!revoked && !conn.verified && (
            <Badge kind="tag" tone="var(--amber)"
                   style={{ border: "1px solid rgba(245,158,11,0.4)", padding: "1px 6px", opacity: 1 }}>
              unverified
            </Badge>
          )}
        </div>
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: "var(--ink-faint)", marginTop: 3 }}>
          {conn.label ? `${conn.label} · ` : ""}
          key ••••{conn.key_last4 || "????"} · last checked {when(conn.last_verified_at)}
        </div>
      </div>
      {!revoked && (
        <div style={{ display: "flex", gap: 8 }}>
          <Button onClick={verify} disabled={busy !== ""}>
            {busy === "verify" ? "checking…" : "Verify"}
          </Button>
          <Button onClick={disconnect} disabled={busy !== ""}>
            {busy === "disconnect" ? "…" : "Disconnect"}
          </Button>
        </div>
      )}
    </div>
  );
}

export default function MyBrokers() {
  const [connections, setConnections] = useState<BrokerConnection[]>([]);
  const [routingEnabled, setRoutingEnabled] = useState(false);
  const [loading, setLoading] = useState(true);
  /** Set when the whole feature is unavailable — no plan, or no encryption
   *  key on this deployment. Distinct from `error`, which is one failed
   *  action and should not blank the page. */
  const [blocked, setBlocked] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [environment, setEnvironment] = useState("paper");
  const [apiKey, setApiKey] = useState("");
  const [secretKey, setSecretKey] = useState("");
  const [label, setLabel] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await api.listBrokerConnections();
      setConnections(data.connections || []);
      setRoutingEnabled(Boolean(data.execution_routing_enabled));
      setBlocked("");
    } catch (e) {
      if (e instanceof ApiError && (e.status === 403 || e.status === 503 || e.status === 404)) {
        setBlocked(e.message);
      } else {
        setError(e instanceof ApiError ? e.message : "Could not load your broker connections.");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(""); setNotice("");
    if (!apiKey.trim() || !secretKey.trim()) {
      setError("Both the API key and the secret are required.");
      return;
    }
    setSaving(true);
    try {
      const r = await api.connectBroker({
        broker: "alpaca", environment,
        api_key: apiKey.trim(), secret_key: secretKey.trim(), label: label.trim(),
      });
      // Cleared on success only. A failed attempt keeps what was typed, so a
      // typo in one field does not cost the user both.
      setApiKey(""); setSecretKey(""); setLabel("");
      setNotice(r.unverified_reason
        ? `Saved, but not verified: ${r.unverified_reason}`
        : "Connected and verified with Alpaca.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not connect that account.");
    } finally {
      setSaving(false);
    }
  };

  if (blocked) {
    return (
      <div style={{ padding: 16 }}>
        <div className="panel-title" style={{ marginBottom: 10 }}>My Brokers</div>
        <div className="instrument-card" style={{ maxWidth: 560, padding: "14px 16px",
             fontFamily: MONO, fontSize: 12, color: "var(--ink-dim)", lineHeight: 1.7 }}>
          {blocked}
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: 16, height: "100%", overflowY: "auto" }}>
      <div className="panel-title" style={{ marginBottom: 10 }}>My Brokers</div>

      {!routingEnabled && (
        // The honest banner. See the file docstring — it is driven by the
        // server so it disappears on its own when routing ships.
        <div style={{
          maxWidth: 560, marginBottom: 16, padding: "10px 12px",
          borderLeft: "2px solid var(--amber)", background: "rgba(245,158,11,0.06)",
          fontFamily: MONO, fontSize: 11, color: "var(--ink-dim)", lineHeight: 1.65,
        }}>
          Your keys are stored encrypted, but orders are still placed through the
          platform account. Connecting here does not route your trades to your own
          broker yet.
        </div>
      )}

      <form onSubmit={submit} className="instrument-card"
            style={{ maxWidth: 560, padding: "14px 16px", marginBottom: 20 }}>
        <div style={{ fontFamily: MONO, fontSize: 11, color: "var(--ink-dim)",
                      lineHeight: 1.65, marginBottom: 12 }}>
          Connect an Alpaca account with an API key pair from the Alpaca
          dashboard. Interactive Brokers needs a dedicated gateway session per
          account and cannot be connected with keys yet.
        </div>

        <Field label="Environment">
          <select value={environment} onChange={e => setEnvironment(e.target.value)}
                  style={inputStyle}>
            <option value="paper">Paper — practice account</option>
            <option value="live">Live — real money</option>
          </select>
        </Field>

        <Field label="API key ID">
          <input value={apiKey} onChange={e => setApiKey(e.target.value)}
                 autoComplete="off" spellCheck={false} style={inputStyle}
                 placeholder="PK..." />
        </Field>

        <Field label="Secret key">
          {/* type=password with autoComplete off: a brokerage secret must not
              be offered back by the browser on an unrelated form. */}
          <input value={secretKey} onChange={e => setSecretKey(e.target.value)}
                 type="password" autoComplete="new-password" spellCheck={false}
                 style={inputStyle} placeholder="never shown again once saved" />
        </Field>

        <Field label="Label (optional)">
          <input value={label} onChange={e => setLabel(e.target.value)}
                 maxLength={60} style={inputStyle} placeholder="e.g. main account" />
        </Field>

        <Button type="submit" disabled={saving}>
          {saving ? "connecting…" : "Connect"}
        </Button>

        {error && (
          <div role="alert" style={{ marginTop: 10, fontFamily: MONO, fontSize: 11,
                                     color: "var(--red)", lineHeight: 1.6 }}>
            {error}
          </div>
        )}
        {notice && (
          <div role="status" style={{ marginTop: 10, fontFamily: MONO, fontSize: 11,
                                      color: "var(--green)", lineHeight: 1.6 }}>
            {notice}
          </div>
        )}
      </form>

      <div className="panel-title" style={{ marginBottom: 8 }}>Connected accounts</div>
      <div className="instrument-card" style={{ maxWidth: 560, padding: "4px 16px" }}>
        {loading && (
          <div style={{ padding: "12px 0", fontFamily: MONO, fontSize: 11,
                        color: "var(--ink-faint)" }}>loading…</div>
        )}
        {!loading && connections.length === 0 && (
          <div style={{ padding: "12px 0", fontFamily: MONO, fontSize: 11,
                        color: "var(--ink-faint)" }}>
            No broker accounts connected yet.
          </div>
        )}
        {connections.map(conn => (
          <ConnectionRow key={conn.id} conn={conn}
                         onChanged={() => { void load(); }}
                         onError={setError} />
        ))}
      </div>
    </div>
  );
}
