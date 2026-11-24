"""执行器 K 线接口测试：最近 N 根已收盘 bar + 丢弃未收盘 bar（假适配器，无外网）。

运行：PYTHONPATH=. python tests/test_executor_bars.py
"""
import asyncio
import tempfile

import numpy as np
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1 import executors as ex_api
from app.infrastructure.db import get_session
from app.infrastructure.models import Base
from app.schemas import ExecutorCreate
from app.services import executor_service as svc

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
        m = await svc.create_executor(s, ExecutorCreate(
            name="bars-test", symbol="BTCUSDT", timeframe="1d"))
        return m.id


def main():
    ex_id = asyncio.run(_seed())
    ex_api.CcxtGateAdapter = FakeAdapter

    app = FastAPI()
    app.include_router(ex_api.router, prefix="/api/v1")

    async def override_session():
        async with Session() as s:
            yield s
    app.dependency_overrides[get_session] = override_session
    client = TestClient(app)

    r = client.get(f"/api/v1/executors/{ex_id}/bars")
    assert r.status_code == 200, r.text
    bars = r.json()["bars"]
    assert len(bars) == 9, f"进行中的 bar 应被丢弃，实际 {len(bars)} 根"
    first, last = bars[0], bars[-1]
    assert set(first) == {"t", "o", "h", "l", "c"}
    assert last["h"] >= last["c"] >= last["l"]
    assert pd.Timestamp(last["t"]) < pd.Timestamp.now("UTC").normalize()

    # 执行器不存在
    assert client.get("/api/v1/executors/nonexistent/bars").status_code == 404
    print("executor bars api tests passed")


if __name__ == "__main__":
    main()
