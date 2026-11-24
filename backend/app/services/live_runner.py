"""实盘执行器调度（设计文档 §4.2）：按执行器 timeframe 在 bar 收盘后触发信号循环。

- 每个 running 执行器一个 IntervalTrigger，start_date 对齐到下一个 bar 收盘点 + 安全偏移；
- tick 状态机：exit → close；flat + entry → open + 挂初始止损；持仓 + 斜率同向 → 重锚止损；
- 连续 3 次失败自动熔断（暂停该执行器，不影响其他执行器）；
- 一期所有订单 dry_run，除非 TP_LIVE_ENABLED=true。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..domain.sizing import ReanchorStop
from ..domain.timeframes import timeframe_ms
from ..infrastructure.db import SessionLocal
from ..infrastructure.models import EquitySnapshotModel, ExecutorModel, SignalLogModel
from . import executor_service as svc

log = logging.getLogger("live_runner")


def close_offset_s(tf_s: int) -> int:
    """收盘后安全偏移：周期的 1%，夹在 [5s, 300s]（1d → 00:05 UTC，与手册 SOP 一致）。"""
    return min(max(tf_s // 100, 5), 300)


def next_fire_after(timeframe: str, now: datetime | None = None) -> datetime:
    """下一次触发时刻：下一个 bar 收盘点 + 安全偏移。"""
    tf_s = timeframe_ms(timeframe) // 1000
    now = now or datetime.now(timezone.utc)
    boundary = (int(now.timestamp()) // tf_s + 1) * tf_s
    return datetime.fromtimestamp(boundary + close_offset_s(tf_s), tz=timezone.utc)


async def _record_failure(s: AsyncSession, m: ExecutorModel, err: Exception) -> dict:
    state = json.loads(json.dumps(m.state or {}))   # 必须深拷贝：浅拷贝会就地污染 m.state，
                                                    # 使最终赋值内容相等、SQLAlchemy 跳过 UPDATE
    fails = int(state.get("consecutive_failures", 0)) + 1
    state["consecutive_failures"] = fails
    m.state = json.loads(json.dumps(state))
    action = "error"
    if fails >= 3:
        m.status = "paused"            # 熔断：只停这一个执行器
        action = "circuit_break"
        log.warning("executor %s circuit-broken after %d failures: %s", m.id, fails, err)
    s.add(SignalLogModel(executor_id=m.id, bar_time=None, action=action,
                         detail={"error": str(err)[:500]}))
    await s.commit()
    return {"executor_id": m.id, "action": action, "error": str(err)}


async def tick(executor_id: str) -> dict | None:
    """单执行器单次 bar 收盘触发：信号 → 动作 → 落日志。"""
    from ..infrastructure.exchange.ccxt_gate import CcxtGateAdapter   # 延迟导入便于测试替换

    settings = get_settings()
    async with SessionLocal() as s:
        m = await s.get(ExecutorModel, executor_id)
        if not m or m.status != "running":
            return None
        try:
            sig = await svc.compute_signal(s, executor_id)
        except Exception as e:  # noqa: BLE001 - 任何失败都计入熔断
            return await _record_failure(s, m, e)

        dry = not settings.live_enabled
        adapter = CcxtGateAdapter(settings.gate_api_key, settings.gate_api_secret,
                                  settings.gate_sandbox)
        try:
            state = json.loads(json.dumps(m.state or {}))   # 深拷贝，原因同上
            if "cash" not in state:                          # 账面钱包：首 tick 以预算初始化
                state["cash"] = float(m.equity_budget)
            cash = float(state["cash"])
            fee_rate = float(settings.default_fee)
            pos = state.get("position")
            action = "hold"

            if pos and sig.exit_signal:                      # 斜率翻转 → 平仓
                await adapter.place_order(m.symbol, "close", pos["qty"], dry_run=dry)
                if sig.close and pos.get("entry_px"):
                    fill = float(sig.close)
                    cash += pos["qty"] * (fill - float(pos["entry_px"])) * int(pos["side"]) \
                        - pos["qty"] * fill * fee_rate       # 已实现盈亏 − 平仓手续费
                state["position"] = None
                action = "close"
            elif not pos and sig.entry_signal:               # 空仓 + 信号 → 开仓 + 初始止损
                side_name = "long" if sig.entry_signal > 0 else "short"
                await adapter.place_order(m.symbol, side_name, sig.suggested_qty, dry_run=dry)
                if sig.suggested_stop:
                    await adapter.set_stop(m.symbol, sig.suggested_stop, dry_run=dry)
                entry_px = float(sig.close) if sig.close else None
                if entry_px:
                    cash -= sig.suggested_qty * entry_px * fee_rate   # 开仓手续费
                state["position"] = {
                    "side": sig.entry_signal, "qty": sig.suggested_qty,
                    "entry_px": entry_px,
                    "entry_stop": sig.suggested_stop, "current_stop": sig.suggested_stop,
                    "entry_bar": sig.as_of.isoformat(),
                    "aligned_bars": 1,   # 入场信号本身要求斜率同向，与回测引擎对齐
                }
                action = "open"
            elif pos and sig.atr and sig.slope is not None and sig.close:
                # 重锚止损：v1 每根按收盘重锚；v2 需斜率同向满 N 根（其间峰值跟踪）
                aligned = (pos["side"] > 0 and sig.slope > 0) or (pos["side"] < 0 and sig.slope < 0)
                rs = ReanchorStop(int(pos["side"]), float(pos["entry_stop"]),
                                  float(m.params.get("atr_mult", 3.0)),
                                  int(m.params.get("reanchor_min_bars", 1)))
                rs.current = float(pos.get("current_stop", pos["entry_stop"]))
                rs.peak = pos.get("peak")
                rs.aligned_bars = int(pos.get("aligned_bars", 0))
                new_stop = rs.update(sig.close, sig.atr, aligned)
                pos["peak"], pos["aligned_bars"] = rs.peak, rs.aligned_bars
                if new_stop != pos.get("current_stop"):
                    await adapter.set_stop(m.symbol, new_stop, dry_run=dry)
                    pos["current_stop"] = new_stop
                    action = "trail_stop"

            state["consecutive_failures"] = 0
            state["cash"] = round(cash, 4)
            # 盯市盈亏与权益快照（每 tick 一条，账面口径见 EquitySnapshotModel docstring）
            pos_now = state.get("position")
            unreal = 0.0
            if pos_now and sig.close and pos_now.get("entry_px"):
                unreal = pos_now["qty"] * (float(sig.close) - float(pos_now["entry_px"])) * int(pos_now["side"])
            equity = cash + unreal
            state["last_equity"] = round(equity, 2)
            m.state = json.loads(json.dumps(state))          # 强制 SQLAlchemy 感知变更 + 校验可序列化
            s.add(m)
            s.add(EquitySnapshotModel(
                executor_id=m.id, bar_time=sig.as_of,
                equity=round(equity, 4), cash=round(cash, 4),
                unrealized=round(unreal, 4),
                close=float(sig.close) if sig.close else None))
            s.add(SignalLogModel(
                executor_id=m.id, bar_time=sig.as_of, action=action,
                entry_signal=sig.entry_signal, exit_signal=sig.exit_signal,
                side=(state.get("position") or {}).get("side"),
                qty=sig.suggested_qty or None, stop=sig.suggested_stop,
                atr=sig.atr, slope=sig.slope,
                detail={"close": sig.close, "dry_run": dry, "explain": sig.explain}))
            await s.commit()
            return {"executor_id": m.id, "action": action}
        finally:
            await adapter.close()


class LiveRunner:
    """执行器调度器：running 执行器各有自己的 bar 收盘触发任务。"""

    def __init__(self):
        self._scheduler = AsyncIOScheduler(timezone=timezone.utc)
        self._started = False

    async def start(self) -> None:
        async with SessionLocal() as s:
            rows = await s.execute(
                select(ExecutorModel.id, ExecutorModel.timeframe)
                .where(ExecutorModel.status == "running"))
            for eid, tf in rows.all():
                self._add_job(eid, tf)
        self._scheduler.start()
        self._started = True
        log.info("live runner started, %d executor jobs", len(self._scheduler.get_jobs()))

    def _add_job(self, executor_id: str, timeframe: str) -> None:
        self._scheduler.add_job(
            tick,
            trigger=IntervalTrigger(seconds=timeframe_ms(timeframe) // 1000,
                                    start_date=next_fire_after(timeframe)),
            id=executor_id, args=[executor_id],
            replace_existing=True, misfire_grace_time=60)

    def _remove_job(self, executor_id: str) -> None:
        if self._scheduler.get_job(executor_id):
            self._scheduler.remove_job(executor_id)

    async def sync_executor(self, executor_id: str) -> None:
        """执行器状态变更后调用：running 则注册/重置任务，否则摘除。"""
        async with SessionLocal() as s:
            m = await s.get(ExecutorModel, executor_id)
        if not m or m.status != "running":
            self._remove_job(executor_id)
        else:
            self._add_job(m.id, m.timeframe)

    def jobs(self) -> list[dict]:
        return [{"executor_id": j.id,
                 "next_run": j.next_run_time.isoformat() if j.next_run_time else None}
                for j in self._scheduler.get_jobs()]

    async def shutdown(self) -> None:
        if self._started:
            self._scheduler.shutdown(wait=False)


runner = LiveRunner()
