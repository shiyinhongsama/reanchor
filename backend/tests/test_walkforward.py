"""走步验证测试：切折、训练段寻优、样本外评估、OOS 拼接（临时库，无外网）。

运行：PYTHONPATH=. python tests/test_walkforward.py
"""
import asyncio
import tempfile

import numpy as np
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.models import Base
from app.schemas import WalkForwardRequest
from app.services.backtest_runner import BacktestRunner
import app.services.backtest_runner as br

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
engine = create_async_engine(f"sqlite+aiosqlite:///{_tmp.name}")
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class FakeAdapter:
    """合成日线：趋势+噪声的随机游走（可复现）。"""

    def __init__(self, *a, **kw): pass

    async def klines(self, symbol, timeframe, since_ms=None, until_ms=None):
        rng = np.random.default_rng(7)
        n = 800
        drift = np.sin(np.arange(n) / 60.0) * 0.6          # 周期性趋势段
        close = 100 + np.cumsum(drift + rng.normal(0, 0.8, n))
        dates = pd.date_range(end=pd.Timestamp.now("UTC").normalize(),
                              periods=n, freq="D", tz="UTC")
        return pd.DataFrame({
            "open_time": dates,
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": 1.0,
        })

    async def close(self): pass


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    br.CcxtGateAdapter = FakeAdapter
    br.SessionLocal = Session

    r = BacktestRunner()
    req = WalkForwardRequest(symbol="TESTUSDT", timeframe="1d",
                             start="2023-01-01", folds=4, train_frac=0.7,
                             criterion="calmar",
                             param_grid={"slope_k": [5, 8]})
    wf_id = await r.submit_walkforward(req)
    task = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*task)

    wf = r.wfs[wf_id]
    assert wf["status"] == "done", wf.get("error")
    res = wf["result"]
    assert len(res["folds"]) == 4
    for f in res["folds"]:
        assert f["params"]["slope_k"] in (5, 8), "训练段应在网格内选参数"
        assert f["test_metrics"], "测试段应有指标"
        assert f["train"][0] < f["test"][0] < f["test"][1], "训练段必须在测试段之前"
    agg = res["aggregate"]
    assert agg["profitable_folds"].endswith("/4")
    assert agg["mean_annualized"] is not None
    assert len(res["oos_equity"]) > 100, "OOS 拼接净值应有实质长度"
    vs = [p["v"] for p in res["oos_equity"]]
    assert all(v > 0 for v in vs)

    # 固定参数模式（空网格）：每折参数一致
    req2 = WalkForwardRequest(symbol="TESTUSDT", timeframe="1d",
                              start="2023-01-01", folds=3, params={"slope_k": 8})
    wf_id2 = await r.submit_walkforward(req2)
    task = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*task)
    wf2 = r.wfs[wf_id2]
    assert wf2["status"] == "done", wf2.get("error")
    assert all(f["params"]["slope_k"] == 8 for f in wf2["result"]["folds"])

    print("walkforward tests passed")


if __name__ == "__main__":
    asyncio.run(main())
