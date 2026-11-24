"""执行器 API：从策略模板创建/管理运行时实例（设计决策 #9）。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import get_settings
from ...domain.timeframes import timeframe_ms
from ...infrastructure.db import get_session
from ...infrastructure.exchange.ccxt_gate import CcxtGateAdapter
from ...schemas import ExecutorCreate, ExecutorOut, ExecutorSignal, ExecutorUpdate
from ...services import executor_service as svc
from ...services.live_runner import runner as live_runner

router = APIRouter(prefix="/executors", tags=["executors"])


@router.post("", response_model=ExecutorOut, status_code=201)
async def create_executor(req: ExecutorCreate, session: AsyncSession = Depends(get_session)):
    try:
        return await svc.create_executor(session, req)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("", response_model=list[ExecutorOut])
async def list_executors(session: AsyncSession = Depends(get_session)):
    rows = await svc.list_executors(session)
    signals = await svc.latest_signal_map(session, [r.id for r in rows])
    out = []
    for r in rows:
        o = ExecutorOut.model_validate(r)
        o.latest_signal = signals.get(r.id)
        out.append(o)
    return out


@router.get("/{executor_id}", response_model=ExecutorOut)
async def get_executor(executor_id: str, session: AsyncSession = Depends(get_session)):
    m = await svc.get_executor(session, executor_id)
    if not m:
        raise HTTPException(404, "executor not found")
    return m


@router.patch("/{executor_id}", response_model=ExecutorOut)
async def update_executor(executor_id: str, req: ExecutorUpdate,
                          session: AsyncSession = Depends(get_session)):
    try:
        m = await svc.update_executor(session, executor_id, req)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if not m:
        raise HTTPException(404, "executor not found")
    await live_runner.sync_executor(m.id)   # 启停/改周期后同步调度任务
    return m


@router.delete("/{executor_id}", response_model=ExecutorOut)
async def delete_executor(executor_id: str,
                          confirm: bool = Query(False, description="停止执行器需显式 confirm=true"),
                          session: AsyncSession = Depends(get_session)):
    if not confirm:
        raise HTTPException(400, "停止执行器需显式 confirm=true")
    m = await svc.delete_executor(session, executor_id)
    if not m:
        raise HTTPException(404, "executor not found")
    await live_runner.sync_executor(m.id)
    return m


@router.get("/{executor_id}/signals")
async def executor_signals(executor_id: str, limit: int = Query(100, le=500),
                           session: AsyncSession = Depends(get_session)):
    """执行器信号/动作流水（open/close/trail_stop/hold/error/circuit_break）。"""
    return {"signals": await svc.list_signals(session, executor_id, limit)}


@router.get("/{executor_id}/bars")
async def executor_bars(executor_id: str, limit: int = Query(300, le=1000),
                        session: AsyncSession = Depends(get_session)):
    """执行器最近 N 根已收盘 K 线（K 线图 + 信号标记用）。

    与信号口径一致：丢弃进行中的最后一根 bar。
    """
    m = await svc.get_executor(session, executor_id)
    if not m:
        raise HTTPException(404, "executor not found")
    import pandas as pd

    tf_ms = timeframe_ms(m.timeframe)
    now_ms = int(pd.Timestamp.now("UTC").timestamp() * 1000)
    since_ms = now_ms - (limit + 5) * tf_ms      # 多取几根覆盖丢弃与缺口
    settings = get_settings()
    adapter = CcxtGateAdapter(settings.gate_api_key, settings.gate_api_secret,
                              settings.gate_sandbox)
    try:
        bars = await adapter.klines(m.symbol, m.timeframe, since_ms, now_ms)
    except Exception as e:
        raise HTTPException(502, f"交易所数据获取失败: {type(e).__name__}") from e
    finally:
        await adapter.close()
    if not len(bars):
        return {"bars": []}
    # 丢弃未收盘 bar
    last_open_ms = int(bars["open_time"].iloc[-1].timestamp() * 1000)
    if last_open_ms + tf_ms > now_ms:
        bars = bars.iloc[:-1]
    bars = bars.tail(limit)
    out = [{
        "t": r.open_time.isoformat(),
        "o": round(float(r.open), 8), "h": round(float(r.high), 8),
        "l": round(float(r.low), 8), "c": round(float(r.close), 8),
    } for r in bars.itertuples()]
    return {"bars": out}


@router.get("/{executor_id}/equity")
async def executor_equity(executor_id: str, limit: int = Query(2000, le=10000),
                          session: AsyncSession = Depends(get_session)):
    """执行器账面权益曲线（每 tick 一条快照：equity = cash + 未实现盈亏）。"""
    return {"points": await svc.list_equity(session, executor_id, limit)}


@router.get("/{executor_id}/signal", response_model=ExecutorSignal)
async def executor_signal(executor_id: str, session: AsyncSession = Depends(get_session)):
    """dry-run 信号快照：该执行器在下一根 bar 开盘应采取的动作。"""
    try:
        return await svc.compute_signal(session, executor_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:                       # 交易所网络错误等
        raise HTTPException(502, f"交易所数据获取失败: {type(e).__name__}") from e
