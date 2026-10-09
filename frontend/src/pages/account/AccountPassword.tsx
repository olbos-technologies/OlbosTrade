/**
 * Change your password.
 *
 * The route has existed since #70 with no UI, which meant the only way to
 * change a password was a shell on the server — so in practice nobody could.
 *
 * WHY IT ASKS FOR THE CURRENT PASSWORD even though you are already signed in:
 * a session cookie proves this browser logged in at some point, not that the
 * person typing now is the owner. Without it, a borrowed laptop turns
 * temporary access into permanent access. The backend enforces this; the form
 * just collects it.
 *
 * SUCCESS REVOKES YOUR OTHER SESSIONS, and the response says how many. That is
 * the point of changing a password, not a side effect — people change it
 * precisely when they think someone else is in. The count is surfaced rather
 * than swallowed so you can tell whether anything else was actually signed in.
 */
import React, { useState } from "react";
import { api, ApiError, MAX_PASSWORD_LEN, MIN_PASSWORD_LEN } from "../../api/client";
import { Button } from "../../components/ui";

const MONO = "var(--mono)";

const inputStyle: React.CSSProperties = {
  width: "100%", boxSizing: "border-box", padding: "8px 10px",
  fontFamily: MONO, fontSize: 12, color: "var(--ink)",
  background: "var(--bg-1)", border: "1px solid var(--line-dim)", borderRadius: 2,
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "block", marginBottom: 12 }}>
      <span style={{ display: "block", fontFamily: MONO, fontSize: 10.5,
                     color: "var(--ink-dim)", textTransform: "uppercase",
                     letterSpacing: "0.08em", marginBottom: 5 }}>{label}</span>
      {children}
    </label>
  );
}

export default function AccountPassword() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(""); setNotice("");

    // Checked here as well as on the server: the mismatch case has no server
    // round trip to make, and a typo should not cost a request that counts
    // against the login rate limiter this route shares.
    if (next !== confirm) {
      setError("The new passwords do not match.");
      return;
    }
    // CODE POINTS, not UTF-16 units. JavaScript's .length counts surrogate
    // pairs twice while the backend's Python len() counts characters, so an
    // emoji or any astral character makes the two disagree: a six-emoji
    // password measures 12 here and 6 there, and this check would wave through
    // something the server then rejects with a message the user cannot act on.
    const nextLength = [...next].length;
    if (nextLength < MIN_PASSWORD_LEN) {
      setError(`Use at least ${MIN_PASSWORD_LEN} characters.`);
      return;
    }
    if (nextLength > MAX_PASSWORD_LEN) {
      setError(`Use at most ${MAX_PASSWORD_LEN} characters.`);
      return;
    }
    if (next === current) {
      setError("The new password must be different from the current one.");
      return;
    }

    setBusy(true);
    try {
      const r = await api.changePassword({ current_password: current, new_password: next });
      // Cleared on success only — a wrong current password should not cost
      // you the new one you just typed twice.
      setCurrent(""); setNext(""); setConfirm("");
      const n = r.other_sessions_revoked || 0;
      setNotice(
        n > 0
          ? `Password changed. ${n} other ${n === 1 ? "session was" : "sessions were"} signed out.`
          : "Password changed. No other sessions were signed in."
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change your password.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ padding: 16, height: "100%", overflowY: "auto" }}>
      <div className="panel-title" style={{ marginBottom: 10 }}>Password</div>

      <form onSubmit={submit} className="instrument-card"
            style={{ maxWidth: 520, padding: "14px 16px" }}>
        <div style={{ fontFamily: MONO, fontSize: 11, color: "var(--ink-dim)",
                      lineHeight: 1.65, marginBottom: 12 }}>
          Changing your password signs out every other browser you are logged
          in on. This one stays signed in.
        </div>

        <Field label="Current password">
          <input value={current} onChange={e => setCurrent(e.target.value)}
                 type="password" autoComplete="current-password" style={inputStyle} />
        </Field>

        <Field label={`New password (at least ${MIN_PASSWORD_LEN} characters)`}>
          <input value={next} onChange={e => setNext(e.target.value)}
                 type="password" autoComplete="new-password" style={inputStyle} />
        </Field>

        <Field label="Confirm new password">
          <input value={confirm} onChange={e => setConfirm(e.target.value)}
                 type="password" autoComplete="new-password" style={inputStyle} />
        </Field>

        <Button type="submit" disabled={busy}>
          {busy ? "changing…" : "Change password"}
        </Button>

        {error && (
          <div role="alert" style={{ marginTop: 10, fontFamily: MONO, fontSize: 11,
                                     color: "var(--red)", lineHeight: 1.6 }}>{error}</div>
        )}
        {notice && (
          <div role="status" style={{ marginTop: 10, fontFamily: MONO, fontSize: 11,
                                      color: "var(--green)", lineHeight: 1.6 }}>{notice}</div>
        )}
      </form>
    </div>
  );
}
