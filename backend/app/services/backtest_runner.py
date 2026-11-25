"""回测任务运行器：内存任务表 + 后台执行（一期单机版；二期可换 Celery/Redis）。

单次回测与参数扫描共用 _execute 引擎路径；扫描的 K 线按 品种×周期×区间 只拉取
一次，全部组合复用（组合间只是策略/定仓参数不同，输入数据完全一致）。
"""
from __future__ import annotations

import asyncio
import itertools
import uuid
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ..core.config import get_settings
from ..domain.sizing import SizingConfig
from ..domain.strategy import StrategyParams
from ..domain.timeframes import TIMEFRAMES
from ..infrastructure.db import SessionLocal
from ..infrastructure.exchange.ccxt_gate import CcxtGateAdapter
from ..infrastructure.models import BacktestRunModel
from ..schemas import (BacktestRequest, SWEEP_MAX_COMBOS, SweepRequest,
                       SymbolMetrics, WalkForwardRequest)
from ..strategies.ema_pullback_v1 import EmaPullbackV1
from ..strategies.ema_trend_v1 import EmaTrendV1
from . import bars_service
from .backtest_engine import BacktestEngine, CostModel

STRATEGIES = {"ema_pullback_v1": EmaPullbackV1, "ema_trend_v1": EmaTrendV1}


def _ms(dt) -> int:
    ts = pd.Timestamp(dt)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return int(ts.timestamp() * 1000)


def expand_sweep(req: SweepRequest) -> list[BacktestRequest]:
    """把扫描请求展开为回测请求列表（risk × vt × param_grid 笛卡尔积）。"""
    keys = [k for k, v in req.param_grid.items() if v]
    grids = [req.param_grid[k] for k in keys]
    combos: list[BacktestRequest] = []
    for risk in (req.risk_fracs or [0.05]):
        for vt in (req.vol_targets or [0.25]):
            for vals in (itertools.product(*grids) if grids else [()]):
                # param_grid 声明为 float 列表；int 型参数（如 slope_k）需收整，
                # 否则策略内切片/rolling 会因 float 索引报错
                params = {k: int(v) if isinstance(v, float) and float(v).is_integer() else v
                          for k, v in zip(keys, vals)}
                combos.append(BacktestRequest(
                    strategy=req.strategy, symbols=req.symbols, timeframe=req.timeframe,
                    start=req.start, end=req.end, params=params,
                    risk_frac=risk, vol_target=vt, max_leverage=req.max_leverage,
                    fee=req.fee, slippage=req.slippage, initial_equity=req.initial_equity,
                    deposit_amount=req.deposit_amount,
                    deposit_every_days=req.deposit_every_days))
    return combos


# 走步验证：窗口前额外带的预热 bar 数（覆盖 EMA200 + slope_k + ATR）
_WF_WARMUP = 260


class BacktestRunner:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.results: dict[str, dict] = {}
        self.sweeps: dict[str, dict] = {}
        self.wfs: dict[str, dict] = {}

    async def submit(self, req: BacktestRequest) -> str:
        job_id = uuid.uuid4().hex[:12]
        self.jobs[job_id] = {"status": "pending", "created_at": datetime.now(timezone.utc)}
        asyncio.create_task(self._run(job_id, req))
        return job_id

    async def submit_sweep(self, req: SweepRequest) -> tuple[str, int]:
        combos = expand_sweep(req)
        if len(combos) > SWEEP_MAX_COMBOS:
            raise ValueError(f"组合数 {len(combos)} 超过上限 {SWEEP_MAX_COMBOS}")
        sweep_id = uuid.uuid4().hex[:12]
        self.sweeps[sweep_id] = {"status": "running", "total": len(combos), "done": 0,
                                 "job_ids": [], "created_at": datetime.now(timezone.utc)}
        asyncio.create_task(self._run_sweep(sweep_id, req, combos))
        return sweep_id, len(combos)

    async def submit_walkforward(self, req: WalkForwardRequest) -> str:
        keys = [k for k, v in req.param_grid.items() if v]
        n_combo = 1
        for k in keys:
            n_combo *= len(req.param_grid[k])
        if n_combo > SWEEP_MAX_COMBOS:
            raise ValueError(f"每折组合数 {n_combo} 超过上限 {SWEEP_MAX_COMBOS}")
        wf_id = uuid.uuid4().hex[:12]
        self.wfs[wf_id] = {"status": "running", "created_at": datetime.now(timezone.utc)}
        asyncio.create_task(self._run_walkforward(wf_id, req))
        return wf_id

    async def _run_walkforward(self, wf_id: str, req: WalkForwardRequest) -> None:
        """走步验证：顺序窗口 → 训练段寻优 → 样本外测试段评估 → 拼接 OOS 净值。"""
        wf = self.wfs[wf_id]
        try:
            settings = get_settings()
            adapter = CcxtGateAdapter(settings.gate_api_key, settings.gate_api_secret,
                                      settings.gate_sandbox)
            since = _ms(req.start or "2023-01-01")
            until = _ms(req.end) if req.end else _ms(pd.Timestamp.now("UTC"))
            try:
                bars = await bars_service.get_bars(SessionLocal, adapter, req.symbol,
                                                   req.timeframe, since, until)
            finally:
                await adapter.close()

            strat_cls = STRATEGIES[req.strategy]
            _, bpy = TIMEFRAMES[req.timeframe]
            n = len(bars)
            win = n // req.folds
            if win < 100:
                raise ValueError(f"数据 {n} 根，每折仅 {win} 根，窗口太短（减少折数或拉长区间）")

            # 参数组合（空网格 = 固定参数单组合）
            keys = [k for k, v in req.param_grid.items() if v]
            combos = []
            for vals in (itertools.product(*(req.param_grid[k] for k in keys)) if keys else [()]):
                p = dict(req.params)
                p.update({k: int(v) if isinstance(v, float) and float(v).is_integer() else v
                          for k, v in zip(keys, vals)})
                combos.append(p)

            sizing = SizingConfig(risk_frac=req.risk_frac, vol_target=req.vol_target,
                                  max_leverage=req.max_leverage, bars_per_year=bpy)
            cost = CostModel(fee=req.fee, slippage=req.slippage)

            def slice_run(i0: int, i1: int, params: dict):
                """独立窗口回测：带预热前缀起跑，净值与交易裁到 [i0, i1)。"""
                pre = max(0, i0 - _WF_WARMUP)
                engine = BacktestEngine(strat_cls(), StrategyParams(params),
                                        sizing, cost, req.initial_equity)
                res = engine.run(bars.iloc[pre:i1])
                lo = bars["open_time"].iloc[i0]
                eq = res.equity[res.equity.index >= lo]
                td = res.trades
                if len(td):
                    td = td[pd.to_datetime(td["entry_time"], utc=True) >= lo]
                metrics = engine._metrics(eq, td) if len(eq) >= 2 else {}
                return eq, metrics

            folds, oos_parts = [], []
            for i in range(req.folds):
                w0 = i * win
                w1 = (i + 1) * win if i < req.folds - 1 else n
                tr1 = w0 + int((w1 - w0) * req.train_frac)
                # 训练段寻优（单组合时即固定参数评估）
                scored = []
                for p in combos:
                    _, m = slice_run(w0, tr1, p)
                    v = m.get(req.criterion) if m else None
                    scored.append((v if v is not None else float("-inf"), p, m))
                scored.sort(key=lambda x: x[0], reverse=True)
                _, best_p, train_m = scored[0]
                # 样本外测试段（平地起跑）
                test_eq, test_m = slice_run(tr1, w1, best_p)
                folds.append({
                    "i": i,
                    "train": [bars["open_time"].iloc[w0].isoformat(),
                              bars["open_time"].iloc[tr1 - 1].isoformat()],
                    "test": [bars["open_time"].iloc[tr1].isoformat(),
                             bars["open_time"].iloc[w1 - 1].isoformat()],
                    "params": best_p, "train_metrics": train_m, "test_metrics": test_m,
                })
                oos_parts.append(test_eq)
                wf["done"] = i + 1

            # 拼接样本外净值（逐折复利）
            mult = 1.0
            stitched: dict = {}
            for eq in oos_parts:
                if len(eq) < 2:
                    continue
                for t, r in eq.pct_change().dropna().items():
                    mult *= 1 + float(r)
                    stitched[t] = mult * req.initial_equity
            oos = pd.Series(stitched).sort_index()
            oos_metrics = BacktestEngine(strat_cls(), StrategyParams({}),
                                         SizingConfig(bars_per_year=bpy),
                                         CostModel())._metrics(oos, pd.DataFrame()) \
                if len(oos) >= 2 else {}

            anns = [f["test_metrics"].get("annualized") for f in folds
                    if f["test_metrics"].get("annualized") is not None]
            sharpes = [f["test_metrics"].get("sharpe", 0.0) for f in folds if f["test_metrics"]]
            dds = [f["test_metrics"].get("max_drawdown", 0.0) for f in folds if f["test_metrics"]]
            aggregate = {
                "profitable_folds": f"{sum(1 for a in anns if a > 0)}/{len(anns)}",
                "mean_annualized": float(np.mean(anns)) if anns else None,
                "median_annualized": float(np.median(anns)) if anns else None,
                "std_annualized": float(np.std(anns)) if anns else None,
                "mean_sharpe": float(np.mean(sharpes)) if sharpes else None,
                "worst_drawdown": float(min(dds)) if dds else None,
            }
            wf.update({"status": "done", "finished_at": datetime.now(timezone.utc),
                       "result": {
                           "criterion": req.criterion,
                           "folds": folds, "aggregate": aggregate,
                           "oos_metrics": oos_metrics,
                           "oos_equity": [{"t": t.isoformat(), "v": round(float(v), 2)}
                                          for t, v in oos.items()],
                       }})
            await self._persist_walkforward(wf_id, req)
        except Exception as e:  # noqa: BLE001 - 任务失败落错误信息
            import traceback
            wf.update({"status": "failed",
                       "error": f"{e}\n{traceback.format_exc(limit=3)}",
                       "finished_at": datetime.now(timezone.utc)})

    async def _persist_walkforward(self, wf_id: str, req: WalkForwardRequest) -> None:
        """走步验证 OOS 拼接结果存入回测存档表（策略名带 ·WF 后缀），
        对比页可与单次回测曲线叠加。与单次回测同一套读取路径（equity/result/bars）。"""
        try:
            wf = self.wfs[wf_id]
            result = wf.get("result") or {}
            oos = result.get("oos_metrics") or {}
            if not oos:
                return
            summary_json = {"per_symbol": [{"symbol": req.symbol, **oos}],
                            "portfolio": None}
            row = BacktestRunModel(
                id=wf_id, status="done", strategy=f"{req.strategy}·WF",
                symbols=[req.symbol], timeframe=req.timeframe,
                request={**req.model_dump(mode="json"), "symbols": [req.symbol]},
                summary=summary_json,
                equity={req.symbol: [[p["t"], p["v"]] for p in result["oos_equity"]]},
                created_at=wf["created_at"], finished_at=wf.get("finished_at"))
            async with SessionLocal() as session:
                await session.merge(row)
                await session.commit()
        except Exception:  # noqa: BLE001 - 存档失败不影响内存态结果
            pass

    async def _fetch_bars(self, req) -> dict[str, pd.DataFrame]:
        """按请求拉取全部品种 K 线（原生周期直拉；非原生周期由适配层重采样）。"""
        settings = get_settings()
        adapter = CcxtGateAdapter(settings.gate_api_key, settings.gate_api_secret,
                                  settings.gate_sandbox)
        since = _ms(req.start or "2023-01-01")
        until = _ms(req.end) if req.end else _ms(pd.Timestamp.now("UTC"))
        try:
            bars = {}
            for symbol in req.symbols:
                bars[symbol] = await bars_service.get_bars(
                    SessionLocal, adapter, symbol, req.timeframe, since, until)
            return bars
        finally:
            await adapter.close()

    async def _run(self, job_id: str, req: BacktestRequest) -> None:
        self.jobs[job_id]["status"] = "running"
        try:
            bars = await self._fetch_bars(req)
            self._execute(job_id, req, bars)
            self.jobs[job_id].update({"status": "done", "finished_at": datetime.now(timezone.utc)})
        except Exception as e:  # noqa: BLE001 - 任务失败需落错误信息
            self.jobs[job_id].update({"status": "failed", "error": str(e),
                                      "finished_at": datetime.now(timezone.utc)})
        await self._persist(job_id, req)

    async def _run_sweep(self, sweep_id: str, req: SweepRequest,
                         combos: list[BacktestRequest]) -> None:
        sw = self.sweeps[sweep_id]
        try:
            bars = await self._fetch_bars(req)          # 全部组合共用一份 K 线
        except Exception as e:  # noqa: BLE001 - 拉数失败则整个扫描失败
            sw.update({"status": "done", "error": str(e)})
            return
        for combo_req in combos:
            job_id = uuid.uuid4().hex[:12]
            self.jobs[job_id] = {"status": "running", "created_at": datetime.now(timezone.utc)}
            sw["job_ids"].append(job_id)
            try:
                self._execute(job_id, combo_req, bars)
                self.jobs[job_id].update({"status": "done",
                                          "finished_at": datetime.now(timezone.utc)})
            except Exception as e:  # noqa: BLE001 - 单组失败不中断其余组合
                self.jobs[job_id].update({"status": "failed", "error": str(e),
                                          "finished_at": datetime.now(timezone.utc)})
            await self._persist(job_id, combo_req)
            sw["done"] += 1
        sw["status"] = "done"

    def _execute(self, job_id: str, req: BacktestRequest,
                 bars: dict[str, pd.DataFrame]) -> None:
        """逐品种跑引擎 + 组合等权净值，结果写入内存任务表（同步、纯计算）。"""
        strat_cls = STRATEGIES[req.strategy]
        _, bpy = TIMEFRAMES[req.timeframe]
        per_symbol, curves = [], {}
        for symbol in req.symbols:
            engine = BacktestEngine(
                strategy=strat_cls(), params=StrategyParams(req.params),
                sizing=SizingConfig(risk_frac=req.risk_frac, vol_target=req.vol_target,
                                    max_leverage=req.max_leverage, bars_per_year=bpy),
                cost=CostModel(fee=req.fee, slippage=req.slippage),
                initial_equity=req.initial_equity,
                deposit_amount=req.deposit_amount,
                deposit_every_days=req.deposit_every_days)
            result = engine.run(bars[symbol])
            per_symbol.append(SymbolMetrics(symbol=symbol, **result.metrics))
            # 组合净值：定投模式用 nav（剔除入金），否则用账户权益
            curves[symbol] = result.nav if result.nav is not None else result.equity
            self.results.setdefault(job_id, {})[symbol] = {
                "trades": result.trades, "equity": result.equity}
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

    async def _persist(self, job_id: str, req: BacktestRequest) -> None:
        """任务结束（成功/失败）后存档到 backtest_runs 表，供对比页跨会话查询。"""
        try:
            job = self.jobs[job_id]
            res = self.results.get(job_id, {})
            equity = {}
            for sym, data in res.items():
                if sym == "summary":
                    continue
                eq = data.get("equity")
                if eq is not None:
                    equity[sym] = [[t.isoformat(), round(float(v), 2)] for t, v in eq.items()]
            summary_json: dict = {}
            summary = res.get("summary")
            if summary:
                summary_json = {
                    "per_symbol": [m.model_dump() for m in summary["per_symbol"]],
                    "portfolio": summary["portfolio"].model_dump() if summary["portfolio"] else None,
                }
            row = BacktestRunModel(
                id=job_id, status=job["status"], error=job.get("error"),
                strategy=req.strategy, symbols=req.symbols, timeframe=req.timeframe,
                request=req.model_dump(mode="json"), summary=summary_json, equity=equity,
                created_at=job["created_at"], finished_at=job.get("finished_at"))
            async with SessionLocal() as session:
                await session.merge(row)
                await session.commit()
        except Exception:  # noqa: BLE001 - 存档失败不影响任务本身的结果
            pass


runner = BacktestRunner()
