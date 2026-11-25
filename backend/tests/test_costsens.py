"""成本敏感性测试：费率×滑点网格批量重跑（假适配器，无外网）。

核心不变量：信号与定仓不依赖成本 ⇒ 同一网格内交易序列一致 ⇒
收益随费率（或滑点）严格单调递减。
"""
import asyncio
import tempfile

import numpy as np
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.models import Base
from app.schemas import CostSensitivityRequest
from app.services import backtest_runner as br
from app.services.backtest_runner import BacktestRunner

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
engine = create_async_engine(f"sqlite+aiosqlite:///{_tmp.name}")
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class FakeAdapter:
    calls = 0

    def __init__(self, *a, **kw): pass

    async def klines(self, symbol, timeframe, since_ms=None, until_ms=None):
        FakeAdapter.calls += 1
        n = 400
        dates = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
        rng = np.random.default_rng(11)
        close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0008, 0.02, n))))
        return pd.DataFrame({
            "open_time": dates,
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": 1.0,
        })

    async def close(self): pass


async def test_costsens():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    br.SessionLocal = Session
    br.CcxtGateAdapter = FakeAdapter
    FakeAdapter.calls = 0

    r = BacktestRunner()
    req = CostSensitivityRequest(symbols=["BTCUSDT"], timeframe="1d",
                                 fees=[0.0002, 0.0005, 0.001],
                                 slippages=[0.0001, 0.0005])
    cs_id, total = await r.submit_costsens(req)
    assert total == 6

    task = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*task)

    cs = r.costsens[cs_id]
    assert cs["status"] == "done", cs.get("error")
    assert cs["done"] == 6
    assert FakeAdapter.calls == 1, f"应只拉一次 K 线，实际 {FakeAdapter.calls} 次"
    assert not r.results and not r.jobs, "成本网格不应留内存残渣"

    cells = cs["result"]["cells"]
    assert all("error" not in c for c in cells)
    # 单调性：固定滑点，费率递增 ⇒ 总收益严格递减
    for slip in req.slippages:
        rets = [c["total_return"] for c in cells
                if c["slippage"] == slip]
        rets = [x for _, x in sorted(zip(
            [c["fee"] for c in cells if c["slippage"] == slip], rets))]
        assert all(a > b for a, b in zip(rets, rets[1:])), f"滑点 {slip} 下收益未随费率递减: {rets}"
    # 网格上限
    try:
        await r.submit_costsens(CostSensitivityRequest(
            fees=[0.0001] * 5, slippages=[0.0001] * 5))
        raise AssertionError("25 格应被拒")
    except ValueError:
        pass
    print("cost sensitivity tests passed")


if __name__ == "__main__":
    asyncio.run(test_costsens())
