"""API 请求/响应模型。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .domain.timeframes import SUPPORTED

_TF_PATTERN = "^(" + "|".join(SUPPORTED) + ")$"


class BacktestRequest(BaseModel):
    strategy: str = "ema_pullback_v1"
    symbols: list[str] = Field(default=["BTCUSDT"], description="交易对列表，多品种时逐个执行器独立回测")
    timeframe: str = Field(default="1d", pattern=_TF_PATTERN)
    start: datetime | None = None
    end: datetime | None = None
    params: dict = Field(default_factory=dict, description="策略参数，如 {ema_n: 20, slope_k: 8}")
    risk_frac: float = Field(default=0.05, gt=0, le=0.5)
    vol_target: float = Field(default=0.25, ge=0)
    max_leverage: float = Field(default=3.0, gt=0, le=10)
    fee: float = 0.0005
    slippage: float = 0.0002
    initial_equity: float = Field(default=10_000.0, gt=0)


class BacktestJob(BaseModel):
    job_id: str
    status: str            # pending | running | done | failed
    created_at: datetime
    finished_at: datetime | None = None
    error: str | None = None


class SymbolMetrics(BaseModel):
    symbol: str
    total_return: float
    annualized: float
    sharpe: float
    max_drawdown: float
    calmar: float | None = None
    trades: int
    profit_factor: float | None = None


class BacktestResultResponse(BaseModel):
    job_id: str
    per_symbol: list[SymbolMetrics]
    portfolio: SymbolMetrics | None = None
    # 净值曲线与交易明细通过 /backtests/{job_id}/equity、/trades 分页获取


class BacktestRunSummary(BaseModel):
    """回测存档列表项（对比页用）：headline 指标取组合（多品种）或首个品种。"""
    job_id: str
    status: str
    strategy: str
    symbols: list[str]
    timeframe: str
    params: dict
    risk_frac: float
    vol_target: float
    max_leverage: float
    created_at: datetime
    metrics: SymbolMetrics | None = None


# ---------- 参数扫描 ----------

SWEEP_MAX_COMBOS = 24


class SweepRequest(BaseModel):
    """参数扫描：基准配置 + 网格维度，笛卡尔积展开为多次回测（上限 SWEEP_MAX_COMBOS）。

    每个网格维度给值列表；留空的维度取单值默认。策略参数网格 param_grid 的键
    必须是该策略的合法参数名，值为候选列表。
    """
    strategy: str = "ema_pullback_v1"
    symbols: list[str] = Field(default=["BTCUSDT"])
    timeframe: str = Field(default="1d", pattern=_TF_PATTERN)
    start: datetime | None = None
    end: datetime | None = None
    fee: float = 0.0005
    slippage: float = 0.0002
    initial_equity: float = Field(default=10_000.0, gt=0)
    max_leverage: float = Field(default=3.0, gt=0, le=10)
    # 网格维度（每个至少 1 个值）
    risk_fracs: list[float] = Field(default=[0.05])
    vol_targets: list[float] = Field(default=[0.25])
    param_grid: dict[str, list[float]] = Field(default_factory=dict)


class SweepJob(BaseModel):
    sweep_id: str
    status: str                     # running | done
    total: int
    done: int
    job_ids: list[str] = Field(default_factory=list)
    created_at: datetime


class LiveOrderRequest(BaseModel):
    symbol: str
    side: str              # long | short | close
    dry_run: bool = True   # 默认演练单，实盘需显式 dry_run=false 且总开关开启


class LiveStatus(BaseModel):
    enabled: bool
    running_executors: int
    open_positions: list[dict]


# ---------- 执行器（策略模板的运行时实例，设计决策 #9） ----------

class ExecutorCreate(BaseModel):
    name: str = ""
    strategy: str = "ema_pullback_v1"
    symbol: str = "BTC/USDT"
    timeframe: str = Field(default="1d", pattern=_TF_PATTERN)
    params: dict = Field(default_factory=dict, description="参数覆盖值，未给定的用策略默认值")
    equity_budget: float = Field(default=1000.0, gt=0, description="账面隔离的资金预算（USDT）")
    risk_frac: float = Field(default=0.05, gt=0, le=0.5)
    vol_target: float = Field(default=0.25, ge=0)
    max_leverage: float = Field(default=3.0, gt=0, le=10)


class ExecutorUpdate(BaseModel):
    name: str | None = None
    params: dict | None = None
    equity_budget: float | None = Field(default=None, gt=0)
    risk_frac: float | None = Field(default=None, gt=0, le=0.5)
    vol_target: float | None = Field(default=None, ge=0)
    max_leverage: float | None = Field(default=None, gt=0, le=10)
    status: str | None = Field(default=None, pattern="^(running|paused)$")


class ExecutorOut(BaseModel):
    id: str
    name: str
    strategy_name: str
    symbol: str
    timeframe: str
    params: dict
    equity_budget: float
    risk_frac: float
    vol_target: float
    max_leverage: float
    status: str
    state: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ExecutorSignal(BaseModel):
    """执行器当前 dry-run 信号快照：基于最近一根已收盘 K 线。"""
    executor_id: str
    symbol: str
    timeframe: str
    as_of: datetime              # 最近一根已收盘 K 线的开盘时间
    position: dict | None = None
    entry_signal: int            # 1 多 / -1 空 / 0 无
    exit_signal: bool
    suggested_qty: float = 0.0
    suggested_stop: float | None = None
    close: float | None = None          # 最近已收盘 bar 收盘价（重锚止损的锚点）
    atr: float | None = None
    slope: float | None = None
    dry_run: bool = True
    note: str = ""
