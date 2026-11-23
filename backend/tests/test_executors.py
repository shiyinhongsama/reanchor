"""执行器服务测试：CRUD 校验 + dry-run 信号链路（假适配器，无外网依赖）。

运行：PYTHONPATH=. python tests/test_executors.py
"""
import asyncio
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.timeframes import SUPPORTED
from app.infrastructure.models import Base
from app.schemas import ExecutorCreate, ExecutorUpdate
from app.services import executor_service as svc

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
engine = create_async_engine(f"sqlite+aiosqlite:///{_tmp.name}")
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class FakeAdapter:
    """返回合成日线：最后一条为进行中的 bar（应被信号计算丢弃）。"""

    def __init__(self, *a, **kw): pass

    async def klines(self, symbol, timeframe, since_ms=None, until_ms=None):
        n = 301
        dates = pd.date_range(end=pd.Timestamp.now("UTC").normalize(),
                              periods=n, freq="D", tz="UTC")
        rng = np.random.default_rng(7)
        close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n)))
        close = pd.Series(close)
        return pd.DataFrame({
            "open_time": dates,
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": 1.0,
        })

    async def close(self): pass


def test_timeframe_table():
    assert "1m" in SUPPORTED and "2d" in SUPPORTED and "1d" in SUPPORTED
    assert svc.TIMEFRAMES["2d"][0] == "1d"          # 非原生周期由 1d 重采样
    assert svc.TIMEFRAMES["1d"][1] == 365.0


async def _crud():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as s:
        # 创建：参数合并默认值
        m = await svc.create_executor(s, ExecutorCreate(
            symbol="ETHUSDT", timeframe="1d", params={"slope_k": 5}, equity_budget=500))
        assert m.params == {"ema_n": 20, "slope_k": 5, "atr_n": 14, "atr_mult": 3.0,
                        "reanchor_min_bars": 1}
        assert m.status == "paused" and m.symbol == "ETHUSDT"
        eid = m.id

        # 未知参数拒绝
        try:
            await svc.create_executor(s, ExecutorCreate(params={"foo": 1}))
            raise AssertionError("未知参数应被拒绝")
        except ValueError as e:
            assert "foo" in str(e)

        # 超范围参数拒绝
        try:
            await svc.create_executor(s, ExecutorCreate(params={"ema_n": 999}))
            raise AssertionError("超范围参数应被拒绝")
        except ValueError:
            pass

        # 未知策略拒绝
        try:
            await svc.create_executor(s, ExecutorCreate(strategy="nope"))
            raise AssertionError("未知策略应被拒绝")
        except ValueError:
            pass

        # 更新：启停 + 参数
        m = await svc.update_executor(s, eid, ExecutorUpdate(status="running",
                                                             params={"slope_k": 8}))
        assert m.status == "running" and m.params["slope_k"] == 8
        assert await svc.count_by_status(s, "running") == 1

        # 删除 = 停止
        m = await svc.delete_executor(s, eid)
        assert m.status == "stopped"
    return eid


async def _signal(eid):
    import app.infrastructure.exchange.ccxt_gate as gate_mod
    gate_mod.CcxtGateAdapter = FakeAdapter          # 调用点在使用时才 import，替换生效
    try:
        async with Session() as s:
            m = await svc.get_executor(s, eid)
            await svc.update_executor(s, eid, ExecutorUpdate(status="paused"))
            sig = await svc.compute_signal(s, eid)
            assert sig.executor_id == eid and sig.dry_run
            assert sig.entry_signal in (-1, 0, 1)
            assert sig.atr and sig.atr > 0
            # as_of 必须是"昨天"（进行中的今日 bar 被丢弃）
            today = pd.Timestamp.now("UTC").normalize()
            assert pd.Timestamp(sig.as_of) < today, f"as_of={sig.as_of} 包含了未收盘 bar"
    finally:
        pass  # 进程结束即还原，无需手动恢复


async def _main():
    test_timeframe_table()
    eid = await _crud()
    await _signal(eid)
    print("executor tests passed")


if __name__ == "__main__":
    asyncio.run(_main())
