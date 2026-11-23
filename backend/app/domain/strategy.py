"""策略领域接口。

约定（防前视纪律，与研究回测引擎一致）：
- 策略在「已收盘 K 线」上产生信号，引擎在「下一根 K 线开盘价」成交；
- prepare() 一次性预计算指标（向量化，仅使用历史数据）；
- entry_signal / exit_signal 在 bar t 被调用时，只能读取 df.iloc[:t] 的数据，
  引擎保证传入的 t 是执行 bar，信号判断用 t-1。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd


@dataclass
class StrategyParams:
    """策略参数容器；所有可调参数集中于此，便于平台扫描与配置化。"""
    values: dict = field(default_factory=dict)

    def get(self, key, default=None):
        return self.values.get(key, default)


class Strategy(Protocol):
    """策略协议：任何新策略实现这四个方法即可接入回测与实盘。"""

    name: str

    def prepare(self, bars: pd.DataFrame, params: StrategyParams) -> pd.DataFrame:
        """输入 OHLCV DataFrame（open_time/open/high/low/close/volume），
        返回附带指标列的 DataFrame。只允许使用历史数据（滚动/ewm 需无未来函数）。"""
        ...

    def entry_signal(self, bars: pd.DataFrame, t: int, params: StrategyParams) -> int:
        """在 bar t（执行 bar）上返回方向：1 多 / -1 空 / 0 无信号。
        实现中只能读取 t-1 及之前的数据。"""
        ...

    def exit_signal(self, bars: pd.DataFrame, t: int, pos_side: int, params: StrategyParams) -> bool:
        """持仓中调用：是否信号出场（下一 bar 开盘执行）。"""
        ...

    def on_bar(self, ctx: "BarContext") -> None:
        """实盘模式下每根新收盘 K 线的回调（可选，用于状态跟踪/日志）。"""
        ...


@dataclass
class BarContext:
    symbol: str
    bar: pd.Series
    position_side: int          # 0 无仓 / 1 多 / -1 空
    position_qty: float
    entry_stop: float | None
    current_stop: float | None
