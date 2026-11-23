"""回测 API。"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from ...schemas import BacktestJob, BacktestRequest, BacktestResultResponse
from ...services.backtest_runner import runner

router = APIRouter(prefix="/backtests", tags=["backtests"])


@router.post("", response_model=BacktestJob, status_code=202)
async def create_backtest(req: BacktestRequest):
    job_id = await runner.submit(req)
    return BacktestJob(job_id=job_id, status="pending", created_at=datetime.now(timezone.utc))


@router.get("/{job_id}", response_model=BacktestJob)
async def get_backtest(job_id: str):
    job = runner.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return BacktestJob(job_id=job_id, **{k: v for k, v in job.items() if k in
                                         {"status", "created_at", "finished_at", "error"}})


@router.get("/{job_id}/result", response_model=BacktestResultResponse)
async def get_backtest_result(job_id: str):
    job = runner.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job["status"] != "done":
        raise HTTPException(409, f"job status: {job['status']}")
    summary = runner.results[job_id]["summary"]
    return BacktestResultResponse(job_id=job_id, per_symbol=summary["per_symbol"],
                                  portfolio=summary["portfolio"])


@router.get("/{job_id}/equity")
async def get_equity(job_id: str, symbol: str = "__portfolio__"):
    res = runner.results.get(job_id, {})
    target = res.get(symbol) or next(iter(res.values()), None)
    if not target or "equity" not in target:
        raise HTTPException(404, "equity not found")
    eq = target["equity"]
    return {"points": [{"t": t.isoformat(), "v": round(v, 2)} for t, v in eq.items()]}


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
