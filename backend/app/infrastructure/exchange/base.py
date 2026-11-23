"""交易所适配层统一接口（设计决策 #7）。

平台自定义最小接口，底层实现可替换（一期：ccxt + Gate.io）。
domain/services 只依赖本协议，永远看不到 ccxt 的字段差异。
"""
from __future__ import annotations

from typing import Protocol

import pandas as pd


class ExchangeAdapter(Protocol):
    """统一交易所接口：行情 + 下单 + 持仓/权益。"""

    name: str

    async def klines(self, symbol: str, timeframe: str,
                     since_ms: int | None = None, until_ms: int | None = None) -> pd.DataFrame:
        """返回目标周期 OHLCV：open_time(UTC)/open/high/low/close/volume。
        非原生周期（如 2d）由实现内部重采样，调用方无感。"""
        ...

    async def place_order(self, symbol: str, side: str, qty: float, *,
                          dry_run: bool = True) -> dict:
        """side: long | short | close。dry_run=True 只模拟不落单。"""
        ...

    async def set_stop(self, symbol: str, stop_px: float, *,
                       dry_run: bool = True) -> dict:
        """挂/改止损委托。"""
        ...

    async def get_position(self, symbol: str) -> dict | None:
        """当前持仓 {side, qty, entry_px}；无持仓返回 None。需 API key。"""
        ...

    async def get_equity(self) -> float:
        """合约账户 USDT 权益。需 API key。"""
        ...

    async def close(self) -> None:
        """释放底层连接。"""
        ...
