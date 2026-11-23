"""引擎冒烟测试：对照研究验证脚本的结果，防移植漂移。"""
from pathlib import Path

import numpy as np
import pandas as pd

from app.domain.sizing import ReanchorStop, SizingConfig, position_size, vt_scalar
from app.domain.strategy import StrategyParams
from app.services.backtest_engine import BacktestEngine, CostModel
from app.strategies.ema_pullback_v1 import EmaPullbackV1

WORKSPACE = Path(__file__).resolve().parents[3]   # reanchor 的上级（研究工作区）


def _daily_bars() -> pd.DataFrame:
    raw = pd.read_csv(WORKSPACE / "btc_1h.csv", parse_dates=["open_time"]).set_index("open_time")
    return (raw.resample("1D", label="right", closed="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna().reset_index())


def test_engine_reproduces_research_baseline():
    """BTC 日线回调版 v1（5% 风险 + VT25%）年化应接近研究值 +23.4%（±3pp 容差）。"""
    engine = BacktestEngine(
        strategy=EmaPullbackV1(), params=StrategyParams({}),
        sizing=SizingConfig(risk_frac=0.05, vol_target=0.25, max_leverage=3.0, bars_per_year=365.0),
        cost=CostModel(fee=0.0005, slippage=0.0002),
        initial_equity=10_000.0)
    result = engine.run(_daily_bars())
    assert abs(result.metrics["annualized"] - 0.234) < 0.03, result.metrics
    assert result.metrics["trades"] > 30


def test_position_size_respects_leverage_cap():
    cfg = SizingConfig(risk_frac=0.5, max_leverage=3.0)
    # 极小止损距离会触发杠杆截断
    qty = position_size(equity=10_000, entry_price=100, stop_distance=0.1, scalar=2.5, cfg=cfg)
    assert qty == 10_000 * 3.0 / 100


def test_reanchor_stop_never_below_entry_stop():
    s = ReanchorStop(side=1, entry_stop=90.0)
    assert s.update(close=100.0, atr=2.0) == 94.0      # 重锚抬高（100 − 3×2）
    assert s.update(close=80.0, atr=2.0) == 90.0       # 深回撤不破底线
    assert s.hit(high=100, low=89.0)
    assert not s.hit(high=100, low=91.0)


def test_reanchor_v2_peak_trailing_until_aligned():
    """v2：连续同向未满 N 根时峰值跟踪（棘轮只进不退），满 N 根后按收盘重锚。"""
    s = ReanchorStop(side=1, entry_stop=90.0, min_aligned=3)
    assert s.update(close=100.0, atr=2.0, aligned=True) == 94.0   # peak=100 → 100−6
    assert s.update(close=99.0, atr=2.0, aligned=True) == 94.0    # peak 仍 100，棘轮不动
    assert s.update(close=97.0, atr=2.0, aligned=False) == 94.0   # 反向：计数清零，仍棘轮
    assert s.aligned_bars == 0
    assert s.update(close=101.0, atr=2.0, aligned=True) == 95.0   # peak=101 → 101−6
    # 空头镜像
    sh = ReanchorStop(side=-1, entry_stop=110.0, min_aligned=2)
    assert sh.update(close=100.0, atr=2.0, aligned=True) == 106.0
    assert sh.update(close=99.0, atr=2.0, aligned=True) == 105.0  # 满 2 根 → 收盘重锚 99+6
    # v2 棘轮语义：同向满 N 根后价格回落不再放松止损（v1 会重锚回落）
    s3 = ReanchorStop(side=1, entry_stop=90.0, min_aligned=2)
    s3.update(close=100.0, atr=2.0, aligned=True)                     # 94（峰值跟踪）
    assert s3.update(close=104.0, atr=2.0, aligned=True) == 98.0      # 满 2 根：104−6
    assert s3.update(close=96.0, atr=2.0, aligned=True) == 98.0       # 回落不放松（v1 会回 90）


def test_engine_v2_mode_runs():
    """reanchor_min_bars=3（v2）锚定研究基准：BTC 年化 +25.1%（±3pp）。"""
    engine = BacktestEngine(
        strategy=EmaPullbackV1(), params=StrategyParams({"reanchor_min_bars": 3}),
        sizing=SizingConfig(risk_frac=0.05, vol_target=0.25, max_leverage=3.0, bars_per_year=365.0),
        cost=CostModel(fee=0.0005, slippage=0.0002),
        initial_equity=10_000.0)
    result = engine.run(_daily_bars())
    assert abs(result.metrics["annualized"] - 0.251) < 0.03, result.metrics
    assert result.metrics["trades"] > 20


if __name__ == "__main__":
    test_engine_reproduces_research_baseline()
    test_position_size_respects_leverage_cap()
    test_reanchor_stop_never_below_entry_stop()
    test_reanchor_v2_peak_trailing_until_aligned()
    test_engine_v2_mode_runs()
    print("all smoke tests passed")
