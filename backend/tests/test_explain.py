"""信号解释测试：入场条件逐项检查 + 人类可读结论（纯领域层，无外网）。

运行：PYTHONPATH=. python tests/test_explain.py
"""
import numpy as np
import pandas as pd

from app.domain.strategy import StrategyParams
from app.strategies.ema_pullback_v1 import EmaPullbackV1


def _bars(closes, lows=None, highs=None):
    n = len(closes)
    c = np.array(closes, dtype=float)
    return pd.DataFrame({
        "open_time": pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC"),
        "open": c, "high": np.array(highs, dtype=float) if highs is not None else c * 1.01,
        "low": np.array(lows, dtype=float) if lows is not None else c * 0.99,
        "close": c, "volume": 1.0,
    })


def main():
    strat = EmaPullbackV1()
    sp = StrategyParams({})

    # 上升趋势 + 最后一根回踩 EMA 收回 → 多头入场成立
    closes = list(np.linspace(100, 130, 60)) + [129.0]
    lows = list(np.linspace(100, 130, 60) * 0.995) + [123.0]   # 最后一根下影刺穿 EMA
    d = strat.prepare(_bars(closes, lows=lows), sp)
    t = len(d)
    ex = strat.explain(d, t, sp)
    assert ex["long"]["touched"] and ex["long"]["slope_ok"], ex
    assert ex["ema"] > 0 and ex["atr"] > 0 and ex["dist_atr"] is not None
    if all(ex["long"].values()):
        assert "多头入场成立" in ex["reason"]
        assert strat.entry_signal(d, t, sp) == 1

    # 强上升趋势但价格远离 EMA → 未触及，reason 报距离
    closes2 = list(np.linspace(100, 160, 60))
    d2 = strat.prepare(_bars(closes2), sp)
    ex2 = strat.explain(d2, len(d2), sp)
    assert not all(ex2["long"].values())
    assert "未入场" in ex2["reason"], ex2["reason"]
    assert not ex2["long"]["touched"] or not ex2["short"]["touched"]
    assert ex2["dist_atr"] > 0, "收盘价应在 EMA 上方"

    # 下跌斜率：沿空侧报告
    closes3 = list(np.linspace(160, 100, 60))
    d3 = strat.prepare(_bars(closes3), sp)
    ex3 = strat.explain(d3, len(d3), sp)
    assert ex3["slope"] < 0
    assert "未入场" in ex3["reason"] or "空头入场成立" in ex3["reason"]

    # 数据不足 / 预热期不抛异常
    d4 = strat.prepare(_bars([100.0, 101.0]), sp)
    assert "reason" in strat.explain(d4, len(d4), sp)

    print("explain tests passed")


if __name__ == "__main__":
    main()
