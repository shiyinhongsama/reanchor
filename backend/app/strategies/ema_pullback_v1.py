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
from .ma import MA_TYPES, ma_series


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
        # 均线类型：研究阶段均线族对比（ema/sma/wma/dema/tema），默认 ema 与研究基线一致
        "ma_type": {"type": "str", "default": "ema", "options": list(MA_TYPES)},
    }

    def prepare(self, bars: pd.DataFrame, params: StrategyParams) -> pd.DataFrame:
        ema_n = params.get("ema_n", 20)
        slope_k = params.get("slope_k", 8)
        atr_n = params.get("atr_n", 14)
        d = bars.copy()
        c = d["close"]
        d["ema"] = ma_series(c, ema_n, params.get("ma_type", "ema"))
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

    def explain(self, bars: pd.DataFrame, t: int, params: StrategyParams) -> dict:
        """信号解释（信号解释卡用）：入场条件逐项检查 + 人类可读结论。

        与 entry_signal 同一口径，只读 t-1 及之前的已收盘数据。
        """
        s = t - 1
        if s < 1:
            return {"reason": "数据不足"}
        row, prev = bars.iloc[s], bars.iloc[s - 1]
        if np.isnan(row.ema) or np.isnan(row.atr) or np.isnan(row.slope):
            return {"reason": "指标预热中"}
        ema, atr, slope = float(row.ema), float(row.atr), float(row.slope)
        close = float(row.close)
        dist_atr = (close - ema) / atr if atr else None
        long_chk = {
            "prev_above": bool(prev.close > prev.ema),   # 前收站上 EMA
            "touched": bool(row.low <= row.ema),          # 当根回踩 EMA
            "reclaimed": bool(row.close > row.ema),       # 收回 EMA 上方
            "slope_ok": bool(slope > 0),                  # 斜率向上
        }
        short_chk = {
            "prev_below": bool(prev.close < prev.ema),   # 前收跌破 EMA
            "touched": bool(row.high >= row.ema),         # 当根反抽 EMA
            "rejected": bool(row.close < row.ema),        # 压回 EMA 下方
            "slope_ok": bool(slope < 0),                  # 斜率向下
        }
        out = {
            "ema": round(ema, 2), "atr": round(atr, 2),
            "slope": round(slope, 4), "close": round(close, 2),
            "dist_atr": round(dist_atr, 2) if dist_atr is not None else None,
            "long": long_chk, "short": short_chk,
        }
        if all(long_chk.values()):
            out["reason"] = "多头入场成立：回踩 EMA 收回，斜率向上"
        elif all(short_chk.values()):
            out["reason"] = "空头入场成立：反抽 EMA 压回，斜率向下"
        else:
            # 沿斜率方向报告第一个未满足的条件
            chk = long_chk if slope > 0 else short_chk
            msgs = {
                "prev_above": "前收未站上 EMA", "prev_below": "前收未跌破 EMA",
                "touched": f"未触及 EMA（距 {dist_atr:+.1f} ATR）" if dist_atr is not None else "未触及 EMA",
                "reclaimed": "回踩未收回 EMA 上方", "rejected": "反抽未压回 EMA 下方",
                "slope_ok": "斜率方向不符",
            }
            fail = next(k for k in chk if not chk[k])
            out["reason"] = f"未入场：{msgs[fail]}"
        return out

    def on_bar(self, ctx: BarContext) -> None:  # noqa: ARG002 - 预留实盘钩子
        return None
