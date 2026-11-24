"""K 线接口测试：按存档请求重拉行情 + 丢弃未收盘 bar（假适配器，无外网）。

运行：PYTHONPATH=. python tests/test_bars.py
"""
import asyncio
import tempfile

import numpy as np
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1 import backtests as bt
from app.infrastructure.db import get_session
from app.infrastructure.models import Base, BacktestRunModel

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
engine = create_async_engine(f"sqlite+aiosqlite:///{_tmp.name}")
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class FakeAdapter:
    """合成日线，最后一根为进行中的 bar（应被接口丢弃）。"""

    def __init__(self, *a, **kw): pass

    async def klines(self, symbol, timeframe, since_ms=None, until_ms=None):
        n = 10
        dates = pd.date_range(end=pd.Timestamp.now("UTC").normalize(),
                              periods=n, freq="D", tz="UTC")
        close = pd.Series(np.linspace(100, 110, n))
        return pd.DataFrame({
            "open_time": dates,
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": 1.0,
        })

    async def close(self): pass


async def _seed() -> str:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with Session() as s:
        row = BacktestRunModel(
            id="barsjob12345", status="done", strategy="ema_pullback_v1",
            symbols=["BTCUSDT"], timeframe="1d",
            request={"symbols": ["BTCUSDT"], "start": "2024-01-01", "end": None},
            summary={}, equity={})
        s.add(row)
        await s.commit()
    return "barsjob12345"


def main():
    job_id = asyncio.run(_seed())
    bt.CcxtGateAdapter = FakeAdapter

    app = FastAPI()
    app.include_router(bt.router, prefix="/api/v1")

    async def override_session():
        async with Session() as s:
            yield s
    app.dependency_overrides[get_session] = override_session
    client = TestClient(app)

    r = client.get(f"/api/v1/backtests/{job_id}/bars?symbol=BTCUSDT")
    assert r.status_code == 200, r.text
    bars = r.json()["bars"]
    assert len(bars) == 9, f"进行中的 bar 应被丢弃，实际 {len(bars)} 根"
    first, last = bars[0], bars[-1]
    assert set(first) == {"t", "o", "h", "l", "c"}
    assert last["h"] >= last["c"] >= last["l"]
    # 最后一根应是昨天（今天 00:00 开盘的未收盘 bar 被丢弃）
    assert pd.Timestamp(last["t"]) < pd.Timestamp.now("UTC").normalize()

    # 品种不在该回测中
    assert client.get(f"/api/v1/backtests/{job_id}/bars?symbol=DOGEUSDT").status_code == 404
    # 任务不存在
    assert client.get("/api/v1/backtests/nonexistent/bars?symbol=BTCUSDT").status_code == 404
    print("bars api tests passed")


if __name__ == "__main__":
    main()
