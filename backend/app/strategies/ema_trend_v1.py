"""EMA 趋势版 v1 —— 斜率过零入场的纯趋势跟踪（研究 ema_slope_trend.py 的移植）。

入场（多）：斜率由 <=0 转为 >0（上穿零轴）→ 下一根开盘做多；空镜像
出场：    斜率反向过零 → 下一根开盘离场（随后若再反向过零可反手）
止损：    3×ATR 重锚（domain.sizing.ReanchorStop，由引擎执行）
          平台适配说明：研究版无 ATR 止损、仅靠斜率反转出场；
          平台保留重锚止损作为灾难保护，atr_mult 调大即可逼近研究版行为。

参数：ema_n=20, slope_k=5, atr_n=14, atr_mult=3.0, reanchor_min_bars=1, ma_type=ema
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..domain.strategy import BarContext, StrategyParams
from .ma import MA_TYPES, ma_series


class EmaTrendV1:
    """EMA 趋势版 v1：斜率过零入场，斜率反转出场，3×ATR 重锚灾难止损。"""

    name = "ema_trend_v1"

    params_schema = {
        "ema_n":    {"type": "int",   "default": 20,  "min": 5,   "max": 200},
        "slope_k":  {"type": "int",   "default": 5,   "min": 2,   "max": 50},
        "atr_n":    {"type": "int",   "default": 14,  "min": 5,   "max": 50},
        "atr_mult": {"type": "float", "default": 3.0, "min": 1.0, "max": 8.0},
        "reanchor_min_bars": {"type": "int", "default": 1, "min": 1, "max": 10},
        "ma_type":  {"type": "str",   "default": "ema", "options": list(MA_TYPES)},
    }

    def prepare(self, bars: pd.DataFrame, params: StrategyParams) -> pd.DataFrame:
        ema_n = params.get("ema_n", 20)
        slope_k = params.get("slope_k", 5)
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
        """斜率过零：信号在 t-1（已收盘）上判定。"""
        s = t - 1
        if s < 1:
            return 0
        cur, prev = bars.iloc[s].slope, bars.iloc[s - 1].slope
        if np.isnan(cur) or np.isnan(prev):
            return 0
        if prev <= 0 < cur:
            return 1
        if prev >= 0 > cur:
            return -1
        return 0

    def exit_signal(self, bars: pd.DataFrame, t: int, pos_side: int, params: StrategyParams) -> bool:
        """斜率反向过零出场。"""
        s = t - 1
        if s < 1:
            return False
        cur, prev = bars.iloc[s].slope, bars.iloc[s - 1].slope
        if np.isnan(cur) or np.isnan(prev):
            return False
        if pos_side > 0:
            return bool(prev >= 0 > cur)
        return bool(prev <= 0 < cur)

    def explain(self, bars: pd.DataFrame, t: int, params: StrategyParams) -> dict:
        """信号解释：斜率位置 + 是否过零。"""
        s = t - 1
        if s < 1:
            return {"reason": "数据不足"}
        row, prev_row = bars.iloc[s], bars.iloc[s - 1]
        if np.isnan(row.slope) or np.isnan(prev_row.slope) or np.isnan(row.atr):
            return {"reason": "指标预热中"}
        cur, prev = float(row.slope), float(prev_row.slope)
        long_chk = {"slope_up": cur > 0, "crossed": prev <= 0}
        short_chk = {"slope_down": cur < 0, "crossed": prev >= 0}
        out = {
            "ema": round(float(row.ema), 2), "atr": round(float(row.atr), 2),
            "slope": round(cur, 4), "slope_prev": round(prev, 4),
            "close": round(float(row.close), 2),
            "dist_atr": round((float(row.close) - float(row.ema)) / float(row.atr), 2)
                        if row.atr else None,
            "long": long_chk, "short": short_chk,
        }
        if all(long_chk.values()):
            out["reason"] = "多头入场成立：斜率上穿零轴"
        elif all(short_chk.values()):
            out["reason"] = "空头入场成立：斜率下穿零轴"
        elif cur > 0:
            out["reason"] = f"未入场：斜率向上但未过零（前值 {prev:.1f} 已为正）"
        else:
            out["reason"] = f"未入场：斜率向下但未过零（前值 {prev:.1f} 已为负）"
        return out

    def on_bar(self, ctx: BarContext) -> None:  # noqa: ARG002 - 预留实盘钩子
        return None
