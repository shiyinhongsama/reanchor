"""K 线缓存测试：增量补拉、进行中 bar 不入库、尾部校验、清空（临时库，无外网）。

运行：PYTHONPATH=. python tests/test_bars_cache.py
"""
import asyncio
import tempfile

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.models import Base, BarsCacheModel
from app.services import bars_service

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
engine = create_async_engine(f"sqlite+aiosqlite:///{_tmp.name}")
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class FakeAdapter:
    """合成日线（最后一根为进行中 bar），记录每次拉取的 since_ms。"""

    calls: list[tuple[int, int]] = []

    def __init__(self, n=30):
        self.n = n

    async def klines(self, symbol, timeframe, since_ms=None, until_ms=None):
        FakeAdapter.calls.append((since_ms, until_ms))
        dates = pd.date_range(end=pd.Timestamp.now("UTC").normalize(),
                              periods=self.n, freq="D", tz="UTC")
        close = pd.Series(np.linspace(100, 100 + self.n, self.n))
        df = pd.DataFrame({
            "open_time": dates,
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": 1.0,
        })
        return df[df["open_time"] >= pd.Timestamp(since_ms, tz="UTC")]


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    since = int((pd.Timestamp.now("UTC") - pd.Timedelta(days=40)).timestamp() * 1000)
    until = int(pd.Timestamp.now("UTC").timestamp() * 1000)

    # 第一次：全量拉取，已收盘 bar 入库（进行中的今日 bar 返回但不缓存）
    FakeAdapter.calls = []
    adapter = FakeAdapter(n=30)
    df1 = await bars_service.get_bars(Session, adapter, "TESTUSDT", "1d", since, until)
    assert len(FakeAdapter.calls) == 1
    assert len(df1) == 30, f"返回含进行中 bar 共 30 根，实际 {len(df1)}"
    async with Session() as s:
        n_cached = await s.scalar(select(func.count()).select_from(BarsCacheModel))
        assert n_cached == 29, f"进行中 bar 不应入库，实际缓存 {n_cached} 根"

    # 第二次同区间：只回拉尾部校验（since 明显晚于首次），结果一致，缓存行数不变
    df2 = await bars_service.get_bars(Session, adapter, "TESTUSDT", "1d", since, until)
    assert len(FakeAdapter.calls) == 2
    first_since, second_since = FakeAdapter.calls[0][0], FakeAdapter.calls[1][0]
    assert second_since > first_since, "命中缓存后应只补拉尾部增量"
    assert len(df2) == 30 and df1["close"].equals(df2["close"])
    async with Session() as s:
        assert await s.scalar(select(func.count()).select_from(BarsCacheModel)) == 29

    # 不同品种互不影响
    await bars_service.get_bars(Session, adapter, "OTHERUSDT", "1d", since, until)
    async with Session() as s:
        n = await s.scalar(select(func.count()).select_from(BarsCacheModel)
                           .where(BarsCacheModel.symbol == "OTHERUSDT"))
        assert n == 29

    # 清空：按品种过滤
    deleted = await bars_service.clear_cache(Session, symbol="TESTUSDT")
    assert deleted == 29
    async with Session() as s:
        n = await s.scalar(select(func.count()).select_from(BarsCacheModel))
        assert n == 29, "OTHERUSDT 的缓存应保留"

    print("bars cache tests passed")


if __name__ == "__main__":
    asyncio.run(main())
