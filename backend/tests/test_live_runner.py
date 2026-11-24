"""实盘调度器测试：tick 状态机（open/trail/close/熔断）+ 触发时刻对齐。

运行：PYTHONPATH=. python tests/test_live_runner.py
"""
import asyncio
import tempfile
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.infrastructure.exchange.ccxt_gate as gate_mod
from app.infrastructure.models import Base, EquitySnapshotModel
from app.schemas import ExecutorCreate, ExecutorSignal
from app.services import executor_service as svc
from app.services import live_runner as lr
from sqlalchemy import select

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
engine = create_async_engine(f"sqlite+aiosqlite:///{_tmp.name}")
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
lr.SessionLocal = Session          # tick 内部用 SessionLocal，替换为测试库


class FakeAdapter:
    calls: list = []

    def __init__(self, *a, **kw): pass

    async def place_order(self, symbol, side, qty, *, dry_run=True):
        self.calls.append(("order", symbol, side, qty, dry_run))
        return {"status": "simulated"}

    async def set_stop(self, symbol, stop_px, *, dry_run=True):
        self.calls.append(("stop", symbol, stop_px, dry_run))
        return {"status": "simulated"}

    async def close(self): pass


gate_mod.CcxtGateAdapter = FakeAdapter     # tick/compute_signal 均为调用点延迟导入


def _sig(**kw):
    base = dict(executor_id="", symbol="BTCUSDT", timeframe="1d",
                as_of=datetime(2026, 11, 22, tzinfo=timezone.utc),
                position=None, entry_signal=0, exit_signal=False,
                suggested_qty=0.0, suggested_stop=None,
                close=105.0, atr=2.0, slope=1.5, note="test")
    base.update(kw)
    return ExecutorSignal(**base)


def test_fire_time_alignment():
    now = datetime(2026, 11, 22, 12, 0, 3, tzinfo=timezone.utc)
    t1m = lr.next_fire_after("1m", now)
    assert (t1m - now).total_seconds() == 60 - 3 + 5        # 下一分钟收盘 + 5s
    t1d = lr.next_fire_after("1d", now)
    assert t1d.hour == 0 and t1d.minute == 5 and t1d.day == 23   # 次日 00:05 UTC
    assert lr.close_offset_s(60) == 5 and lr.close_offset_s(86400) == 300


async def _tick_state_machine():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as s:
        m = await svc.create_executor(s, ExecutorCreate(symbol="BTCUSDT", equity_budget=1000))
        eid = m.id
        await svc.update_executor(s, eid, _upd_running())

    # 1) 空仓 + 多头信号 → open，挂初始止损 + 记账（开仓手续费）
    orig = svc.compute_signal
    svc.compute_signal = lambda s, eid: _async(_sig(entry_signal=1, suggested_qty=0.5,
                                                    suggested_stop=99.0))
    try:
        r = await lr.tick(eid)
        assert r["action"] == "open", r
        async with Session() as s:
            m = await svc.get_executor(s, eid)
            pos = m.state["position"]
            assert pos["side"] == 1 and pos["current_stop"] == 99.0
            assert pos["entry_px"] == 105.0
            logs = await svc.list_signals(s, eid)
            assert logs[0]["action"] == "open"
            # 记账：cash = 1000 − 0.5×105×0.0005 = 999.97375，浮盈 0
            assert abs(m.state["cash"] - 999.97375) < 1e-3
            assert abs(m.state["last_equity"] - 999.97) < 0.01
            snaps = await svc.list_equity(s, eid)
            assert len(snaps) == 1 and abs(snaps[0]["equity"] - 999.97375) < 1e-3
        assert ("order", "BTCUSDT", "long", 0.5, True) in FakeAdapter.calls

        # 2) 持仓 + 斜率同向 → 重锚止损（105 - 3×2 = 99，等于初始 → hold；抬高 close 再试）
        svc.compute_signal = lambda s, eid: _async(_sig(close=110.0, slope=1.5))
        r = await lr.tick(eid)
        assert r["action"] == "trail_stop", r
        async with Session() as s:
            m = await svc.get_executor(s, eid)
            assert m.state["position"]["current_stop"] == 104.0     # 110 - 3×2
            # 浮盈 = 0.5×(110−105) = 2.5 → equity = 999.97375 + 2.5
            assert abs(m.state["last_equity"] - 1002.47) < 0.01
            snaps = await svc.list_equity(s, eid)
            assert len(snaps) == 2 and abs(snaps[-1]["unrealized"] - 2.5) < 1e-3

        # 3) 斜率翻转 → close，清仓 + 已实现盈亏（0.5×(105−105)=0，扣平仓费）
        svc.compute_signal = lambda s, eid: _async(_sig(exit_signal=True, slope=-0.5))
        r = await lr.tick(eid)
        assert r["action"] == "close", r
        async with Session() as s:
            m = await svc.get_executor(s, eid)
            assert m.state["position"] is None
            # cash = 999.97375 − 0.5×105×0.0005 = 999.9475（双边手续费后的净额）
            assert abs(m.state["cash"] - 999.9475) < 1e-3
            assert abs(m.state["last_equity"] - 999.95) < 0.01
            snaps = await svc.list_equity(s, eid)
            assert len(snaps) == 3 and abs(snaps[-1]["unrealized"]) < 1e-3

        # 4) 连续 3 次异常 → 熔断暂停
        async def _boom(s, eid): raise RuntimeError("network down")
        svc.compute_signal = _boom
        for _ in range(3):
            await lr.tick(eid)
        async with Session() as s:
            m = await svc.get_executor(s, eid)
            assert m.status == "paused" and m.state["consecutive_failures"] == 3
            logs = await svc.list_signals(s, eid)
            assert logs[0]["action"] == "circuit_break"

        # 5) 熔断后 tick 不再执行
        r = await lr.tick(eid)
        assert r is None
    finally:
        svc.compute_signal = orig


def _upd_running():
    from app.schemas import ExecutorUpdate
    return ExecutorUpdate(status="running")


def _async(v):
    async def _r(): return v
    return _r()


async def _main():
    test_fire_time_alignment()
    await _tick_state_machine()
    print("live runner tests passed")


if __name__ == "__main__":
    asyncio.run(_main())
