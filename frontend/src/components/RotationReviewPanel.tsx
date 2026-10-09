import React, { useState } from "react";
import { api } from "../api/client";
import HoldToConfirmButton from "./HoldToConfirmButton";
import { Button } from "./ui";

/**
 * Capital Rotation reviews awaiting approval.
 *
 * Approving one closes a real position and opens another, so this panel is
 * built to be *read* before it is clicked, not to make approval convenient:
 *
 * - Every heuristic is rendered with an UNCALIBRATED tag. Alpha Edge, quality
 *   score and confidence are indicator composites, not probabilities, and the
 *   panel says so next to the number rather than in a footnote.
 * - Expected R is shown as "not computed", with the reason, instead of being
 *   quietly omitted — an absent field reads as an oversight; a stated absence
 *   reads as a decision.
 * - The incumbent's unrealized P&L is displayed under a "context only" label
 *   and visually separated from the comparison, because it is excluded from
 *   the decision. Showing it at all is a deliberate call: an operator will
 *   want to know, and hiding it would look like concealment.
 * - Approve is hold-to-confirm; reject is a plain click. The asymmetry is the
 *   point — one sends two orders, the other sends none.
 */

interface Facts {
  ticker: string;
  direction?: string | null;
  alpha_edge?: number | null;
  quality_score?: number | null;
  confidence?: number | null;
  composite?: number | null;
  p_target_before_stop?: number | null;
  in_flagged_cluster?: boolean | null;
  liquidity_ok?: boolean | null;
  unrealized_pnl_context_only?: number | null;
}

interface Review {
  recommendation: "replace" | "hold" | "insufficient_data";
  reasons: string[];
  incumbent: Facts;
  challenger: Facts;
  composite_margin?: number | null;
  materiality_margin?: number;
  hard_constraint_failures?: string[];
  data_quality?: Record<string, string>;
  sunk_cost_excluded?: boolean;
}

export interface RotationReviewEntry {
  review_id: string;
  ticker: string;
  asset_type?: string;
  queued_at?: string;
  incumbent_trade_id?: string | null;
  review: Review;
}

const num = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined ? "—" : v.toFixed(digits);

const money = (v: number | null | undefined) =>
  v === null || v === undefined
    ? "—"
    : `${v < 0 ? "-" : ""}$${Math.abs(v).toLocaleString(undefined, {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
      })}`;

function Uncal() {
  return (
    <span
      title="Heuristic indicator composite — not a calibrated probability"
      style={{
        fontSize: 8, letterSpacing: 0.5, marginLeft: 5, padding: "1px 3px",
        border: "1px solid var(--amber)", color: "var(--amber)",
        borderRadius: 2, verticalAlign: "middle",
      }}
    >
      UNCAL
    </span>
  );
}

function Row({ label, incumbent, challenger, uncal = false }: {
  label: string; incumbent: React.ReactNode; challenger: React.ReactNode; uncal?: boolean;
}) {
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "1.4fr 1fr 1fr", gap: 8,
      padding: "5px 0", borderBottom: "1px solid var(--line-dim)",
      fontFamily: "var(--mono)", fontSize: 11,
    }}>
      <span style={{ color: "var(--text-dim)" }}>
        {label}{uncal && <Uncal />}
      </span>
      <span style={{ textAlign: "right" }}>{incumbent}</span>
      <span style={{ textAlign: "right" }}>{challenger}</span>
    </div>
  );
}

export default function RotationReviewPanel({
  reviews, onResolved,
}: {
  reviews: RotationReviewEntry[];
  onResolved?: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  if (!reviews.length) {
    return (
      <div style={{ padding: "14px 16px", fontSize: 11, color: "var(--text-dim)" }}>
        No rotation reviews awaiting approval.
      </div>
    );
  }

  const act = async (id: string, kind: "approve" | "reject") => {
    setBusy(id);
    setErrors((e) => ({ ...e, [id]: "" }));
    try {
      if (kind === "approve") await api.approveRotationReview(id);
      else await api.rejectRotationReview(id);
      onResolved?.();
    } catch (err: any) {
      const msg = String(err?.message || err);
      // ApiError carries the status as a field; the substring checks below
      // remain for non-ApiError throws. `is()` prefers the field.
      const is = (code: number) => err?.status === code || msg.includes(String(code));
      // Lead with what did NOT happen — the operator's first question after a
      // failed approve is whether anything was sent.
      setErrors((e) => ({
        ...e,
        [id]: is(403)
          ? "REFUSED — nothing closed. Needs Operator API Key: Risk → paste SECRET_KEY → Save."
          : is(423)
          ? "REFUSED — nothing closed. Kill switch is engaged."
          : is(409)
          ? "STALE — nothing closed. The position already closed since this review was raised."
          : is(404)
          ? "GONE — nothing closed. This review was already approved or rejected."
          : `FAILED — ${msg}`,
      }));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      {reviews.map((entry) => {
        const r = entry.review;
        const inc = r.incumbent, chal = r.challenger;
        const actionable = r.recommendation === "replace" && !!entry.incumbent_trade_id;
        const err = errors[entry.review_id];

        return (
          <div key={entry.review_id} style={{
            padding: "14px 16px", borderBottom: "1px solid var(--line-dim)",
          }}>
            <div style={{
              display: "flex", justifyContent: "space-between",
              alignItems: "center", marginBottom: 10,
            }}>
              <span style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
                Close <b>{inc.ticker}</b> → open <b>{chal.ticker}</b>
              </span>
              <span style={{
                fontSize: 9, letterSpacing: 0.5, padding: "2px 6px", borderRadius: 2,
                border: `1px solid ${actionable ? "var(--amber)" : "var(--line)"}`,
                color: actionable ? "var(--amber)" : "var(--text-dim)",
              }}>
                {r.recommendation.toUpperCase().replace("_", " ")}
              </span>
            </div>

            <div style={{
              display: "grid", gridTemplateColumns: "1.4fr 1fr 1fr", gap: 8,
              fontSize: 9, letterSpacing: 0.5, color: "var(--text-dim)",
              paddingBottom: 4, borderBottom: "1px solid var(--line)",
            }}>
              <span />
              <span style={{ textAlign: "right" }}>INCUMBENT</span>
              <span style={{ textAlign: "right" }}>CHALLENGER</span>
            </div>

            <Row label="Composite" uncal
                 incumbent={num(inc.composite)} challenger={num(chal.composite)} />
            <Row label="Alpha Edge" uncal
                 incumbent={num(inc.alpha_edge, 0)} challenger={num(chal.alpha_edge, 0)} />
            <Row label="Quality score" uncal
                 incumbent={num(inc.quality_score)} challenger={num(chal.quality_score)} />
            <Row label="Confidence" uncal
                 incumbent={num(inc.confidence, 2)} challenger={num(chal.confidence, 2)} />
            <Row label="P(target before stop)"
                 incumbent={inc.p_target_before_stop == null ? "—"
                   : `${(inc.p_target_before_stop * 100).toFixed(0)}%`}
                 challenger={chal.p_target_before_stop == null ? "—"
                   : `${(chal.p_target_before_stop * 100).toFixed(0)}%`} />
            <Row label="In flagged cluster"
                 incumbent={inc.in_flagged_cluster === null ? "—" : inc.in_flagged_cluster ? "yes" : "no"}
                 challenger={chal.in_flagged_cluster === null ? "—" : chal.in_flagged_cluster ? "yes" : "no"} />

            <div style={{
              marginTop: 8, padding: "6px 8px", background: "var(--bg-dim)",
              borderLeft: "2px solid var(--line)", fontSize: 10,
              color: "var(--text-dim)", lineHeight: 1.5,
            }}>
              <div>
                <b>Expected R:</b> not computed — no calibrated probability model
                exists. Composite is a heuristic average, not an expectancy.
              </div>
              <div style={{ marginTop: 4 }}>
                <b>Sunk cost excluded.</b> {inc.ticker} unrealized{" "}
                <span style={{
                  color: (inc.unrealized_pnl_context_only ?? 0) < 0 ? "var(--red)" : "var(--green)",
                }}>
                  {money(inc.unrealized_pnl_context_only)}
                </span>{" "}
                — shown for context only, and deliberately not part of the
                comparison above. That loss is incurred whether or not the
                position is closed.
              </div>
            </div>

            {r.reasons?.length > 0 && (
              <ul style={{
                margin: "8px 0 0", paddingLeft: 16, fontSize: 10,
                color: "var(--text-dim)", lineHeight: 1.5,
              }}>
                {r.reasons.map((reason, i) => <li key={i}>{reason}</li>)}
              </ul>
            )}

            {err && (
              <div role="alert" style={{
                marginTop: 8, padding: "6px 8px", fontSize: 10,
                border: "1px solid var(--red)", color: "var(--red)", borderRadius: 2,
              }}>{err}</div>
            )}

            <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center" }}>
              <HoldToConfirmButton
                label={actionable ? "APPROVE REPLACEMENT" : "APPROVE (not recommended)"}
                confirmingLabel="HOLD TO APPROVE…"
                disabled={busy === entry.review_id || !entry.incumbent_trade_id}
                onConfirm={() => act(entry.review_id, "approve")}
              />
              <Button
                onClick={() => act(entry.review_id, "reject")}
                disabled={busy === entry.review_id}
              >
                REJECT
              </Button>
              {entry.queued_at && (
                <span style={{ fontSize: 9, color: "var(--text-dim)", marginLeft: "auto" }}>
                  raised {new Date(entry.queued_at).toLocaleTimeString()}
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
