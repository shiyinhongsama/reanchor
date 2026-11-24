"""参数扫描测试：网格展开 + 共享 K 线批量执行 + 逐组存档（假适配器，无外网）。

运行：PYTHONPATH=. python tests/test_sweep.py
"""
import asyncio
import tempfile

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.models import Base, BacktestRunModel
from app.schemas import SweepRequest
from app.services import backtest_runner as br
from app.services.backtest_runner import BacktestRunner, expand_sweep

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
engine = create_async_engine(f"sqlite+aiosqlite:///{_tmp.name}")
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class FakeAdapter:
    """合成日线；记录 klines 调用次数以验证扫描只拉一次数据。"""
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


def test_expand():
    req = SweepRequest(symbols=["BTCUSDT"], risk_fracs=[0.05, 0.15],
                       vol_targets=[0.25], param_grid={"slope_k": [3, 4]})
    combos = expand_sweep(req)
    assert len(combos) == 4                       # 2 risk × 1 vt × 2 slope_k
    risks = sorted({c.risk_frac for c in combos})
    ks = sorted({c.params["slope_k"] for c in combos})
    assert risks == [0.05, 0.15] and ks == [3, 4]
    assert all(c.vol_target == 0.25 and c.symbols == ["BTCUSDT"] for c in combos)

    # 空 param_grid：纯风险网格
    combos2 = expand_sweep(SweepRequest(risk_fracs=[0.05, 0.1, 0.15]))
    assert len(combos2) == 3 and all(c.params == {} for c in combos2)
    print("expand_sweep tests passed")


async def test_run_sweep():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    br.SessionLocal = Session
    br.CcxtGateAdapter = FakeAdapter
    FakeAdapter.calls = 0

    r = BacktestRunner()
    req = SweepRequest(symbols=["BTCUSDT"], timeframe="1d",
                       risk_fracs=[0.05, 0.15], param_grid={"slope_k": [8]})
    sweep_id, total = await r.submit_sweep(req)
    assert total == 2

    # submit_sweep 后台执行；测试中直接等待任务收尾
    task = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*task)

    sw = r.sweeps[sweep_id]
    assert sw["status"] == "done" and sw["done"] == 2 and len(sw["job_ids"]) == 2
    assert FakeAdapter.calls == 1, f"扫描应只拉一次 K 线，实际 {FakeAdapter.calls} 次"
    assert all(r.jobs[j]["status"] == "done" for j in sw["job_ids"])

    async with Session() as s:
        rows = (await s.execute(select(BacktestRunModel)
                                .where(BacktestRunModel.id.in_(sw["job_ids"])))).scalars().all()
        assert len(rows) == 2, "每组组合都应存档"
        risks = sorted(row.request["risk_frac"] for row in rows)
        assert risks == [0.05, 0.15]
        assert all(row.status == "done" and row.equity.get("BTCUSDT") for row in rows)
        # 风险更大的组合仓位更大，结果应不同（同一份 K 线下的参数敏感性）
        rets = sorted(row.summary["per_symbol"][0]["total_return"] for row in rows)
        assert rets[0] != rets[1]
    print("run_sweep tests passed")


async def main():
    test_expand()
    await test_run_sweep()


if __name__ == "__main__":
    asyncio.run(main())
