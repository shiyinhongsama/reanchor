"""回测存档测试：任务结束落库 + 对比页数据形状（临时库，无外网依赖）。

运行：PYTHONPATH=. python tests/test_backtest_persist.py
"""
import asyncio
import tempfile
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.models import Base, BacktestRunModel
from app.schemas import BacktestRequest, SymbolMetrics
from app.services import backtest_runner as br
from app.services.backtest_runner import BacktestRunner

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
engine = create_async_engine(f"sqlite+aiosqlite:///{_tmp.name}")
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    br.SessionLocal = Session  # 重定向到临时库

    r = BacktestRunner()
    job_id = "testjob12345"
    now = datetime.now(timezone.utc)
    r.jobs[job_id] = {"status": "done", "created_at": now, "finished_at": now}
    idx = pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC")
    eq = pd.Series([10000.0, 10100.0, 10050.0, 10200.0, 10300.0], index=idx)
    m = SymbolMetrics(symbol="BTCUSDT", total_return=0.03, annualized=2.5, sharpe=1.1,
                      max_drawdown=-0.01, calmar=2.0, trades=3, profit_factor=1.5)
    r.results[job_id] = {"BTCUSDT": {"equity": eq, "trades": pd.DataFrame()},
                         "summary": {"per_symbol": [m], "portfolio": None}}

    req = BacktestRequest(symbols=["BTCUSDT"], timeframe="1d", risk_frac=0.05)
    await r._persist(job_id, req)

    async with Session() as s:
        row = await s.get(BacktestRunModel, job_id)
        assert row is not None, "存档未落库"
        assert row.status == "done"
        assert row.strategy == "ema_pullback_v1" and row.symbols == ["BTCUSDT"]
        assert row.request["risk_frac"] == 0.05 and row.timeframe == "1d"
        assert row.summary["per_symbol"][0]["total_return"] == 0.03
        assert row.summary["portfolio"] is None
        pts = row.equity["BTCUSDT"]
        assert len(pts) == 5 and pts[0][1] == 10000.0 and pts[-1][1] == 10300.0
        assert pts[0][0].startswith("2024-01-01")

    # 失败任务也存档（status=failed + error），对比页按 status 过滤
    fail_id = "failjob12345"
    r.jobs[fail_id] = {"status": "failed", "error": "boom", "created_at": now, "finished_at": now}
    await r._persist(fail_id, req)
    async with Session() as s:
        row = await s.get(BacktestRunModel, fail_id)
        assert row is not None and row.status == "failed" and row.error == "boom"
        assert row.equity == {}

    print("backtest persist tests passed")


if __name__ == "__main__":
    asyncio.run(main())
