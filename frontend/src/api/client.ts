/**
 * Central API client for the options trading platform backend.
 * All fetch calls go through this module — never fetch directly in components.
 */

// When running via Vite dev server (Docker or local), use "" so fetch calls
// go to /api/... — Vite's proxy forwards them to the backend container.
// Set VITE_API_URL only if you need to bypass the proxy (e.g. direct calls from a static build).
const BASE_URL = "";

const OPERATOR_KEY_STORAGE = "olbos.operatorApiKey";

/** Session-scoped operator API key (never bake SECRET_KEY into the bundle). */
export function getOperatorApiKey(): string {
  try {
    return sessionStorage.getItem(OPERATOR_KEY_STORAGE) || "";
  } catch {
    return "";
  }
}

export function setOperatorApiKey(key: string): void {
  try {
    if (key) sessionStorage.setItem(OPERATOR_KEY_STORAGE, key);
    else sessionStorage.removeItem(OPERATOR_KEY_STORAGE);
  } catch {
    /* ignore */
  }
}

/** Headers for authenticated mutate calls — use from raw fetch sites too. */
export function apiAuthHeaders(extra?: HeadersInit): HeadersInit {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const key = getOperatorApiKey();
  if (key) headers["X-Api-Key"] = key;
  if (extra) {
    const e = new Headers(extra);
    e.forEach((v, k) => { headers[k] = v; });
  }
  return headers;
}

/**
 * An HTTP failure, with the status kept as a field rather than baked into a
 * string.
 *
 * Callers need to branch on it: a 403 from a mutate route means the operator
 * API key is missing or stale and the fix is to re-enter it, which is a wholly
 * different message from "the broker rejected this order". Parsing that back
 * out of "API error 403: Forbidden" is not something call sites should be
 * doing.
 */
export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Build an ApiError from a failed response, preferring the server's own words.
 *
 * FastAPI puts the useful half in {"detail": ...}; res.statusText is
 * "Forbidden", which tells an operator nothing they can act on. The body is
 * read defensively — an error response is not guaranteed to be JSON (nginx
 * returns HTML for a 502), and a parse failure here would replace a real
 * status with an unrelated SyntaxError.
 */
async function apiError(res: Response): Promise<ApiError> {
  let detail = "";
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") detail = body.detail;
    else if (body?.detail) detail = JSON.stringify(body.detail);
  } catch {
    /* not JSON — statusText it is */
  }
  const why = detail || res.statusText || "request failed";
  // The status stays IN the message as well as on the field.
  //
  // `.status` is the right thing to branch on and callers are being moved to
  // it, but several still classify by substring — RotationReviewPanel keys
  // 403/423/409/404 to four different "nothing was closed because…" messages,
  // and TerminalLayout keys 403 to the operator-key hint. A detail-only
  // message silently downgraded all of those to generic failure text, which
  // was a regression introduced by this very refactor and caught in review on
  // PR #64. Keeping the prefix costs nothing and means no caller loses its
  // meaning on a deploy boundary.
  return new ApiError(res.status, `${res.status}: ${why}`);
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    // Same-origin is already the default, stated explicitly because the whole
    // session mechanism depends on it: the session cookie is httpOnly, so this
    // is the only way it reaches the server. A later change to BASE_URL that
    // points at another origin would silently stop sending it, and every call
    // would 401 for a reason nothing here names.
    credentials: "same-origin",
    headers: apiAuthHeaders(options?.headers),
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

// ── Health ────────────────────────────────────────────────────────────────────
export const api = {
  health: () => request<{ status: string }>("/health"),

  // ── Backtest ──────────────────────────────────────────────────────────────
  runBacktest: (body: object) => request("/api/backtest/run", { method: "POST", body: JSON.stringify(body) }),
  runEquityBacktest: (body: object) => request("/api/backtest/run-equity", { method: "POST", body: JSON.stringify(body) }),
  getBacktestResults: (id: string) => request(`/api/backtest/${id}/results`),
  getBacktestHistory: (limit?: number) =>
    request(`/api/backtest/history${limit ? `?limit=${limit}` : ""}`),
  compareStrategies: (body: object) => request("/api/backtest/compare", { method: "POST", body: JSON.stringify(body) }),
  getBaselineComparison: (symbol: string, start: string, end: string) =>
    request(`/api/backtest/baseline-comparison?symbol=${encodeURIComponent(symbol)}&start=${start}&end=${end}`),

  // ── Market Data ───────────────────────────────────────────────────────────
  getSnapshot: (symbol: string) => request(`/api/market/snapshot/${symbol}`),
  getRegime: () => request("/api/market/regime"),
  getOptionsChain: (symbol: string, expiry: string) => request(`/api/market/options-chain/${symbol}?expiry=${expiry}`),
  getIVRank: (symbol: string) => request(`/api/market/iv-rank/${symbol}`),
  getSectorRotation: () => request("/api/market/sector-rotation"),

  // ── Paper Trading ─────────────────────────────────────────────────────────
  getPositions: () => request("/api/paper-trade/positions"),
  getPortfolio: () => request("/api/paper-trade/portfolio"),
  toggleStrategy: (strategy: string) => request(`/api/paper-trade/toggle/${strategy}`, { method: "POST" }),
  getTradeHistory: (params?: { limit?: number; status?: string }) => {
    const q = new URLSearchParams();
    if (params?.limit != null) q.set("limit", String(params.limit));
    if (params?.status) q.set("status", params.status);
    const qs = q.toString();
    return request(`/api/paper-trade/history${qs ? `?${qs}` : ""}`);
  },
  getGreeksSummary: () => request("/api/paper-trade/greeks-summary"),

  // ── Risk ──────────────────────────────────────────────────────────────────
  getPortfolioState: () => request("/api/risk/portfolio-state"),
  getTradeApproval: (tradeId: string) => request(`/api/risk/approval/${tradeId}`),
  getDailyPnl: () => request("/api/risk/daily-pnl"),
  getLatestReconciliation: () => request("/api/risk/reconciliation/latest"),
  getReconciliationHistory: (limit?: number) =>
    request(`/api/risk/reconciliation/history${limit ? `?limit=${limit}` : ""}`),
  runReconciliation: () => request("/api/risk/reconciliation/run", { method: "POST" }),
  getKillSwitchStatus: () => request("/api/risk/kill-switch/status"),
  triggerKillSwitch: () => request("/api/risk/kill-switch/trigger", { method: "POST" }),
  // Operator-facing engage (dashboard button) — protected by the app auth layer.
  engageKillSwitch: () => request("/api/risk/kill-switch/engage", { method: "POST" }),
  resetKillSwitch: (code: string) =>
    request("/api/risk/kill-switch/reset", {
      method: "POST",
      body: JSON.stringify({ authorization_code: code }),
    }),

  // ── Options Income (Wheel & CSP) ──────────────────────────────────────────
  screenCsp: (body: object) =>
    request("/api/options/csp/screen", { method: "POST", body: JSON.stringify(body) }),

  // ── Intelligence Hub ──────────────────────────────────────────────────────
  getCatalystCalendar: (symbols?: string, daysAhead = 45) =>
    request(`/api/intel/calendar?days_ahead=${daysAhead}${symbols ? `&symbols=${symbols}` : ""}`),
  getWatchlists: () => request("/api/intel/watchlists"),
  getDataQuality: (symbol: string) => request(`/api/intel/data-quality/${symbol}`),
  getWhyMoving: (symbol: string) => request(`/api/intel/why-moving/${symbol}`),
  getSymbolNews: (symbol: string, limit = 15) => request(`/api/intel/news/${symbol}?limit=${limit}`),
  getSymbolFilings: (symbol: string, limit = 15) => request(`/api/intel/filings/${symbol}?limit=${limit}`),
  getClassifiedNews: (symbol: string, limit = 15) => request(`/api/intel/classify/${symbol}?limit=${limit}`),
  getInsiderIntel: (symbol: string) => request(`/api/intel/insider/${symbol}`),

  // ── Chart Intelligence ────────────────────────────────────────────────────
  getMarketBias: (symbol: string, strategy = "default") => request(`/api/chart/bias/${symbol}?strategy=${strategy}`),
  getTimeframeAlignment: (symbol: string, strategy = "default") => request(`/api/chart/alignment/${symbol}?strategy=${strategy}`),
  getMarketStructure: (symbol: string, timeframe = "1d") => request(`/api/chart/structure/${symbol}?timeframe=${timeframe}`),
  getConfirmation: (symbol: string, strategy = "default") => request(`/api/chart/confirmation/${symbol}?strategy=${strategy}`),
  getSetupScanner: (watchlist?: string, strategy = "default") =>
    request(`/api/chart/scanner?strategy=${strategy}${watchlist ? `&watchlist=${watchlist}` : ""}`),

  // ── Smart Alerts + Notifications ──────────────────────────────────────────
  getAlertRules: () => request("/api/alerts/rules"),
  createAlertRule: (body: object) => request("/api/alerts/rules", { method: "POST", body: JSON.stringify(body) }),
  deleteAlertRule: (id: string) => request(`/api/alerts/rules/${id}`, { method: "DELETE" }),
  toggleAlertRule: (id: string, enabled: boolean) => request(`/api/alerts/rules/${id}/toggle?enabled=${enabled}`, { method: "POST" }),
  getNotifications: (unreadOnly = false) => request(`/api/notifications?unread_only=${unreadOnly}`),
  getUnreadCount: () => request("/api/notifications/unread-count"),
  markNotificationRead: (id: string) => request(`/api/notifications/${id}/read`, { method: "POST" }),
  markAllNotificationsRead: () => request("/api/notifications/read-all", { method: "POST" }),

  // ── Guardrails ────────────────────────────────────────────────────────────
  getGuardrailStatus: () => request("/api/guardrails/status"),
  getGuardrailHistory: () => request("/api/guardrails/history"),
  getTradingMode: () => request("/api/guardrails/trading-mode"),

  // ── Strategy & Signals ────────────────────────────────────────────────────
  getStrategyRegistry: () => request("/api/strategy/registry"),
  getStrategyProfile: (strategyId: string) => request(`/api/strategy/registry/${strategyId}`),
  getSignalCalendar: (days = 30) =>
    request(`/api/signals/calendar?days=${days}`),
  getStrategyHealth: (minSample?: number) =>
    request(`/api/strategy/health${minSample != null ? `?min_sample=${minSample}` : ""}`),
  getAlphaEdge: (ticker: string, assetType: "equity" | "options" = "equity") =>
    request(`/api/alpha-edge/${encodeURIComponent(ticker)}?asset_type=${assetType}`),
  getStrategyPresets: (strategyId?: string) =>
    request(`/api/strategy${strategyId ? `/${strategyId}/presets` : "/presets"}`),
  getStrategySnapshots: (strategyId: string) => request(`/api/strategy/${strategyId}/snapshots`),
  createStrategySnapshot: (body: object) => request("/api/strategy/snapshots", { method: "POST", body: JSON.stringify(body) }),
  restoreStrategySnapshot: (snapshotId: string) => request(`/api/strategy/snapshots/${snapshotId}/restore`, { method: "POST" }),
  compareStrategySnapshots: (left: string, right: string) =>
    request(`/api/strategy/snapshots/compare?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`),
  getStrategyConfig: () => request("/api/strategy/config"),
  updateStrategyConfig: (body: object) => request("/api/strategy/config", { method: "PUT", body: JSON.stringify(body) }),
  getCurrentSignals: () => request("/api/strategy/signals/current"),
  getSignalExplanation: (id: string) => request(`/api/strategy/signals/${id}/explanation`),

  // ── Equity Workstation ────────────────────────────────────────────────────
  scanEquitySignals: () => request("/api/equity/scan", { method: "POST" }),
  getEquitySignals: (limit?: number) => request(`/api/equity/signals${limit ? `?limit=${limit}` : ""}`),
  getOptionsSignals: (limit?: number) => request(`/api/options/signals${limit ? `?limit=${limit}` : ""}`),
  scanOptionsSignals: () => request("/api/options/signals/scan", { method: "POST" }),
  getOptionsSignalHistory: (params?: { limit?: number; strategy?: string; ticker?: string }) => {
    const q = params
      ? "?" + new URLSearchParams(
          Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined)) as any
        ).toString()
      : "";
    return request(`/api/options/signals/history${q}`);
  },
  getEquityChart: (symbol: string, params?: { timeframe?: string; limit?: number }) => {
    const search = new URLSearchParams();
    if (params?.timeframe) search.set("timeframe", params.timeframe);
    if (params?.limit) search.set("limit", String(params.limit));
    const q = search.toString();
    return request(`/api/equity/chart/${symbol}${q ? `?${q}` : ""}`);
  },

  // ── Research ──────────────────────────────────────────────────────────────
  getComparison: () => request("/api/research/comparison"),
  runComparison: (body: object) => request("/api/research/run-comparison", { method: "POST", body: JSON.stringify(body) }),
  getModelPerformance: () => request("/api/research/model-performance"),
  transitionExperiment: (expId: string, body: object) =>
    request(`/api/research/lab/experiments/${expId}/transition`, { method: "POST", body: JSON.stringify(body) }),
  getForecast: <T = unknown>(symbol: string, horizon: number) =>
    request<T>(`/api/forecasts/symbols/${encodeURIComponent(symbol)}?horizon=${horizon}`),

  // ── Journal ───────────────────────────────────────────────────────────────
  createJournalEntry: (body: object) => request("/api/journal/entry", { method: "POST", body: JSON.stringify(body) }),
  getJournalEntries: () => request("/api/journal/entries"),
  getJournalEntry: (tradeId: string) => request(`/api/journal/${tradeId}`),
  updateJournalEntry: (id: string, body: object) => request(`/api/journal/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  getTagPerformance: () => request("/api/journal/analytics/tags"),
  getMistakeFrequency: () => request("/api/journal/analytics/mistakes"),
  getRuleBreachImpact: () => request("/api/journal/analytics/rule-breach-impact"),
  getMonthlyReview: (month: string) => request(`/api/journal/review/monthly/${month}`),

  // ── Trading Mode ────────────────────────────────────────────────────────────
  getCurrentMode:   () => request("/api/mode/current"),
  getAllModes:       () => request("/api/mode/all"),
  setTradingMode:   (body: { mode: string; confirmed: boolean }) =>
    request("/api/mode/set", { method: "POST", body: JSON.stringify(body) }),
  resetToBalanced:  () => request("/api/mode/reset-to-balanced", { method: "POST" }),

  // ── Trade Desk (Execution Modes) ───────────────────────────────────────────
  // Capital Rotation reviews. The GET is unauthenticated like other reads;
  // approve/reject are auth-gated server-side and will 403 without the
  // Operator API Key, the same way the execution-mode toggle does.
  getRotationReviews: () => request("/api/trade-desk/rotation-reviews"),
  approveRotationReview: (reviewId: string) =>
    request(`/api/trade-desk/rotation-review/${encodeURIComponent(reviewId)}/approve`,
            { method: "POST" }),
  rejectRotationReview: (reviewId: string) =>
    request(`/api/trade-desk/rotation-review/${encodeURIComponent(reviewId)}/reject`,
            { method: "POST" }),

  getExecutionMode:   () => request("/api/trade-desk/execution-mode"),
  setExecutionMode:   (mode: string) =>
    request("/api/trade-desk/execution-mode", { method: "POST", body: JSON.stringify({ mode }) }),
  getTradeDeskKillSwitch: () => request("/api/trade-desk/kill-switch"),
  setTradeDeskKillSwitch: (engaged: boolean, authorizationCode?: string) =>
    request("/api/trade-desk/kill-switch", {
      method: "POST",
      body: JSON.stringify({
        engaged,
        ...(authorizationCode != null ? { authorization_code: authorizationCode } : {}),
      }),
    }),
  evaluateEquityIntent: (body: object) =>
    request("/api/trade-desk/evaluate-equity", { method: "POST", body: JSON.stringify(body) }),
  evaluateOptionsIntent: (body: object) =>
    request("/api/trade-desk/evaluate-options", { method: "POST", body: JSON.stringify(body) }),
  closePosition: (tradeId: string, orderType: "market" | "limit" = "market", limitPrice?: number) =>
    request("/api/trade-desk/close-position", {
      method: "POST",
      body: JSON.stringify({
        trade_id: tradeId,
        order_type: orderType,
        ...(limitPrice != null ? { limit_price: limitPrice } : {}),
      }),
    }),
  closeUntrackedPosition: (symbol: string, orderType: "market" | "limit" = "market", limitPrice?: number) =>
    request("/api/trade-desk/close-untracked-position", {
      method: "POST",
      body: JSON.stringify({
        symbol,
        order_type: orderType,
        ...(limitPrice != null ? { limit_price: limitPrice } : {}),
      }),
    }),
  getPendingApprovals: () => request("/api/trade-desk/pending"),
  approveSignal:       (id: string) =>
    request(`/api/trade-desk/approve/${id}`, { method: "POST" }),
  rejectSignal:        (id: string) =>
    request(`/api/trade-desk/reject/${id}`, { method: "POST" }),
  getExecutionLog:     () => request("/api/trade-desk/execution-log"),

  // ── Mode Analytics ──────────────────────────────────────────────────────────
  getModeAnalytics: (params?: { date_from?: string; date_to?: string }) => {
    const q = params ? "?" + new URLSearchParams(params as any).toString() : "";
    return request(`/api/analytics/by-mode${q}`);
  },
  getModeDetail:         (mode: string) => request(`/api/analytics/mode/${mode}`),
  getSignalScoreImpact:  ()             => request("/api/analytics/signal-score-impact"),

  // ── Crypto (phase 1: read-only signals, no execution) ───────────────────────
  getCryptoWatchlist: () => request<CryptoWatchlist>("/api/crypto/watchlist"),
  getCryptoSignals:   (params?: { limit?: number; routable_only?: boolean }) => {
    const q = new URLSearchParams();
    if (params?.limit != null) q.set("limit", String(params.limit));
    if (params?.routable_only) q.set("routable_only", "true");
    const qs = q.toString();
    return request<CryptoSignalList>(`/api/crypto/signals${qs ? `?${qs}` : ""}`);
  },
  runCryptoScan: () => request<CryptoScanSummary>("/api/crypto/scan", { method: "POST" }),

  // ── Signal Research (forward-outcome tracking) ──────────────────────────────
  // asset_type is passed explicitly rather than relying on the backend default,
  // so which population these numbers describe is visible at the call site.
  getSignalOutcomes:    (assetType: string = "equity") =>
    request(`/api/signal-research/outcomes?asset_type=${encodeURIComponent(assetType)}`),
  getSignalOutcomesRaw: (params?: { limit?: number; status?: string; asset_type?: string }) => {
    const q = params
      ? "?" + new URLSearchParams(
          Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined)) as any
        ).toString()
      : "";
    return request(`/api/signal-research/outcomes/raw${q}`);
  },

  // ── Options Flow (Options Intelligence module) ──────────────────────────────
  getOptionsFlow: (params?: Record<string, string | number | undefined>) => {
    const clean = Object.fromEntries(
      Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== "")
    ) as Record<string, string>;
    const q = Object.keys(clean).length ? "?" + new URLSearchParams(clean).toString() : "";
    return request<{ count: number; results: any[] }>(`/api/options-flow${q}`);
  },
  getOptionsFlowSummary: () => request<any>("/api/options-flow/summary"),

  // ── Broker connections (a user's OWN broker account) ────────────────────────
  // Note what these never carry back: the API key and secret go UP and are
  // never returned. The list shows key_last4, which the server stores in the
  // clear precisely so that a read-only screen never has to decrypt anything.
  listBrokerConnections: () =>
    request<BrokerConnectionList>("/api/brokers/connections"),
  connectBroker: (body: ConnectBrokerRequest) =>
    request<ConnectBrokerResponse>("/api/brokers/connections", {
      method: "POST", body: JSON.stringify(body),
    }),
  verifyBrokerConnection: (id: string) =>
    request<{ ok: boolean; verified: boolean; detail?: string }>(
      `/api/brokers/connections/${encodeURIComponent(id)}/verify`, { method: "POST" }),
  disconnectBroker: (id: string) =>
    request<{ ok: boolean; detail: string }>(
      `/api/brokers/connections/${encodeURIComponent(id)}`, { method: "DELETE" }),

  // ── Account (the signed-in person, not the platform) ───────────────────────
  // These routes shipped in #70/#72 with no UI at all: until now the only
  // account surface in the app was the status-bar menu, so there was no way
  // to change a password without a shell on the server.
  getMe: () => request<{ user: AccountUser }>("/api/auth/me"),
  changePassword: (body: { current_password: string; new_password: string }) =>
    request<{ ok: boolean; other_sessions_revoked: number }>(
      "/api/auth/password", { method: "POST", body: JSON.stringify(body) }),
  listSessions: () => request<{ sessions: AccountSession[] }>("/api/auth/sessions"),
  revokeSession: (id: string) =>
    request<{ ok: boolean }>(
      `/api/auth/sessions/${encodeURIComponent(id)}/revoke`, { method: "POST" }),
};

export interface AccountUser {
  id: string;
  email: string;
  tier: string;
}

export interface AccountSession {
  id: string;
  created_at: string;
  last_seen_at: string | null;
  user_agent: string | null;
  ip: string | null;
  /** The session making this request. Never offered a revoke button — use
   *  Sign out for that, which also clears the cookie. */
  current: boolean;
}

/** Mirrors backend auth_service. A shorter cap here would reject a password
 *  the server would have accepted, with a message blaming the user. */
export const MIN_PASSWORD_LEN = 12;
export const MAX_PASSWORD_LEN = 1024;

export interface CryptoWatchlist {
  enabled: boolean;
  /** Always "disabled" in phase 1 — the scan has no path to the order layer. */
  execution: string;
  phase: number;
  symbols: { symbol: string; alpaca_symbol: string }[];
  count: number;
  min_confidence: number;
  scan_interval_minutes: number;
  max_position_pct: number;
  engine_version: string;
  data_source: string;
}

export interface CryptoSignal {
  id: string;
  ticker: string;
  asset_type: "crypto";
  action: "BUY" | "SELL" | "HOLD";
  confidence: number;
  generated_at: string;
  routable: boolean;
  regime: string;
  reasons?: Record<string, unknown>;
  trade_plan?: {
    entry_price?: number;
    stop_price?: number;
    target_price?: number;
    target_move_pct?: number;
    risk_reward?: number;
  };
  indicators?: {
    rsi?: number;
    macd?: number;
    bb_pct_b?: number;
    atr?: number;
    volume_ratio?: number;
  };
  opportunity_score?: { score: number; components: Record<string, number> } | null;
  outcome_id?: string;
}

export interface CryptoSignalList {
  enabled: boolean;
  execution: string;
  signals: CryptoSignal[];
  total: number;
}

export interface CryptoScanSummary {
  enabled: boolean;
  scanned: number;
  signals: number;
  routable: number;
  recorded: number;
  skipped_insufficient_bars: number;
  skipped_unrepresentable_price: number;
  errors: number;
}

export interface BrokerConnection {
  id: string;
  broker: string;
  environment: "paper" | "live";
  label: string;
  key_last4: string;
  status: "active" | "revoked";
  created_at: string | null;
  last_verified_at: string | null;
  verified: boolean;
}

export interface BrokerConnectionList {
  connections: BrokerConnection[];
  /** False until per-user order routing ships. The UI says so rather than
   *  letting a user assume their orders already go to their own account. */
  execution_routing_enabled: boolean;
  note?: string;
}

export interface ConnectBrokerRequest {
  broker: string;
  environment: string;
  api_key: string;
  secret_key: string;
  label?: string;
}

export interface ConnectBrokerResponse {
  ok: boolean;
  connection: BrokerConnection;
  verification: Record<string, unknown> | null;
  /** Set when the broker could not be REACHED — not when it said no. A
   *  rejected key never reaches the database, so it never reaches here. */
  unverified_reason: string | null;
  execution_routing_enabled: boolean;
}

/** Build the absolute WebSocket URL for the options-flow live stream. */
export function optionsFlowWsUrl(params?: Record<string, string | number | undefined>): string {
  const clean = Object.fromEntries(
    Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== "")
  ) as Record<string, string>;
  const q = Object.keys(clean).length ? "?" + new URLSearchParams(clean).toString() : "";
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api/options-flow/ws${q}`;
}
