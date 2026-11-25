"""回测 API。

任务结果双轨：内存任务表（会话内，含逐笔交易）+ backtest_runs 存档表
（跨会话，含请求/指标/权益曲线）。result/equity 接口内存未命中时回源存档表，
因此服务重启后对比页仍可叠加历史回测。
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import get_settings
from ...infrastructure.db import SessionLocal, get_session
from ...infrastructure.exchange.ccxt_gate import CcxtGateAdapter
from ...infrastructure.models import BacktestRunModel
from ...services import bars_service
from ...schemas import (BacktestJob, BacktestRequest, BacktestResultResponse,
                        BacktestRunSummary, CostSensitivityJob,
                        CostSensitivityRequest, SweepJob, SweepRequest,
                        SymbolMetrics, WalkForwardRequest)
from ...services.backtest_runner import STRATEGIES, runner

router = APIRouter(prefix="/backtests", tags=["backtests"])


@router.post("", response_model=BacktestJob, status_code=202)
async def create_backtest(req: BacktestRequest):
    job_id = await runner.submit(req)
    return BacktestJob(job_id=job_id, status="pending", created_at=datetime.now(timezone.utc))


@router.post("/sweep", response_model=SweepJob, status_code=202)
async def create_sweep(req: SweepRequest):
    """参数扫描：网格展开为多次回测顺序执行，每组自动存档进对比页。"""
    if req.strategy not in STRATEGIES:
        raise HTTPException(400, f"unknown strategy: {req.strategy}")
    schema = STRATEGIES[req.strategy].params_schema
    for key, values in req.param_grid.items():
        if key not in schema:
            raise HTTPException(400, f"unknown param: {key}（可选：{', '.join(schema)}）")
        lo, hi = schema[key].get("min"), schema[key].get("max")
        bad = [v for v in values if (lo is not None and v < lo) or (hi is not None and v > hi)]
        if bad:
            raise HTTPException(400, f"param {key} 越界 {bad}（允许 {lo}~{hi}）")
    try:
        sweep_id, total = await runner.submit_sweep(req)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    sw = runner.sweeps[sweep_id]
    return SweepJob(sweep_id=sweep_id, status=sw["status"], total=total, done=0,
                    job_ids=[], created_at=sw["created_at"])


@router.post("/cost-sensitivity", response_model=CostSensitivityJob, status_code=202)
async def create_costsens(req: CostSensitivityRequest):
    """成本敏感性：固定参数按 费率×滑点 网格重跑，检验策略对成本的耐受度。"""
    if req.strategy not in STRATEGIES:
        raise HTTPException(400, f"unknown strategy: {req.strategy}")
    try:
        cs_id, total = await runner.submit_costsens(req)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    cs = runner.costsens[cs_id]
    return CostSensitivityJob(cs_id=cs_id, status=cs["status"], total=total, done=0,
                              created_at=cs["created_at"])


@router.get("/cost-sensitivity/{cs_id}")
async def get_costsens(cs_id: str):
    """成本敏感性状态与结果（内存态，重启后失效）。"""
    cs = runner.costsens.get(cs_id)
    if not cs:
        raise HTTPException(404, "cost sensitivity not found（状态仅存内存）")
    return {"cs_id": cs_id, **cs}


@router.post("/walkforward", status_code=202)
async def submit_walkforward(req: WalkForwardRequest):
    """走步验证：区间切折 → 训练段寻优 → 样本外测试段评估。"""
    if req.strategy not in STRATEGIES:
        raise HTTPException(422, f"unknown strategy: {req.strategy}")
    try:
        wf_id = await runner.submit_walkforward(req)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {"wf_id": wf_id}


@router.get("/walkforward/{wf_id}")
async def get_walkforward(wf_id: str):
    """走步验证状态与结果（内存态，重启后失效）。"""
    wf = runner.wfs.get(wf_id)
    if not wf:
        raise HTTPException(404, "walkforward not found（状态仅存内存）")
    return {"wf_id": wf_id, **wf}


@router.get("/sweeps/{sweep_id}", response_model=SweepJob)
async def get_sweep(sweep_id: str):
    sw = runner.sweeps.get(sweep_id)
    if not sw:
        raise HTTPException(404, "sweep not found（扫描状态仅存内存，重启后查存档列表即可）")
    return SweepJob(sweep_id=sweep_id, status=sw["status"], total=sw["total"],
                    done=sw["done"], job_ids=sw["job_ids"], created_at=sw["created_at"])


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


@router.get("/{job_id}/bars")
async def get_bars(job_id: str, symbol: str, limit: int = 1500,
                   session: AsyncSession = Depends(get_session)):
    """回测区间的 K 线（K 线图 + 买卖点标记用）。

    按存档请求的品种/周期/区间重拉 Gate 公开行情；与引擎口径一致，
    丢弃最后一根未收盘 bar。
    """
    row = await session.get(BacktestRunModel, job_id)
    if not row:
        raise HTTPException(404, "job not found")
    req = row.request or {}
    if symbol not in (req.get("symbols") or []):
        raise HTTPException(404, f"symbol {symbol} not in this backtest")
    import pandas as pd

    def _ms(dt) -> int:
        ts = pd.Timestamp(dt)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return int(ts.timestamp() * 1000)

    since = _ms(req.get("start") or "2023-01-01")
    until = _ms(req["end"]) if req.get("end") else _ms(pd.Timestamp.now("UTC"))
    settings = get_settings()
    adapter = CcxtGateAdapter(settings.gate_api_key, settings.gate_api_secret,
                              settings.gate_sandbox)
    try:
        bars = await bars_service.get_bars(SessionLocal, adapter, symbol,
                                           row.timeframe, since, until)
    finally:
        await adapter.close()
    if not len(bars):
        return {"bars": []}
    # 丢弃未收盘 bar：最后 bar 的开盘时间 + 周期长度（按相邻间隔推断）超过当前即未收盘
    if len(bars) > 1:
        eff_ms = int((bars["open_time"].iloc[-1] - bars["open_time"].iloc[-2])
                     .total_seconds() * 1000)
        last_open_ms = int(bars["open_time"].iloc[-1].timestamp() * 1000)
        if last_open_ms + eff_ms > int(pd.Timestamp.now("UTC").timestamp() * 1000):
            bars = bars.iloc[:-1]
    bars = bars.tail(min(limit, 5000))
    out = [{
        "t": row_.open_time.isoformat(),
        "o": round(float(row_.open), 8), "h": round(float(row_.high), 8),
        "l": round(float(row_.low), 8), "c": round(float(row_.close), 8),
    } for row_ in bars.itertuples()]
    return {"bars": out}


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
