"""Gate.io 适配器：ccxt.async_support 实现统一接口（设计决策 #7/8）。

- 行情走公共接口，无需 API key；
- 合约固定 defaultType="swap"（USDT-M 永续）；
- 非原生周期（2d）在适配层内重采样，调用方拿到的永远是目标周期；
- 真实下单刻意保留 NotImplementedError：待实网联调 + TP_LIVE_ENABLED 后开放。
"""
from __future__ import annotations

import asyncio
import time

import ccxt.async_support as ccxt
import pandas as pd

from ...domain.timeframes import TIMEFRAMES, timeframe_ms


def normalize_symbol(symbol: str) -> str:
    """'BTCUSDT' / 'BTC/USDT' -> 'BTC/USDT:USDT'（ccxt 永续统一格式）。"""
    s = symbol.upper().replace("_", "/")
    if ":" in s:
        return s
    if "/" not in s:
        for q in ("USDT", "USDC"):
            if s.endswith(q):
                s = s[: -len(q)] + "/" + q
                break
    base, _, quote = s.partition("/")
    return f"{base}/{quote}:{quote}"


def gate_contract(symbol: str) -> str:
    """'BTC/USDT:USDT' -> 'BTC_USDT'（Gate 裸接口合约名）。"""
    return normalize_symbol(symbol).split(":")[0].replace("/", "_")


class CcxtGateAdapter:
    name = "gate"
    _COLS = ["open_time", "open", "high", "low", "close", "volume"]

    def __init__(self, api_key: str = "", api_secret: str = "", sandbox: bool = False):
        self.api_key = api_key
        self._ex = ccxt.gate({
            "apiKey": api_key,
            "secret": api_secret,
            "options": {"defaultType": "swap"},
            "enableRateLimit": True,
            "timeout": 30_000,          # 国内直连 gate 延迟偏高，默认 10s 容易超时
        })
        if sandbox:
            self._ex.set_sandbox_mode(True)

    async def klines(self, symbol: str, timeframe: str,
                     since_ms: int | None = None, until_ms: int | None = None) -> pd.DataFrame:
        """直接调 Gate 裸接口拿 K 线（绕过 ccxt load_markets——它要拉全市场合约清单，
        国内直连经常超时；裸 candlesticks 接口轻量且无需鉴权）。"""
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"不支持的周期 {timeframe}，可选：{list(TIMEFRAMES)}")
        fetch_tf, _ = TIMEFRAMES[timeframe]
        contract = gate_contract(symbol)
        tf_s = timeframe_ms(fetch_tf) // 1000
        until_s = int(until_ms // 1000) if until_ms else int(time.time())
        since_s = int(since_ms // 1000) if since_ms is not None else until_s - 1000 * tf_s

        rows: list[dict] = []
        cursor = since_s
        while cursor < until_s:
            params = {"settle": "usdt", "contract": contract, "interval": fetch_tf,
                      "from": cursor, "to": until_s}
            batch = None
            for attempt in range(3):            # 国内直连偶发超时，退避重试
                try:
                    batch = await self._ex.publicFuturesGetSettleCandlesticks(params)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2 * (attempt + 1))
            if not batch:
                break
            rows.extend(batch)
            last_t = max(int(r["t"]) for r in batch)
            if last_t <= cursor:
                break
            cursor = last_t + tf_s
            if len(batch) < 2000:      # Gate 单页上限 2000 根
                break

        if not rows:
            return pd.DataFrame(columns=self._COLS)
        df = pd.DataFrame([{
            "ts": int(r["t"]) * 1000, "open": float(r["o"]), "high": float(r["h"]),
            "low": float(r["l"]), "close": float(r["c"]), "volume": float(r.get("v") or 0),
        } for r in rows])
        df = df.drop_duplicates(subset="ts").sort_values("ts")
        df["open_time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df = df[self._COLS].reset_index(drop=True)

        if fetch_tf != timeframe:  # 非原生周期：适配层内重采样（2d）
            df = (df.set_index("open_time")
                  .resample(timeframe.upper(), label="right", closed="right")
                  .agg({"open": "first", "high": "max", "low": "min",
                        "close": "last", "volume": "sum"})
                  .dropna().reset_index())
        return df

    async def place_order(self, symbol: str, side: str, qty: float, *,
                          dry_run: bool = True) -> dict:
        if dry_run:
            return {"symbol": normalize_symbol(symbol), "side": side, "qty": qty,
                    "dry_run": True, "status": "simulated"}
        if not self.api_key:
            raise PermissionError("未配置 Gate.io API key，仅支持 dry_run")
        raise NotImplementedError("真实下单待实网联调后开放（需 TP_LIVE_ENABLED=true）")

    async def set_stop(self, symbol: str, stop_px: float, *,
                       dry_run: bool = True) -> dict:
        if dry_run:
            return {"symbol": normalize_symbol(symbol), "stop_px": stop_px,
                    "dry_run": True, "status": "simulated"}
        raise NotImplementedError("真实止损委托待实网联调后开放")

    async def get_position(self, symbol: str) -> dict | None:
        pos = await self._ex.fetch_position(normalize_symbol(symbol))
        qty = float(pos.get("contracts") or 0)
        if qty == 0:
            return None
        return {"side": 1 if pos.get("side") == "long" else -1,
                "qty": qty, "entry_px": float(pos.get("entryPrice") or 0)}

    async def get_equity(self) -> float:
        bal = await self._ex.fetch_balance()
        return float(bal.get("USDT", {}).get("total") or 0.0)

    async def close(self) -> None:
        await self._ex.close()
