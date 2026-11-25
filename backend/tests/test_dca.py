"""定投（DCA）测试：份额法记账——入金折算份额，绩效剔除入金影响。

注意：nav 并非与入金金额无关——持仓期间入金以现金待在场外，稀释敞口，
这是真实定投行为而非缺陷。正确性锚点：入金趋零时 nav 收敛到不定投基线。
"""
from pathlib import Path

import numpy as np
import pandas as pd

from app.domain.sizing import SizingConfig
from app.domain.strategy import StrategyParams
from app.services.backtest_engine import BacktestEngine, CostModel
from app.strategies.ema_pullback_v1 import EmaPullbackV1

WORKSPACE = Path(__file__).resolve().parents[3]


def _daily_bars() -> pd.DataFrame:
    raw = pd.read_csv(WORKSPACE / "btc_1h.csv", parse_dates=["open_time"]).set_index("open_time")
    return (raw.resample("1D", label="right", closed="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna().reset_index())


def _run(deposit_amount: float, deposit_every_days: int = 30) -> object:
    engine = BacktestEngine(
        strategy=EmaPullbackV1(), params=StrategyParams({}),
        sizing=SizingConfig(risk_frac=0.05, vol_target=0.25, max_leverage=3.0, bars_per_year=365.0),
        cost=CostModel(fee=0.0005, slippage=0.0002),
        initial_equity=1_000.0,
        deposit_amount=deposit_amount, deposit_every_days=deposit_every_days)
    return engine.run(_daily_bars())


def test_no_deposit_unchanged():
    """deposit_amount=0 时与原行为完全一致：无 nav、无入金字段，年化锚定基线。"""
    res = _run(0.0)
    assert res.nav is None
    assert "total_deposited" not in res.metrics
    assert abs(res.metrics["annualized"] - 0.234) < 0.03, res.metrics


def test_deposits_counted():
    """入金次数 = 区间内完整间隔数；净利润 = 终值 - 初始 - 累计入金。"""
    res = _run(1_000.0, 30)
    m = res.metrics
    bars = _daily_bars()
    span_days = (bars.open_time.iloc[-1] - bars.open_time.iloc[60]).days
    expected = span_days // 30
    assert m["total_deposited"] == expected * 1_000.0
    final = res.equity.iloc[-1]
    assert abs(m["net_profit"] - (final - 1_000.0 - m["total_deposited"])) < 1e-6
    # 账户终值应明显大于纯交易结果（入金进了账户）
    assert final > 1_000.0 + m["total_deposited"]


def test_nav_continuous_to_tiny_deposit():
    """ plumbing 连续性：入金金额趋零时，nav 绩效应收敛到不定投基线。"""
    base = _run(0.0)
    tiny = _run(1e-4, 30)
    assert abs(tiny.metrics["annualized"] - base.metrics["annualized"]) < 0.01
    assert abs(tiny.metrics["sharpe"] - base.metrics["sharpe"]) < 0.05


def test_metrics_use_nav_not_account():
    """绩效口径是 nav（剔除入金）：total_return 等于 nav 曲线涨幅，而非账户倍数。"""
    dca = _run(1_000.0, 30)
    nav_ret = dca.nav.iloc[-1] / dca.nav.iloc[0] - 1
    assert abs(dca.metrics["total_return"] - nav_ret) < 1e-9
    account_growth = dca.equity.iloc[-1] / 1_000.0 - 1
    assert account_growth > dca.metrics["total_return"] + 1.0   # 大量入金推高账户倍数


def test_deposit_buoys_blown_account():
    """空仓权益为负时定投继续运行不终止（等下一笔入金）。"""
    bars = _daily_bars()
    engine = BacktestEngine(
        strategy=EmaPullbackV1(), params=StrategyParams({}),
        sizing=SizingConfig(risk_frac=0.5, vol_target=0.0, max_leverage=10.0, bars_per_year=365.0),
        cost=CostModel(fee=0.0005, slippage=0.0002),
        initial_equity=100.0,
        deposit_amount=10_000.0, deposit_every_days=30)
    res = engine.run(bars)
    # 不论是否爆过仓，最终都应正常产出指标而非中断在空数据
    assert res.metrics and res.metrics["trades"] >= 0
    assert res.metrics["total_deposited"] > 0


if __name__ == "__main__":
    test_no_deposit_unchanged()
    test_deposits_counted()
    test_nav_continuous_to_tiny_deposit()
    test_metrics_use_nav_not_account()
    test_deposit_buoys_blown_account()
    print("dca tests passed")
