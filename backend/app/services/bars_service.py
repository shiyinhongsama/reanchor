"""K 线缓存服务：已收盘 bar 落 SQLite，各消费方共享一份行情。

策略：
- 命中缓存的历史段直接读库；只向交易所补拉「缓存尾部之后」的增量；
- 增量里只有已收盘 bar 入库（进行中的 bar 返回但不缓存）；
- 尾部一根已缓存 bar 会被重拉校验（交易所可能复权/修正），保证数据收敛；
- 不处理区间内部的空洞（Gate 主流品种连续，一期可接受，见设计文档补充）。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..domain.timeframes import timeframe_ms
from ..infrastructure.models import BarsCacheModel

# 缓存尾部回拉校验的根数：覆盖最近可能未闭合/被修正的 bar
_TAIL_RECHECK_BARS = 2


def _ts(dt) -> pd.Timestamp:
    """统一成 UTC 时区感知时间戳（SQLite 读回的时间可能丢失 tzinfo）。"""
    ts = pd.Timestamp(dt)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts


def _ms(dt: datetime) -> int:
    return int(_ts(dt).timestamp() * 1000)


async def get_bars(session_factory: async_sessionmaker[AsyncSession],
                   adapter, symbol: str, timeframe: str,
                   since_ms: int, until_ms: int) -> pd.DataFrame:
    """读 [since_ms, until_ms] 的 K 线（open_time/open/high/low/close/volume）。

    adapter 需实现 klines(symbol, timeframe, since_ms, until_ms)（与 CcxtGateAdapter 同签名）。
    """
    tf_ms = timeframe_ms(timeframe)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    # 已收盘上限：最后一根可能闭合的 bar 的开盘时间
    closed_open_ms = now_ms - tf_ms

    async with session_factory() as s:
        try:
            rows = (await s.scalars(
                select(BarsCacheModel)
                .where(BarsCacheModel.symbol == symbol,
                       BarsCacheModel.timeframe == timeframe,
                       BarsCacheModel.open_time >= pd.Timestamp(since_ms, unit="ms", tz="UTC"),
                       BarsCacheModel.open_time <= pd.Timestamp(until_ms, unit="ms", tz="UTC"))
                .order_by(BarsCacheModel.open_time))).all()
        except Exception:                       # noqa: BLE001 - 缓存是优化不是依赖
            rows = []

        cached_max_ms = max((_ms(r.open_time) for r in rows), default=None)
        # 从缓存尾部回退几根重拉（校验修正 + 补齐增量）；缓存为空则全量
        if cached_max_ms is None:
            fetch_since = since_ms
        else:
            fetch_since = max(since_ms, cached_max_ms - (_TAIL_RECHECK_BARS - 1) * tf_ms)

        fresh = pd.DataFrame()
        if fetch_since <= until_ms:
            fresh = await adapter.klines(symbol, timeframe, fetch_since, until_ms)

        # 入库：只缓存已收盘 bar（upsert 按主键覆盖，尾部校验自然生效）
        if len(fresh):
            try:
                closable = fresh[fresh["open_time"].map(
                    lambda t: _ms(t) <= closed_open_ms)]
                for r in closable.itertuples():
                    await s.merge(BarsCacheModel(
                        symbol=symbol, timeframe=timeframe,
                        open_time=_ts(r.open_time).to_pydatetime(),
                        open=float(r.open), high=float(r.high), low=float(r.low),
                        close=float(r.close), volume=float(getattr(r, "volume", 0.0))))
                await s.commit()
            except Exception:                   # noqa: BLE001 - 写缓存失败不影响返回
                await s.rollback()

    frames = []
    if rows:
        frames.append(pd.DataFrame([{
            "open_time": _ts(r.open_time), "open": r.open, "high": r.high,
            "low": r.low, "close": r.close, "volume": r.volume} for r in rows]))
    if len(fresh):
        f = fresh.copy()
        f["open_time"] = f["open_time"].map(_ts)
        frames.append(f)
    if not frames:
        return pd.DataFrame(
            columns=["open_time", "open", "high", "low", "close", "volume"])
    out = (pd.concat(frames, ignore_index=True)
           .drop_duplicates(subset="open_time", keep="last")
           .sort_values("open_time").reset_index(drop=True))
    lo, hi = pd.Timestamp(since_ms, unit="ms", tz="UTC"), pd.Timestamp(until_ms, unit="ms", tz="UTC")
    ot = out["open_time"].map(_ts)
    return out[(ot >= lo) & (ot <= hi)].reset_index(drop=True)


async def clear_cache(session_factory: async_sessionmaker[AsyncSession],
                      symbol: str | None = None, timeframe: str | None = None) -> int:
    """清缓存（全量或按品种/周期过滤），返回删除行数。"""
    async with session_factory() as s:
        q = delete(BarsCacheModel)
        if symbol:
            q = q.where(BarsCacheModel.symbol == symbol)
        if timeframe:
            q = q.where(BarsCacheModel.timeframe == timeframe)
        res = await s.execute(q)
        await s.commit()
        return res.rowcount or 0
