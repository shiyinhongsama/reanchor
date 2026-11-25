/** API 客户端：所有后端调用的唯一入口。 */

const BASE = "/api/v1";
const TOKEN_KEY = "reanchor_token";

export const auth = {
  token: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

/** 信号已读标记：executor_id -> 已读的最大信号 id（本地存储，用于未读高亮）。 */
const SEEN_KEY = "reanchor_seen_signals";
export const seenSignals = {
  get: (): Record<string, number> => {
    try {
      return JSON.parse(localStorage.getItem(SEEN_KEY) ?? "{}");
    } catch {
      return {};
    }
  },
  mark: (executorId: string, signalId: number) => {
    const m = seenSignals.get();
    const next = Math.max(m[executorId] ?? 0, signalId);
    if (next !== m[executorId]) {
      m[executorId] = next;
      localStorage.setItem(SEEN_KEY, JSON.stringify(m));
    }
  },
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const token = auth.token();
  const r = await fetch(`${BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...init,
  });
  if (r.status === 401 && !path.startsWith("/auth/login")) {
    auth.clear();
    if (window.location.pathname !== "/login") window.location.href = "/login";
  }
  if (!r.ok) {
    let detail = `${r.status}`;
    try {
      const j = await r.json();
      detail = j.detail || detail;
    } catch { /* 非 JSON 错误体 */ }
    throw new Error(detail);
  }
  return r.json() as Promise<T>;
}

// ---------- 类型（与后端 schemas 对齐） ----------

export type ParamSpec = { type: string; default: number | string; min?: number; max?: number; options?: string[] };

export interface StrategyInfo {
  name: string;
  description: string;
  params: Record<string, ParamSpec>;
}

export interface BacktestRequest {
  strategy: string;
  symbols: string[];
  timeframe: string;
  start?: string | null;
  end?: string | null;
  params: Record<string, number | string>;
  risk_frac: number;
  vol_target: number;
  max_leverage: number;
  fee: number;
  slippage: number;
  initial_equity: number;
  deposit_amount?: number;
  deposit_every_days?: number;
}

export interface BacktestJob {
  job_id: string;
  status: "pending" | "running" | "done" | "failed";
  created_at: string;
  finished_at?: string | null;
  error?: string | null;
}

export interface SymbolMetrics {
  symbol: string;
  total_return: number;
  annualized: number;
  sharpe: number;
  max_drawdown: number;
  calmar?: number | null;
  trades: number;
  profit_factor?: number | null;
  total_deposited?: number | null;
  net_profit?: number | null;
}

export interface BacktestResult {
  job_id: string;
  per_symbol: SymbolMetrics[];
  portfolio?: SymbolMetrics | null;
}

export interface EquityPoint { t: string; v: number }

export interface BacktestRunSummary {
  job_id: string;
  status: string;
  strategy: string;
  symbols: string[];
  timeframe: string;
  params: Record<string, number | string>;
  risk_frac: number;
  vol_target: number;
  max_leverage: number;
  created_at: string;
  metrics?: SymbolMetrics | null;
}

export interface SweepRequest {
  strategy: string;
  symbols: string[];
  timeframe: string;
  start?: string | null;
  risk_fracs: number[];
  vol_targets: number[];
  param_grid: Record<string, number[]>;
}

export interface WalkForwardFold {
  i: number;
  train: [string, string];
  test: [string, string];
  params: Record<string, number | string>;
  train_metrics: Partial<SymbolMetrics>;
  test_metrics: Partial<SymbolMetrics>;
}

export interface WalkForwardResult {
  criterion: string;
  folds: WalkForwardFold[];
  aggregate: {
    profitable_folds: string;
    mean_annualized?: number | null;
    median_annualized?: number | null;
    std_annualized?: number | null;
    mean_sharpe?: number | null;
    worst_drawdown?: number | null;
  };
  oos_metrics: Partial<SymbolMetrics>;
  oos_equity: EquityPoint[];
}

export interface WalkForwardJob {
  wf_id: string;
  status: "running" | "done" | "failed";
  error?: string;
  done?: number;
  result?: WalkForwardResult;
}

export interface WalkForwardRequest {
  strategy: string;
  symbol: string;
  timeframe: string;
  start?: string | null;
  folds: number;
  train_frac: number;
  criterion: string;
  params: Record<string, number | string>;
  param_grid: Record<string, number[]>;
  risk_frac: number;
  vol_target: number;
  max_leverage: number;
  fee: number;
  slippage: number;
  initial_equity: number;
}

export interface SweepJob {
  sweep_id: string;
  status: "running" | "done";
  total: number;
  done: number;
  job_ids: string[];
  created_at: string;
}

export interface CostCell extends Partial<SymbolMetrics> {
  fee: number;
  slippage: number;
  error?: string;
}

export interface CostSensitivityJob {
  cs_id: string;
  status: "running" | "done" | "failed";
  total: number;
  done: number;
  created_at?: string;
  error?: string;
  result?: { fees: number[]; slippages: number[]; cells: CostCell[] };
}

export interface CostSensitivityRequest {
  strategy: string;
  symbols: string[];
  timeframe: string;
  start?: string | null;
  params: Record<string, number | string>;
  risk_frac: number;
  vol_target: number;
  max_leverage: number;
  initial_equity: number;
  fees: number[];
  slippages: number[];
}

export interface Trade {
  symbol?: string;
  entry_time: string;
  exit_time: string;
  side: "long" | "short";
  entry_px: number;
  exit_px: number;
  qty: number;
  vt?: number;
  entry_stop?: number;
  reason: "signal_exit" | "atr_stop" | "end";
  pnl: number;
  equity_after: number;
}

export interface Executor {
  id: string;
  name: string;
  strategy_name: string;
  symbol: string;
  timeframe: string;
  params: Record<string, number | string>;
  equity_budget: number;
  risk_frac: number;
  vol_target: number;
  max_leverage: number;
  status: "running" | "paused" | "stopped";
  state: {
    position?: { side: number; qty: number; entry_px?: number | null; entry_stop: number; current_stop: number; entry_bar: string; aligned_bars?: number } | null;
    cash?: number;
    last_equity?: number;
    consecutive_failures?: number;
  };
  latest_signal?: { id: number; action: string; bar_time: string | null } | null;
  created_at: string;
  updated_at: string;
}

export interface ExecutorCreate {
  name?: string;
  strategy: string;
  symbol: string;
  timeframe: string;
  params: Record<string, number | string>;
  equity_budget: number;
  risk_frac: number;
  vol_target: number;
  max_leverage: number;
}

export interface SignalExplain {
  ema?: number;
  atr?: number;
  slope?: number;
  close?: number;
  dist_atr?: number | null;
  reason?: string;
  long?: Record<string, boolean>;
  short?: Record<string, boolean>;
}

export interface ExecutorSignal {
  executor_id: string;
  symbol: string;
  timeframe: string;
  as_of: string;
  position: Executor["state"]["position"];
  entry_signal: number;
  exit_signal: boolean;
  suggested_qty: number;
  suggested_stop?: number | null;
  close?: number | null;
  atr?: number | null;
  slope?: number | null;
  dry_run: boolean;
  note: string;
  explain?: SignalExplain;
}

export interface SignalLog {
  id: number;
  bar_time: string | null;
  action: string;
  entry_signal: number;
  exit_signal: boolean;
  side: number | null;
  qty: number | null;
  stop: number | null;
  atr: number | null;
  slope: number | null;
  detail: Record<string, unknown>;
  created_at: string | null;
}

export interface LiveStatus {
  enabled: boolean;
  running_executors: number;
  open_positions: Record<string, unknown>[];
}

export interface SchedulerJob { executor_id: string; next_run: string | null }

export interface MarketTicker {
  symbol: string;
  last: number;
  change_pct: number;
  volume_usd: number;
  high_24h: number;
  low_24h: number;
}

export interface MarketOverview {
  as_of: string;
  fng: { value: number; label: string; prev_value?: number } | null;
  breadth: {
    advancers: number;
    decliners: number;
    flat: number;
    avg_change_pct: number | null;
    total_volume_usd: number;
  };
  heatmap: MarketTicker[];
}

export const TIMEFRAMES = ["1m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","2d","1w"] as const;

// ---------- 调用 ----------

export const api = {
  health: async (): Promise<boolean> => {
    try {
      const r = await fetch("/api/health");
      return r.ok;
    } catch {
      return false;
    }
  },
  strategies: () => req<{ strategies: StrategyInfo[] }>("/strategies"),

  login: (password: string) =>
    req<{ token: string; expires_at: number }>("/auth/login", {
      method: "POST", body: JSON.stringify({ password }),
    }),
  me: () => req<{ ok: boolean }>("/auth/me"),

  createBacktest: (body: BacktestRequest) =>
    req<BacktestJob>("/backtests", { method: "POST", body: JSON.stringify(body) }),
  backtest: (id: string) => req<BacktestJob>(`/backtests/${id}`),
  backtestResult: (id: string) => req<BacktestResult>(`/backtests/${id}/result`),
  backtestEquity: (id: string, symbol?: string) =>
    req<{ points: EquityPoint[] }>(`/backtests/${id}/equity${symbol ? `?symbol=${symbol}` : ""}`),
  backtestTrades: (id: string, limit = 500) =>
    req<{ trades: Trade[] }>(`/backtests/${id}/trades?limit=${limit}`),
  backtestBars: (id: string, symbol: string, limit = 1500) =>
    req<{ bars: { t: string; o: number; h: number; l: number; c: number }[] }>(
      `/backtests/${id}/bars?symbol=${encodeURIComponent(symbol)}&limit=${limit}`),
  backtestRuns: (limit = 100) => req<BacktestRunSummary[]>(`/backtests?limit=${limit}`),
  deleteBacktest: (id: string) =>
    fetch(`${BASE}/backtests/${id}`, {
      method: "DELETE",
      headers: auth.token() ? { Authorization: `Bearer ${auth.token()}` } : {},
    }).then((r) => {
      if (r.status === 401) { auth.clear(); window.location.href = "/login"; }
      if (!r.ok) throw new Error(`${r.status}`);
    }),
  createSweep: (body: SweepRequest) =>
    req<SweepJob>("/backtests/sweep", { method: "POST", body: JSON.stringify(body) }),
  sweep: (id: string) => req<SweepJob>(`/backtests/sweeps/${id}`),
  walkforward: (body: WalkForwardRequest) =>
    req<{ wf_id: string }>("/backtests/walkforward", { method: "POST", body: JSON.stringify(body) }),
  walkforwardStatus: (id: string) => req<WalkForwardJob>(`/backtests/walkforward/${id}`),
  costSensitivity: (body: CostSensitivityRequest) =>
    req<CostSensitivityJob>("/backtests/cost-sensitivity", { method: "POST", body: JSON.stringify(body) }),
  costSensitivityStatus: (id: string) => req<CostSensitivityJob>(`/backtests/cost-sensitivity/${id}`),

  executors: () => req<Executor[]>("/executors"),
  createExecutor: (body: ExecutorCreate) =>
    req<Executor>("/executors", { method: "POST", body: JSON.stringify(body) }),
  updateExecutor: (id: string, patch: Partial<ExecutorCreate> & { status?: string }) =>
    req<Executor>(`/executors/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteExecutor: (id: string) =>
    req<Executor>(`/executors/${id}?confirm=true`, { method: "DELETE" }),
  executorSignal: (id: string) => req<ExecutorSignal>(`/executors/${id}/signal`),
  executorEquity: (id: string, limit = 2000) =>
    req<{ points: { t: string; equity: number; cash: number; unrealized: number; close: number | null }[] }>(
      `/executors/${id}/equity?limit=${limit}`),
  executorSignals: (id: string, limit = 100) =>
    req<{ signals: SignalLog[] }>(`/executors/${id}/signals?limit=${limit}`),
  executorBars: (id: string, limit = 300) =>
    req<{ bars: { t: string; o: number; h: number; l: number; c: number }[] }>(
      `/executors/${id}/bars?limit=${limit}`),

  marketOverview: () => req<MarketOverview>("/market/overview"),

  liveStatus: () => req<LiveStatus>("/live/status"),
  scheduler: () => req<{ jobs: SchedulerJob[] }>("/live/scheduler"),
};
