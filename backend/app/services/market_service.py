"""大盘总览：Gate 永续 ticker 聚合 + 恐慌贪婪指数（alternative.me），60s TTL 缓存。

数据源全部为公开接口，无需 API key；任一外部源失败时对应字段降级为 None，
不阻塞其余部分（大盘页是信息面板，不是交易链路）。
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

import httpx

from ..core.config import get_settings
from ..infrastructure.exchange.ccxt_gate import CcxtGateAdapter

_TTL = 60.0
_HEATMAP_TOP = 24          # 热力图瓦片数
_BREADTH_TOP = 100         # 涨跌家数统计范围（按成交额）

# 大盘口径 = 加密资产。Gate 永续里混有 TradFi/代币化股票（XAU/XAG/SOXL/SNDK…），
# 用主流加密资产清单过滤；清单外的长尾合约不进热力图和涨跌家数
CRYPTO_UNIVERSE = {
    "BTC", "ETH", "SOL", "DOGE", "XRP", "BNB", "ADA", "LINK", "AVAX", "TON",
    "TRX", "DOT", "LTC", "BCH", "NEAR", "UNI", "SUI", "APT", "FIL", "ATOM",
    "OP", "ARB", "INJ", "TIA", "SEI", "WIF", "PEPE", "SHIB", "FET", "RENDER",
    "ETC", "AAVE", "HBAR", "VET", "ALGO", "SAND", "AXS", "EOS", "XLM", "HYPE",
    "ONDO", "JUP", "WLD", "PUMP", "ENA", "PENDLE", "CRV", "LDO", "S", "TAO",
}

_cache: dict[str, Any] = {"t": 0.0, "data": None}


def parse_tickers(raw: list[dict]) -> list[dict]:
    """Gate futures ticker -> 统一行：symbol/last/change_pct/volume_usd/high/low。

    过滤：非 USDT 结算、成交额缺失或为 0、最新价非法。change_percentage 为 24h 百分比。
    """
    out = []
    for r in raw:
        try:
            contract = str(r.get("contract", ""))
            if not contract.endswith("_USDT"):
                continue
            symbol = contract[: -len("_USDT")]
            if symbol not in CRYPTO_UNIVERSE:
                continue
            vol = float(r.get("volume_24h_usd") or r.get("volume_24h_settle") or 0)
            last = float(r.get("last") or 0)
            if vol <= 0 or last <= 0:
                continue
            out.append({
                "symbol": symbol,
                "last": last,
                "change_pct": float(r.get("change_percentage") or 0),
                "volume_usd": vol,
                "high_24h": float(r.get("high_24h") or 0),
                "low_24h": float(r.get("low_24h") or 0),
            })
        except (TypeError, ValueError):
            continue            # 单行脏数据不影响整体
    out.sort(key=lambda x: x["volume_usd"], reverse=True)
    return out


def breadth(rows: list[dict]) -> dict:
    """涨跌家数 / 平均涨跌 / 总成交（按成交额前 N 名统计，避免长尾噪声）。"""
    top = rows[:_BREADTH_TOP]
    if not top:
        return {"advancers": 0, "decliners": 0, "flat": 0,
                "avg_change_pct": None, "total_volume_usd": 0.0}
    adv = sum(1 for r in top if r["change_pct"] > 0.05)
    dec = sum(1 for r in top if r["change_pct"] < -0.05)
    return {
        "advancers": adv,
        "decliners": dec,
        "flat": len(top) - adv - dec,
        "avg_change_pct": sum(r["change_pct"] for r in top) / len(top),
        "total_volume_usd": sum(r["volume_usd"] for r in rows),
    }


async def _fetch_tickers() -> list[dict]:
    settings = get_settings()
    adapter = CcxtGateAdapter(settings.gate_api_key, settings.gate_api_secret,
                              settings.gate_sandbox)
    try:
        import asyncio
        for attempt in range(3):            # 全量 ticker payload 大，直连偶发超时，退避重试
            try:
                raw = await adapter._ex.publicFuturesGetSettleTickers({"settle": "usdt"})
                return list(raw)
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 * (attempt + 1))
    finally:
        await adapter.close()


async def _fetch_fng() -> dict | None:
    """恐慌贪婪指数；失败返回 None（前端显示"暂不可用"）。"""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get("https://api.alternative.me/fng/", params={"limit": 2})
            data = r.json().get("data") or []
            if not data:
                return None
            cur = data[0]
            out = {"value": int(cur["value"]), "label": cur["value_classification"]}
            if len(data) > 1:
                out["prev_value"] = int(data[1]["value"])
            return out
    except Exception:           # noqa: BLE001 - 外部源失败不阻塞大盘页
        return None


async def get_overview(
    tickers_fetcher: Callable[[], Awaitable[list[dict]]] = _fetch_tickers,
    fng_fetcher: Callable[[], Awaitable[dict | None]] = _fetch_fng,
    force: bool = False,
) -> dict:
    """大盘总览（带 60s TTL 缓存；fetcher 可注入以便测试）。"""
    now = time.monotonic()
    if not force and _cache["data"] is not None and now - _cache["t"] < _TTL:
        return _cache["data"]

    rows = parse_tickers(await tickers_fetcher())
    fng = await fng_fetcher()
    data = {
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fng": fng,
        "breadth": breadth(rows),
        "heatmap": rows[:_HEATMAP_TOP],
    }
    _cache.update({"t": now, "data": data})
    return data
