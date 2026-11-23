"""回测任务运行器：内存任务表 + 后台执行（一期单机版；二期可换 Celery/Redis）。"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pandas as pd

from ..core.config import get_settings
from ..domain.sizing import SizingConfig
from ..domain.strategy import StrategyParams
from ..domain.timeframes import TIMEFRAMES
from ..infrastructure.exchange.ccxt_gate import CcxtGateAdapter
from ..schemas import BacktestRequest, SymbolMetrics
from ..strategies.ema_pullback_v1 import EmaPullbackV1
from .backtest_engine import BacktestEngine, CostModel

STRATEGIES = {"ema_pullback_v1": EmaPullbackV1}


class BacktestRunner:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.results: dict[str, dict] = {}

    async def submit(self, req: BacktestRequest) -> str:
        job_id = uuid.uuid4().hex[:12]
        self.jobs[job_id] = {"status": "pending", "created_at": datetime.now(timezone.utc)}
        asyncio.create_task(self._run(job_id, req))
        return job_id

    async def _run(self, job_id: str, req: BacktestRequest) -> None:
        self.jobs[job_id]["status"] = "running"
        try:
            settings = get_settings()
            adapter = CcxtGateAdapter(settings.gate_api_key, settings.gate_api_secret,
                                      settings.gate_sandbox)
            strat_cls = STRATEGIES[req.strategy]
            _, bpy = TIMEFRAMES[req.timeframe]

            def _ms(dt) -> int:
                ts = pd.Timestamp(dt)
                if ts.tzinfo is None:
                    ts = ts.tz_localize("UTC")
                return int(ts.timestamp() * 1000)

            since = _ms(req.start or "2023-01-01")
            until = _ms(req.end) if req.end else _ms(pd.Timestamp.now("UTC"))
            per_symbol, curves = [], {}
            try:
                for symbol in req.symbols:
                    # 原生周期直拉；非原生周期（2d）由适配层内重采样
                    bars = await adapter.klines(symbol, req.timeframe, since, until)
                    engine = BacktestEngine(
                        strategy=strat_cls(), params=StrategyParams(req.params),
                        sizing=SizingConfig(risk_frac=req.risk_frac, vol_target=req.vol_target,
                                            max_leverage=req.max_leverage, bars_per_year=bpy),
                        cost=CostModel(fee=req.fee, slippage=req.slippage),
                        initial_equity=req.initial_equity)
                    result = engine.run(bars)
                    per_symbol.append(SymbolMetrics(symbol=symbol, **result.metrics))
                    curves[symbol] = result.equity
                    self.results.setdefault(job_id, {})[symbol] = {
                        "trades": result.trades, "equity": result.equity}
            finally:
                await adapter.close()
            portfolio = None
            if len(curves) > 1:
                port = pd.concat(curves, axis=1).ffill().bfill().mean(axis=1)
                engine = BacktestEngine(strat_cls(), StrategyParams(req.params),
                                        SizingConfig(bars_per_year=bpy), CostModel())
                # 组合指标：净值用各品种等权平均；笔数/盈亏比合并全部交易计算
                all_trades = pd.concat(
                    [r["trades"] for k, r in self.results.get(job_id, {}).items()
                     if k != "__portfolio__" and isinstance(r.get("trades"), pd.DataFrame)
                     and len(r["trades"])],
                    ignore_index=True) if any(
                        len(r.get("trades", [])) for k, r in self.results.get(job_id, {}).items()
                        if k != "__portfolio__") else pd.DataFrame()
                portfolio = SymbolMetrics(symbol="__portfolio__", **engine._metrics(port, all_trades))
                self.results[job_id]["__portfolio__"] = {"equity": port}
            self.results[job_id]["summary"] = {"per_symbol": per_symbol, "portfolio": portfolio}
            self.jobs[job_id].update({"status": "done", "finished_at": datetime.now(timezone.utc)})
        except Exception as e:  # noqa: BLE001 - 任务失败需落错误信息
            self.jobs[job_id].update({"status": "failed", "error": str(e),
                                      "finished_at": datetime.now(timezone.utc)})


runner = BacktestRunner()
