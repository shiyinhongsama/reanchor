"""策略模板与实盘 API（一期骨架）。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import get_settings
from ...infrastructure.db import get_session
from ...infrastructure.exchange.ccxt_gate import CcxtGateAdapter
from ...schemas import LiveOrderRequest, LiveStatus
from ...services import executor_service as svc
from ...services.backtest_runner import STRATEGIES
from ...services.live_runner import runner as live_runner

router = APIRouter(tags=["strategies", "live"])


@router.get("/strategies")
async def list_strategies():
    """策略模板列表：含参数 schema（执行器创建时按此校验）。"""
    return {"strategies": [
        {"name": cls.name,
         "description": (cls.__doc__ or "").strip().splitlines()[0] if cls.__doc__ else cls.name,
         "params": getattr(cls, "params_schema", {})}
        for cls in STRATEGIES.values()]}


@router.get("/live/status", response_model=LiveStatus)
async def live_status(session: AsyncSession = Depends(get_session)):
    settings = get_settings()
    running = await svc.count_by_status(session, "running")
    return LiveStatus(enabled=settings.live_enabled,
                      running_executors=running, open_positions=[])


@router.get("/live/scheduler")
async def scheduler_status():
    """调度器任务列表：每个 running 执行器的下一次触发时刻。"""
    return {"jobs": live_runner.jobs()}


@router.post("/live/orders")
async def submit_order(req: LiveOrderRequest):
    """手动下单（一期仅 dry_run；真实下单需 TP_LIVE_ENABLED 且实网联调）。"""
    settings = get_settings()
    if not req.dry_run and not settings.live_enabled:
        raise HTTPException(403, "实盘总开关未开启（TP_LIVE_ENABLED=false），仅支持 dry_run")
    adapter = CcxtGateAdapter(settings.gate_api_key, settings.gate_api_secret,
                              settings.gate_sandbox)
    try:
        return await adapter.place_order(req.symbol, req.side, 0.0, dry_run=req.dry_run)
    finally:
        await adapter.close()
