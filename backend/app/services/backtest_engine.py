"""回测引擎：事件驱动、逐 bar 推进，与验证研究的执行语义完全一致。

执行纪律：
1. 信号用 bar t-1（已收盘），成交在 bar t 开盘价（±滑点）；
2. 盘中止损用当根 high/low 判定（保守假设：止损单成交价 = min(open, stop)±滑点）；
3. 成本：手续费（双边）+ 滑点；资金费率可选；
4. v1 重锚止损由 ReanchorStop 执行。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..domain.sizing import ReanchorStop, SizingConfig, position_size, vt_scalar
from ..domain.strategy import Strategy, StrategyParams


@dataclass
class CostModel:
    fee: float = 0.0005
    slippage: float = 0.0002
    funding_per_8h: float = 0.0        # >0 时按持仓名义价值每 8h 扣减


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: pd.DataFrame
    metrics: dict = field(default_factory=dict)


class BacktestEngine:
    def __init__(self, strategy: Strategy, params: StrategyParams,
                 sizing: SizingConfig, cost: CostModel,
                 initial_equity: float = 10_000.0, warmup: int = 60):
        self.strategy = strategy
        self.params = params
        self.sizing = sizing
        self.cost = cost
        self.initial_equity = initial_equity
        self.warmup = warmup

    def run(self, raw_bars: pd.DataFrame) -> BacktestResult:
        d = self.strategy.prepare(raw_bars, self.params)
        n = len(d)
        atr_mult = self.params.get("atr_mult", 3.0)
        self._min_aligned = int(self.params.get("reanchor_min_bars", 1))

        equity = self.initial_equity
        pos, entry_px = 0.0, 0.0
        stop: ReanchorStop | None = None
        eq_curve = np.full(n, np.nan)
        ret_hist: list[float] = []
        trades: list[dict] = []
        cur: dict = {}

        def record(i: int, px: float, reason: str) -> None:
            nonlocal equity, pos, stop
            pnl = pos * (px - entry_px) - abs(pos) * px * self.cost.fee
            equity += pnl
            cur.update({"exit_time": d.open_time.iloc[i], "exit_px": px,
                        "reason": reason, "pnl": pnl, "equity_after": equity})
            trades.append(cur)
            pos, stop = 0.0, None

        for t in range(self.warmup, n):
            row = d.iloc[t]
            o, h, l, cl = row.open, row.high, row.low, row.close
            if equity <= 0:
                break

            # 1) 信号出场（昨日信号 → 今日开盘执行）
            if pos != 0 and self.strategy.exit_signal(d, t, int(np.sign(pos)), self.params):
                px = o * (1 - self.cost.slippage) if pos > 0 else o * (1 + self.cost.slippage)
                record(t, px, "signal_exit")

            # 2) 空仓开新仓
            if pos == 0 and equity > 0:
                direction = self.strategy.entry_signal(d, t, self.params)
                if direction != 0 and not np.isnan(d.atr.iloc[t - 1]):
                    dist = atr_mult * d.atr.iloc[t - 1]
                    fill = o * (1 + self.cost.slippage) if direction > 0 else o * (1 - self.cost.slippage)
                    scalar = vt_scalar(ret_hist, self.sizing)
                    qty = position_size(equity, fill, dist, scalar, self.sizing)
                    if qty > 0:
                        equity -= qty * fill * self.cost.fee
                        pos, entry_px = qty * direction, fill
                        init_stop = (cl - dist) if direction > 0 else (cl + dist)
                        stop = ReanchorStop(direction, init_stop, atr_mult,
                                            self._min_aligned)
                        stop.aligned_bars = 1     # 入场信号本身要求斜率同向，与研究脚本对齐
                        cur = {"entry_time": row.open_time,
                               "side": "long" if direction > 0 else "short",
                               "entry_px": fill, "qty": qty, "vt": scalar,
                               "entry_stop": init_stop}

            # 3) 盘中止损
            if pos != 0 and stop is not None and stop.hit(h, l):
                px = (min(o, stop.current) * (1 - self.cost.slippage)) if pos > 0 \
                    else (max(o, stop.current) * (1 + self.cost.slippage))
                record(t, px, "atr_stop")

            # 4) 收盘后重锚止损（v2 模式下按斜率同向计数门控）
            if pos != 0 and stop is not None:
                aligned = True
                if self._min_aligned > 1 and "slope" in d.columns:
                    sl = d.slope.iloc[t]
                    aligned = bool(not np.isnan(sl)) and ((pos > 0 and sl > 0) or (pos < 0 and sl < 0))
                stop.update(cl, d.atr.iloc[t], aligned)

            # 5) 资金费（可选）
            if pos != 0 and self.cost.funding_per_8h > 0 and row.open_time.hour % 8 == 0:
                equity -= pos * cl * self.cost.funding_per_8h

            eq_curve[t] = equity + pos * (cl - entry_px) if pos != 0 else equity
            if t > self.warmup and not np.isnan(eq_curve[t - 1]) and eq_curve[t - 1] > 0:
                ret_hist.append(eq_curve[t] / eq_curve[t - 1] - 1)

        # 期末强平
        if pos != 0:
            record(n - 1, d.close.iloc[-1] * (1 - self.cost.slippage if pos > 0 else 1 + self.cost.slippage), "end")
            eq_curve[n - 1] = equity

        eq = pd.Series(eq_curve, index=d["open_time"]).ffill().dropna()
        td = pd.DataFrame(trades)
        metrics = self._metrics(eq, td)
        return BacktestResult(equity=eq, trades=td, metrics=metrics)

    def _metrics(self, eq: pd.Series, td: pd.DataFrame) -> dict:
        if len(eq) < 2:
            return {}
        years = (eq.index[-1] - eq.index[0]).days / 365.25
        ann = (eq.iloc[-1] / eq.iloc[0]) ** (1 / max(years, 1e-9)) - 1
        rets = eq.pct_change().dropna()
        sharpe = float(rets.mean() / rets.std() * np.sqrt(self.sizing.bars_per_year)) if rets.std() > 0 else 0.0
        mdd = float((eq / eq.cummax() - 1).min())
        out = {"total_return": float(eq.iloc[-1] / eq.iloc[0] - 1),
               "annualized": float(ann), "sharpe": sharpe, "max_drawdown": mdd,
               "calmar": float(ann / abs(mdd)) if mdd < 0 else None,
               "trades": int(len(td))}
        if len(td) and (td.pnl <= 0).any():
            out["profit_factor"] = float(td.loc[td.pnl > 0, "pnl"].sum() / abs(td.loc[td.pnl <= 0, "pnl"].sum()))
        return out
