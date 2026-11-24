"""回测 API。

任务结果双轨：内存任务表（会话内，含逐笔交易）+ backtest_runs 存档表
（跨会话，含请求/指标/权益曲线）。result/equity 接口内存未命中时回源存档表，
因此服务重启后对比页仍可叠加历史回测。
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...infrastructure.db import get_session
from ...infrastructure.models import BacktestRunModel
from ...schemas import (BacktestJob, BacktestRequest, BacktestResultResponse,
                        BacktestRunSummary, SymbolMetrics)
from ...services.backtest_runner import runner

router = APIRouter(prefix="/backtests", tags=["backtests"])


@router.post("", response_model=BacktestJob, status_code=202)
async def create_backtest(req: BacktestRequest):
    job_id = await runner.submit(req)
    return BacktestJob(job_id=job_id, status="pending", created_at=datetime.now(timezone.utc))


@router.get("", response_model=list[BacktestRunSummary])
async def list_backtests(limit: int = 100,
                         session: AsyncSession = Depends(get_session)):
    """回测存档列表（新→旧），对比页的数据源。"""
    rows = (await session.execute(
        select(BacktestRunModel).order_by(BacktestRunModel.created_at.desc())
        .limit(min(limit, 500)))).scalars().all()
    out = []
    for r in rows:
        metrics = None
        s = r.summary or {}
        head = s.get("portfolio") or (s.get("per_symbol") or [None])[0]
        if head:
            metrics = SymbolMetrics(**head)
        out.append(BacktestRunSummary(
            job_id=r.id, status=r.status, strategy=r.strategy, symbols=r.symbols,
            timeframe=r.timeframe, params=r.request.get("params", {}),
            risk_frac=r.request.get("risk_frac", 0.05),
            vol_target=r.request.get("vol_target", 0.25),
            max_leverage=r.request.get("max_leverage", 3.0),
            created_at=r.created_at, metrics=metrics))
    return out


@router.get("/{job_id}", response_model=BacktestJob)
async def get_backtest(job_id: str, session: AsyncSession = Depends(get_session)):
    job = runner.jobs.get(job_id)
    if job:
        return BacktestJob(job_id=job_id, **{k: v for k, v in job.items() if k in
                                             {"status", "created_at", "finished_at", "error"}})
    row = await session.get(BacktestRunModel, job_id)
    if not row:
        raise HTTPException(404, "job not found")
    return BacktestJob(job_id=job_id, status=row.status, created_at=row.created_at,
                       finished_at=row.finished_at, error=row.error)


@router.delete("/{job_id}", status_code=204)
async def delete_backtest(job_id: str, session: AsyncSession = Depends(get_session)):
    """删除存档（含内存结果）；正在运行的任务拒绝删除。"""
    job = runner.jobs.get(job_id)
    if job and job["status"] in ("pending", "running"):
        raise HTTPException(409, f"job status: {job['status']}")
    runner.jobs.pop(job_id, None)
    runner.results.pop(job_id, None)
    await session.execute(delete(BacktestRunModel).where(BacktestRunModel.id == job_id))
    await session.commit()


@router.get("/{job_id}/result", response_model=BacktestResultResponse)
async def get_backtest_result(job_id: str, session: AsyncSession = Depends(get_session)):
    job = runner.jobs.get(job_id)
    if job and job["status"] != "done":
        raise HTTPException(409, f"job status: {job['status']}")
    summary = runner.results.get(job_id, {}).get("summary")
    if not summary:  # 内存未命中（服务重启后）：回源存档表
        row = await session.get(BacktestRunModel, job_id)
        if not row:
            raise HTTPException(404, "job not found")
        if row.status != "done":
            raise HTTPException(409, f"job status: {row.status}")
        s = row.summary or {}
        return BacktestResultResponse(
            job_id=job_id,
            per_symbol=[SymbolMetrics(**m) for m in s.get("per_symbol", [])],
            portfolio=SymbolMetrics(**s["portfolio"]) if s.get("portfolio") else None)
    return BacktestResultResponse(job_id=job_id, per_symbol=summary["per_symbol"],
                                  portfolio=summary["portfolio"])


@router.get("/{job_id}/equity")
async def get_equity(job_id: str, symbol: str = "__portfolio__",
                     session: AsyncSession = Depends(get_session)):
    res = runner.results.get(job_id, {})
    target = res.get(symbol) or next(iter(res.values()), None)
    if target and "equity" in target:
        eq = target["equity"]
        return {"points": [{"t": t.isoformat(), "v": round(v, 2)} for t, v in eq.items()]}
    # 内存未命中：回源存档表
    row = await session.get(BacktestRunModel, job_id)
    if not row:
        raise HTTPException(404, "equity not found")
    points = (row.equity or {}).get(symbol)
    if points is None and row.equity:
        points = next(iter(row.equity.values()))
    if not points:
        raise HTTPException(404, "equity not found")
    return {"points": [{"t": t, "v": v} for t, v in points]}


@router.get("/{job_id}/trades")
async def get_trades(job_id: str, symbol: str | None = None, limit: int = 200):
    res = runner.results.get(job_id, {})
    frames = []
    for sym, data in res.items():
        if sym in ("summary", "__portfolio__"):
            continue
        if symbol and sym != symbol:
            continue
        td = data["trades"].copy()
        td["symbol"] = sym
        frames.append(td)
    if not frames:
        return {"trades": []}
    import pandas as pd
    td = pd.concat(frames).sort_values("entry_time").tail(limit)
    for col in ("entry_time", "exit_time"):
        td[col] = td[col].astype(str)
    return {"trades": td.to_dict(orient="records")}
