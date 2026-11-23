"""仓位与止损管理（与验证研究 v1 规则逐条对应）。

风险分数仓位：  qty = equity × risk_frac × vt_scalar / (3 × ATR)
杠杆上限：      qty ≤ equity × max_leverage / price
VT 缩放：       scalar = clamp(vol_target / σ_rolling, floor, cap)
v1 重锚止损：   斜率与持仓同向时，每日以当日收盘重锚 3×ATR，不低于初始止损
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class SizingConfig:
    risk_frac: float = 0.05          # 单笔风险占权益比例
    max_leverage: float = 3.0
    vol_target: float = 0.25         # 年化波动目标；<=0 表示关闭 VT
    vol_lookback: int = 20
    vol_cap: float = 2.5
    vol_floor: float = 0.25
    bars_per_year: float = 365.0     # 日线=365；12h=730


def vt_scalar(equity_returns: list[float], cfg: SizingConfig) -> float:
    """根据滚动权益收益的年化波动计算 VT 缩放系数。"""
    if cfg.vol_target <= 0 or len(equity_returns) < cfg.vol_lookback:
        return 1.0
    sigma = float(np.std(equity_returns[-cfg.vol_lookback:]) * np.sqrt(cfg.bars_per_year))
    if sigma <= 0:
        return 1.0
    return float(min(max(cfg.vol_target / sigma, cfg.vol_floor), cfg.vol_cap))


def position_size(equity: float, entry_price: float, stop_distance: float,
                  scalar: float, cfg: SizingConfig) -> float:
    """风险分数仓位公式；返回基础货币数量。返回 0 表示不开仓。"""
    if stop_distance <= 0 or entry_price <= 0 or equity <= 0:
        return 0.0
    qty_by_risk = equity * cfg.risk_frac * scalar / stop_distance
    qty_by_leverage = equity * cfg.max_leverage / entry_price
    return float(min(qty_by_risk, qty_by_leverage))


class ReanchorStop:
    """重锚止损（对应研究 v1/v2 两个变体）。

    min_aligned=1（默认，研究 v1）：每根收盘以 收盘 ∓ mult×ATR 重新计算，
        多头 max(初始止损, close−dist)、空头 min(初始止损, close+dist)。
        注意这是「重锚」而非棘轮：价格回撤后止损会随新锚点回落。
    min_aligned>1（研究 v2）：斜率与持仓同向连续满 min_aligned 根前，峰值跟踪棘轮；
        满 N 根后多头取 收盘锚/峰值锚 较紧者 max(stop, close−dist, peak−dist)，
        空头取 min(stop, close+dist)（不对称，与研究脚本行为一致）。全程棘轮，只进不退。
    """

    def __init__(self, side: int, entry_stop: float, atr_mult: float = 3.0,
                 min_aligned: int = 1):
        self.side = side                # 1 多 / -1 空
        self.entry_stop = entry_stop
        self.atr_mult = atr_mult
        self.min_aligned = max(1, int(min_aligned))
        self.current = entry_stop
        self.peak: float | None = None  # v2 峰值跟踪用
        self.aligned_bars = 0           # v2 连续同向计数

    def update(self, close: float, atr: float, aligned: bool = True) -> float:
        """aligned：当根斜率是否与持仓同向（v1 模式下忽略）。"""
        dist = self.atr_mult * atr
        if self.min_aligned <= 1:
            # v1：每根以当前收盘重锚
            if self.side > 0:
                self.current = max(self.entry_stop, close - dist)
            else:
                self.current = min(self.entry_stop, close + dist)
            return self.current
        # v2：连续同向满 N 根 → 棘轮锚定；否则峰值跟踪棘轮（全程只进不退）
        # 注意多头/空头不对称，这是研究脚本的原样行为：
        #   多头满 N 根后同时取 收盘锚 与 峰值锚 中较紧者；空头只取收盘锚。
        self.aligned_bars = self.aligned_bars + 1 if aligned else 0
        if self.peak is None:
            self.peak = close
        if self.side > 0:
            self.peak = max(self.peak, close)
            if self.aligned_bars >= self.min_aligned:
                self.current = max(self.entry_stop, self.current,
                                   close - dist, self.peak - dist)
            else:
                self.current = max(self.current, self.peak - dist)
        else:
            self.peak = min(self.peak, close)
            if self.aligned_bars >= self.min_aligned:
                self.current = min(self.entry_stop, self.current, close + dist)
            else:
                self.current = min(self.current, self.peak + dist)
        return self.current

    def hit(self, high: float, low: float) -> bool:
        return low <= self.current if self.side > 0 else high >= self.current
