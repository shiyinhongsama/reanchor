"""EMA 回调版策略 v1 —— 研究验证通过的首个内置策略。

入场（多）：前收 > EMA20；当根最低 ≤ EMA20（回踩）；当根收 > EMA20（收回）；斜率 > 0
入场（空）：镜像
出场：    斜率翻转（EMA20[t] - EMA20[t-8] 变号）
止损：    3×ATR14 重锚（domain.sizing.ReanchorStop，由引擎执行；
          reanchor_min_bars=1 为研究 v1 每根重锚，>=2 为 v2 同向满 N 根才重锚）

参数：ema_n=20, slope_k=8, atr_n=14, atr_mult=3.0, reanchor_min_bars=1
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..domain.strategy import BarContext, StrategyParams


class EmaPullbackV1:
    """EMA 回调版 v1：回踩 EMA20 收回入场（顺斜率方向），斜率翻转出场，3×ATR 重锚止损。"""

    name = "ema_pullback_v1"

    # 参数声明：执行器创建时按此校验（类型/范围），未传参数用 default
    params_schema = {
        "ema_n":    {"type": "int",   "default": 20,  "min": 5,   "max": 200},
        "slope_k":  {"type": "int",   "default": 8,   "min": 2,   "max": 50},
        "atr_n":    {"type": "int",   "default": 14,  "min": 5,   "max": 50},
        "atr_mult": {"type": "float", "default": 3.0, "min": 1.0, "max": 8.0},
        # 重锚模式：1=研究 v1 每根收盘重锚；>=2=研究 v2 斜率同向满 N 根才重锚（其间峰值跟踪）
        "reanchor_min_bars": {"type": "int", "default": 1, "min": 1, "max": 10},
    }

    def prepare(self, bars: pd.DataFrame, params: StrategyParams) -> pd.DataFrame:
        ema_n = params.get("ema_n", 20)
        slope_k = params.get("slope_k", 8)
        atr_n = params.get("atr_n", 14)
        d = bars.copy()
        c = d["close"]
        d["ema"] = c.ewm(span=ema_n, adjust=False).mean()
        d["slope"] = d["ema"] - d["ema"].shift(slope_k)
        tr = pd.concat([d["high"] - d["low"],
                        (d["high"] - c.shift(1)).abs(),
                        (d["low"] - c.shift(1)).abs()], axis=1).max(axis=1)
        d["atr"] = tr.ewm(alpha=1 / atr_n, adjust=False).mean()
        return d

    def entry_signal(self, bars: pd.DataFrame, t: int, params: StrategyParams) -> int:
        """信号在 t-1（已收盘）上判定。"""
        s = t - 1
        if s < 1:
            return 0
        row, prev = bars.iloc[s], bars.iloc[s - 1]
        if np.isnan(row.ema) or np.isnan(row.slope):
            return 0
        if prev.close > prev.ema and row.low <= row.ema and row.close > row.ema and row.slope > 0:
            return 1
        if prev.close < prev.ema and row.high >= row.ema and row.close < row.ema and row.slope < 0:
            return -1
        return 0

    def exit_signal(self, bars: pd.DataFrame, t: int, pos_side: int, params: StrategyParams) -> bool:
        s = t - 1
        if s < 0:
            return False
        slope = bars.iloc[s].slope
        if np.isnan(slope):
            return False
        return (pos_side > 0 and slope < 0) or (pos_side < 0 and slope > 0)

    def on_bar(self, ctx: BarContext) -> None:  # noqa: ARG002 - 预留实盘钩子
        return None
