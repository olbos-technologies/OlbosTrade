/** GET /api/admin/position-trades: each broker holding next to every trade row recorded for it. */
export type PositionTradeCode =
  | "tracked" | "quantity_mismatch" | "direction_mismatch" | "hidden_from_positions_page"
  | "open_row_other_owner" | "pending_confirmation" | "only_closed_rows" | "no_trade_row"
  | "row_without_broker_position";

export interface PositionTradeRow {
  id: string;
  status: string;
  strategy: string;
  asset_class: string;
  direction: "long" | "short" | null;
  quantity: number | null;
  owner: string;
  shown_on_positions_page: boolean;
  entry_date: string | null;
  exit_date: string | null;
  approved_by: string | null;
}

export interface PositionTradeClaim {
  asset_class: string;
  state: string;
  claimed_at: string | null;
  reason: string | null;
}

export interface PositionTradeItem {
  symbol: string;
  asset_class: string;
  broker_quantity: number | null;
  code: PositionTradeCode;
  severity: "ok" | "info" | "attention";
  explanation: string;
  positions_page_shows: string;
  trades: PositionTradeRow[];
  claims: PositionTradeClaim[];
}

export interface PositionTradesResponse {
  viewer_scope: string;
  operator_scope: string;
  generated_at: string;
  broker: { available: boolean; error: string | null };
  summary: { holdings: number; tracked: number; attention: number; rows_without_position: number } | null;
  items: PositionTradeItem[];
}
