"""执行器服务：策略模板 → 执行器实例的 CRUD 与 dry-run 信号计算。

设计决策 #9：策略是无状态模板，执行器持有参数与运行时状态。
回测与实盘共用同一套"执行器配置"概念。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..domain.sizing import SizingConfig, position_size
from ..domain.strategy import StrategyParams
from ..domain.timeframes import TIMEFRAMES, timeframe_ms
from ..infrastructure.models import ExecutorModel
from ..schemas import ExecutorCreate, ExecutorSignal, ExecutorUpdate
from .backtest_runner import STRATEGIES

# 信号计算需要的预热 bar 数（覆盖 EMA200 + slope_k + ATR + 波动率回看）
_WARMUP_BARS = 300


def effective_params(strat_cls, overrides: dict) -> dict:
    """按策略 params_schema 校验并合并默认值；未知参数直接拒绝。"""
    schema = getattr(strat_cls, "params_schema", {})
    unknown = set(overrides) - set(schema)
    if unknown:
        raise ValueError(f"策略 {strat_cls.name} 不接受参数: {sorted(unknown)}")
    eff = {}
    for key, spec in schema.items():
        v = overrides.get(key, spec["default"])
        try:
            v = int(v) if spec["type"] == "int" else float(v)
        except (TypeError, ValueError):
            raise ValueError(f"参数 {key} 类型错误，期望 {spec['type']}") from None
        if v < spec.get("min", v) or v > spec.get("max", v):
            raise ValueError(f"参数 {key}={v} 超出范围 [{spec.get('min')}, {spec.get('max')}]")
        eff[key] = v
    return eff


def _to_dict(m: ExecutorModel) -> ExecutorModel:
    """触发属性加载（配合 expire_on_commit=False 其实可直接用，这里仅做语义封装）。"""
    return m


async def create_executor(session: AsyncSession, req: ExecutorCreate) -> ExecutorModel:
    strat_cls = STRATEGIES.get(req.strategy)
    if not strat_cls:
        raise ValueError(f"未知策略 {req.strategy}，可选：{list(STRATEGIES)}")
    params = effective_params(strat_cls, req.params)
    m = ExecutorModel(
        id=uuid.uuid4().hex[:12],
        name=req.name or f"{req.strategy}-{req.symbol}-{req.timeframe}",
        strategy_name=req.strategy,
        symbol=req.symbol.upper(),
        timeframe=req.timeframe,
        params=params,
        equity_budget=req.equity_budget,
        risk_frac=req.risk_frac,
        vol_target=req.vol_target,
        max_leverage=req.max_leverage,
        status="paused",
        state={},
    )
    session.add(m)
    await session.commit()
    return _to_dict(m)


async def list_executors(session: AsyncSession) -> list[ExecutorModel]:
    rows = await session.scalars(select(ExecutorModel).order_by(ExecutorModel.created_at.desc()))
    return list(rows)


async def get_executor(session: AsyncSession, executor_id: str) -> ExecutorModel | None:
    return await session.get(ExecutorModel, executor_id)


async def count_by_status(session: AsyncSession, status: str) -> int:
    q = select(func.count()).select_from(ExecutorModel).where(ExecutorModel.status == status)
    return int(await session.scalar(q) or 0)


async def update_executor(session: AsyncSession, executor_id: str,
                          req: ExecutorUpdate) -> ExecutorModel | None:
    m = await session.get(ExecutorModel, executor_id)
    if not m:
        return None
    data = req.model_dump(exclude_none=True)
    if "params" in data:
        strat_cls = STRATEGIES[m.strategy_name]
        data["params"] = effective_params(strat_cls, data["params"])
    for k, v in data.items():
        setattr(m, k, v)
    await session.commit()
    return _to_dict(m)


async def delete_executor(session: AsyncSession, executor_id: str) -> ExecutorModel | None:
    """停止执行器。一期：仅标记 stopped；持仓平仓在实盘执行器接入后处理。"""
    m = await session.get(ExecutorModel, executor_id)
    if not m:
        return None
    m.status = "stopped"
    await session.commit()
    return _to_dict(m)


async def list_signals(session: AsyncSession, executor_id: str, limit: int = 100) -> list[dict]:
    from ..infrastructure.models import SignalLogModel
    rows = await session.scalars(
        select(SignalLogModel).where(SignalLogModel.executor_id == executor_id)
        .order_by(SignalLogModel.id.desc()).limit(limit))
    return [{
        "id": r.id, "bar_time": r.bar_time.isoformat() if r.bar_time else None,
        "action": r.action, "entry_signal": r.entry_signal, "exit_signal": r.exit_signal,
        "side": r.side, "qty": r.qty, "stop": r.stop, "atr": r.atr, "slope": r.slope,
        "detail": r.detail,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


async def compute_signal(session: AsyncSession, executor_id: str) -> ExecutorSignal:
    """dry-run 信号快照：基于最近一根已收盘 K 线，计算下一根开盘应采取的动作。

    防前视纪律：丢弃进行中的最后一根 bar，信号只读已收盘数据。
    VT 缩放一期取 1.0（执行器尚无权益历史），与研究口径的差异在 note 中说明。
    """
    from ..infrastructure.exchange.ccxt_gate import CcxtGateAdapter

    m = await session.get(ExecutorModel, executor_id)
    if not m:
        raise LookupError("executor not found")
    strat_cls = STRATEGIES[m.strategy_name]
    params = effective_params(strat_cls, m.params)

    fetch_tf, bpy = TIMEFRAMES[m.timeframe]
    tf_ms = timeframe_ms(fetch_tf)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    since_ms = now_ms - _WARMUP_BARS * tf_ms

    settings = get_settings()
    adapter = CcxtGateAdapter(settings.gate_api_key, settings.gate_api_secret,
                              settings.gate_sandbox)
    try:
        bars = await adapter.klines(m.symbol, m.timeframe, since_ms, now_ms)
    finally:
        await adapter.close()

    if len(bars) < 60:
        raise ValueError(f"K 线不足（{len(bars)} 根），无法计算信号")

    # 丢弃进行中的最后一根 bar（开盘时间 + 周期 > 当前时刻）
    eff_tf_ms = timeframe_ms(fetch_tf) if fetch_tf == m.timeframe else timeframe_ms(m.timeframe)
    last_open_ms = int(bars["open_time"].iloc[-1].timestamp() * 1000)
    if last_open_ms + eff_tf_ms > now_ms:
        bars = bars.iloc[:-1].reset_index(drop=True)

    strat = strat_cls()
    sp = StrategyParams(params)
    d = strat.prepare(bars, sp)
    t = len(d)  # 下一根（未发生的）执行 bar；信号函数内部只读 t-1
    entry = strat.entry_signal(d, t, sp)

    pos = (m.state or {}).get("position")
    exit_sig = bool(strat.exit_signal(d, t, int(pos["side"]), sp)) if pos else False

    row = d.iloc[-1]
    atr = float(row.atr) if not np.isnan(row.atr) else None
    slope = float(row.slope) if not np.isnan(row.slope) else None
    close = float(row.close)
    atr_mult = params.get("atr_mult", 3.0)

    qty, stop = 0.0, None
    if entry and atr:
        cfg = SizingConfig(risk_frac=m.risk_frac, max_leverage=m.max_leverage,
                           vol_target=m.vol_target, bars_per_year=bpy)
        qty = position_size(m.equity_budget, close, atr_mult * atr, 1.0, cfg)
        stop = close - entry * atr_mult * atr

    return ExecutorSignal(
        executor_id=m.id, symbol=m.symbol, timeframe=m.timeframe,
        as_of=bars["open_time"].iloc[-1].to_pydatetime(),
        position=pos, entry_signal=entry, exit_signal=exit_sig,
        suggested_qty=round(qty, 6),
        suggested_stop=round(stop, 2) if stop else None,
        close=close,
        atr=round(atr, 2) if atr else None,
        slope=round(slope, 4) if slope else None,
        dry_run=True,
        note="dry-run：VT 缩放取 1.0（执行器无权益历史）；信号基于最近已收盘 bar",
    )
