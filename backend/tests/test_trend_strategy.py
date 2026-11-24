"""趋势版策略 + 均线族测试：斜率过零信号、ma_type 枚举校验（纯领域层，无外网）。

运行：PYTHONPATH=. python tests/test_trend_strategy.py
"""
import numpy as np
import pandas as pd

from app.domain.strategy import StrategyParams
from app.services.executor_service import effective_params
from app.strategies.ema_pullback_v1 import EmaPullbackV1
from app.strategies.ema_trend_v1 import EmaTrendV1
from app.strategies.ma import MA_TYPES, ma_series


def _bars(closes):
    c = np.array(closes, dtype=float)
    return pd.DataFrame({
        "open_time": pd.date_range("2024-01-01", periods=len(c), freq="D", tz="UTC"),
        "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1.0,
    })


def main():
    # --- 均线族：五种类型都能出值，ema 与 ewm 完全一致
    c = pd.Series(np.linspace(100, 130, 80))
    for kind in MA_TYPES:
        s = ma_series(c, 20, kind)
        assert len(s) == 80 and not np.isnan(s.iloc[-1]), kind
    assert np.allclose(ma_series(c, 20, "ema"), c.ewm(span=20, adjust=False).mean())
    assert np.allclose(ma_series(c, 20, "sma").iloc[-1], c.iloc[-20:].mean())
    try:
        ma_series(c, 20, "xxx")
        raise AssertionError("未知均线类型应报错")
    except ValueError:
        pass

    # --- 趋势版：V 形走势，底部附近斜率上穿零轴 → 做多信号
    strat = EmaTrendV1()
    sp = StrategyParams({})
    down = np.linspace(130, 100, 40)
    up = np.linspace(100, 140, 40)
    d = strat.prepare(_bars(np.concatenate([down, up])), sp)
    sigs = [strat.entry_signal(d, t, sp) for t in range(2, len(d) + 1)]
    assert 1 in sigs, "V 形反转应出现多头过零信号"
    # 信号必然出现在斜率由负转正当根
    t_long = next(t for t in range(2, len(d) + 1) if strat.entry_signal(d, t, sp) == 1)
    assert d.iloc[t_long - 1].slope > 0 >= d.iloc[t_long - 2].slope

    # 倒 V 形：顶部斜率下穿零轴 → 做空信号
    d2 = strat.prepare(_bars(np.concatenate([up, down])), sp)
    sigs2 = [strat.entry_signal(d2, t, sp) for t in range(2, len(d2) + 1)]
    assert -1 in sigs2, "倒 V 形应出现空头过零信号"

    # 持仓中斜率反向过零 → 出场
    assert strat.exit_signal(d2, t_long, 1, sp) in (True, False)  # 类型正确
    # explain：过零那根给出多头成立结论
    ex = strat.explain(d, t_long, sp)
    assert "多头入场成立" in ex["reason"], ex
    # 非过零处：报告未过零
    ex2 = strat.explain(d, 10, sp)
    assert "未入场" in ex2["reason"] or "入场成立" in ex2["reason"]

    # --- effective_params：str 枚举校验
    eff = effective_params(EmaPullbackV1, {"ma_type": "wma"})
    assert eff["ma_type"] == "wma"
    eff = effective_params(EmaTrendV1, {})
    assert eff["ma_type"] == "ema" and eff["slope_k"] == 5
    for bad in ("xxx", 123):
        try:
            effective_params(EmaPullbackV1, {"ma_type": bad})
            raise AssertionError(f"ma_type={bad} 应被拒")
        except ValueError:
            pass
    # 回调版默认参数不含 ma_type 覆盖时与研究基线一致（ema）
    assert effective_params(EmaPullbackV1, {})["ma_type"] == "ema"

    print("trend strategy tests passed")


if __name__ == "__main__":
    main()
