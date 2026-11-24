"""均线族：策略模板的均线类型可配置（研究阶段 ma_type 对比的移植）。

全部只用历史数据（ewm/rolling 无未来函数），与研究脚本口径一致。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MA_TYPES = ("ema", "sma", "wma", "dema", "tema")


def ma_series(c: pd.Series, n: int, kind: str) -> pd.Series:
    """收盘价序列的 n 周期均线。kind ∈ MA_TYPES。"""
    if kind == "ema":
        return c.ewm(span=n, adjust=False).mean()
    if kind == "sma":
        return c.rolling(n).mean()
    if kind == "wma":
        w = np.arange(1, n + 1, dtype=float)
        return c.rolling(n).apply(lambda x: np.dot(x, w) / w.sum(), raw=True)
    if kind == "dema":
        e1 = c.ewm(span=n, adjust=False).mean()
        e2 = e1.ewm(span=n, adjust=False).mean()
        return 2 * e1 - e2
    if kind == "tema":
        e1 = c.ewm(span=n, adjust=False).mean()
        e2 = e1.ewm(span=n, adjust=False).mean()
        e3 = e2.ewm(span=n, adjust=False).mean()
        return 3 * e1 - 3 * e2 + e3
    raise ValueError(f"未知均线类型 {kind}，可选：{MA_TYPES}")
