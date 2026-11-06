/** One unresolved position claim, as GET /api/admin/position-claims returns it. */
export interface PositionClaim {
  claim_token: string;
  scope: string | null;
  underlying: string;
  asset_class: string;
  /** `submitted`: intent recorded, outcome never confirmed. `unknown`: submission failed, outcome undetermined. */
  state: string;
  idempotency_key: string | null;
  dispatch_id: string | null;
  unresolved_reason: string | null;
  claimed_at: string | null;
  submitted_at: string | null;
  releasing_allows_a_new_entry: boolean;
}

export interface PositionClaimsResponse {
  count: number;
  claims: PositionClaim[];
}
