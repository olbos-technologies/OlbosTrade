export interface FunnelReason { reason: string; count: number }

export interface FunnelStage {
  key: string;
  label: string;
  count: number;
  reasons: FunnelReason[];
}

export type FunnelVerdictCode =
  | "trading" | "no_data" | "nothing_qualifies" | "mode_manual"
  | "waiting_for_approval" | "qualified_but_never_dispatched" | "blocked_at_execution" | "market_closed_only";

/** Why candidates are sized to zero contracts: the risk budget of one trade against what one spread can lose. */
export interface FunnelSizing {
  attempts: number;
  max_loss_median: number | null;
  max_loss_min: number | null;
  /** Null when the rejections predate the budget being recorded. */
  risk_budget: number | null;
  cheapest_fits_one_contract?: boolean;
}

export interface OptionsFunnel {
  window_days: number;
  since: string;
  generated_at: string;
  execution_mode: string;
  regime: { name: string; options_allowed: boolean; strategies_allowed: string[]; signal_threshold?: number | null } | null;
  frequency_rule: {
    trading_mode: string; min_confidence: number; min_pop: number; max_risk_score: number;
    hard_max_per_day: number; credit_spread_min_pop: number | null;
  } | null;
  scanned: number;
  stages: FunnelStage[];
  sizing?: FunnelSizing | null;
  verdict: { code: FunnelVerdictCode | string; text: string };
  notes: string[];
}
