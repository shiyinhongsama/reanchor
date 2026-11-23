"""K 线周期支持表（设计决策 #8）。

timeframe -> (ccxt 拉取周期, 年化 bar 数)
拉取周期与 timeframe 不同表示需要在适配层重采样（如 2d 由 1d 合成）。
策略年化指标统一用这里的 bars_per_year，周期切换时不改策略代码。
"""
from __future__ import annotations

TIMEFRAMES: dict[str, tuple[str, float]] = {
    "1m":  ("1m", 525_600.0),
    "5m":  ("5m", 105_120.0),
    "15m": ("15m", 35_040.0),
    "30m": ("30m", 17_520.0),
    "1h":  ("1h", 8_760.0),
    "2h":  ("2h", 4_380.0),
    "4h":  ("4h", 2_190.0),
    "6h":  ("6h", 1_460.0),
    "8h":  ("8h", 1_095.0),
    "12h": ("12h", 730.0),
    "1d":  ("1d", 365.0),
    "2d":  ("1d", 182.5),      # Gate.io 无原生 2d，适配层内重采样
    "1w":  ("1w", 52.0),
}

SUPPORTED: tuple[str, ...] = tuple(TIMEFRAMES)


def timeframe_ms(ccxt_tf: str) -> int:
    """'15m'/'4h'/'1d'/'1w' -> 毫秒。"""
    unit = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
    return int(ccxt_tf[:-1]) * unit[ccxt_tf[-1]]
