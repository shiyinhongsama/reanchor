# 时间尺度检验：12h 与 2日 级别（回调版 + v1重锚 + 5% + VT25%）
import numpy as np
import pandas as pd

ATR_N, ATR_MULT = 14, 3.0
RISK = 0.05
VOL_TARGET, VOL_LOOKBACK, VOL_CAP, VOL_FLOOR = 0.25, 20, 2.5, 0.25
MAX_LEVERAGE = 3.0
FEE, SLIP = 0.0005, 0.0002
INITIAL = 10_000.0
WARMUP = 60
# 年化因子：12h 每天 2 根，1D 每天 1 根，2D 每两天 1 根
ANN = {"12h": 365 * 2, "1D": 365, "2D": 365 / 2}

def run(csv, rule, ema_n, slope_k, risk=RISK):
    df = pd.read_csv(csv, parse_dates=["open_time"]).set_index("open_time")
    d = df.resample(rule, label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna().reset_index()
    c = d["close"]
    d["ema"] = c.ewm(span=ema_n, adjust=False).mean()
    d["slope"] = d["ema"] - d["ema"].shift(slope_k)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - c.shift(1)).abs(),
                    (d["low"] - c.shift(1)).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / ATR_N, adjust=False).mean()
    sig_long = (c.shift(1) > d["ema"].shift(1)) & (d["low"] <= d["ema"]) & (c > d["ema"]) & (d["slope"] > 0)
    sig_short = (c.shift(1) < d["ema"].shift(1)) & (d["high"] >= d["ema"]) & (c < d["ema"]) & (d["slope"] < 0)
    n = len(d)
    equity, pos, entry_px, stop, entry_stop = INITIAL, 0.0, 0.0, np.nan, np.nan
    eq_curve = np.full(n, np.nan)
    eq_ret_hist = []

    def record(i, px):
        nonlocal equity, pos
        equity += pos * (px - entry_px) - abs(pos) * px * FEE
        pos = 0.0

    for t in range(WARMUP, n):
        row = d.iloc[t]
        o, h, l, cl = row.open, row.high, row.low, row.close
        if equity <= 0:
            break
        sig_exit = (pos > 0 and d.slope.iloc[t - 1] < 0) or (pos < 0 and d.slope.iloc[t - 1] > 0)
        if pos != 0 and sig_exit:
            record(t, o * (1 - SLIP) if pos > 0 else o * (1 + SLIP))
        if pos == 0 and equity > 0:
            direction = 1 if sig_long.iloc[t - 1] else (-1 if sig_short.iloc[t - 1] else 0)
            if direction != 0:
                dist = ATR_MULT * d.atr.iloc[t - 1]
                fill = o * (1 + SLIP) if direction > 0 else o * (1 - SLIP)
                scalar = 1.0
                if len(eq_ret_hist) >= VOL_LOOKBACK:
                    sig = float(np.std(eq_ret_hist[-VOL_LOOKBACK:]) * np.sqrt(ANN[rule]))
                    if sig > 0:
                        scalar = min(max(VOL_TARGET / sig, VOL_FLOOR), VOL_CAP)
                qty = min(equity * risk * scalar / dist, equity * MAX_LEVERAGE / fill)
                if qty > 0:
                    equity -= qty * fill * FEE
                    pos, entry_px = qty * direction, fill
                    stop = (cl - dist) if direction > 0 else (cl + dist)
                    entry_stop = stop
        if pos != 0:
            hit = (pos > 0 and l <= stop) or (pos < 0 and h >= stop)
            if hit:
                record(t, (min(o, stop) * (1 - SLIP)) if pos > 0 else (max(o, stop) * (1 + SLIP)))
        if pos != 0:
            dist_now = ATR_MULT * d.atr.iloc[t]
            if pos > 0:
                stop = max(entry_stop, cl - dist_now)
            else:
                stop = min(entry_stop, cl + dist_now)
        eq_curve[t] = equity + pos * (cl - entry_px) if pos != 0 else equity
        if t > WARMUP and not np.isnan(eq_curve[t - 1]) and eq_curve[t - 1] > 0:
            eq_ret_hist.append(eq_curve[t] / eq_curve[t - 1] - 1)
    eq = pd.Series(eq_curve, index=d["open_time"]).ffill().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    ann = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
    rets = eq.pct_change().dropna()
    sharpe = rets.mean() / rets.std() * np.sqrt(ANN[rule])
    mdd = (eq / eq.cummax() - 1).min()
    return eq, ann, sharpe, mdd

SYMS = ["btc", "ethusdt", "solusdt"]
for rule in ("12h", "2D"):
    print(f"===== {rule} 级别 =====")
    # EMA20/k8 点位
    anns = []
    for sym in SYMS:
        eq, a, s, m = run(f"{sym}_1h.csv", rule, 20, 8)
        anns.append(a)
        print(f"  {sym:<10} EMA20/k8: 年化 {a:+.1%} | Sharpe {s:.2f} | MaxDD {m:.1%}")
    # 9格平台
    pos = 0; all_anns = []
    for ema_n in (15, 20, 30):
        for k in (3, 5, 8):
            cell = []
            for sym in SYMS:
                eq, a, s, m = run(f"{sym}_1h.csv", rule, ema_n, k)
                cell.append(a)
            cell_mean = np.mean(cell)
            all_anns.append(cell_mean)
            pos += sum(1 for a in cell if a > 0)
    print(f"  平台: {pos}/27 格为正（三品种×9参数）| 格均值中位 {np.median(all_anns):+.1%} | "
          f"区间 [{min(all_anns):+.1%}, {max(all_anns):+.1%}]")
    # 组合
    ports = [run(f"{sym}_1h.csv", rule, 20, 8)[0] for sym in SYMS]
    port = pd.concat(ports, axis=1).ffill().bfill().mean(axis=1)
    ret = port.pct_change().dropna()
    years = (port.index[-1] - port.index[0]).days / 365.25
    pann = (port.iloc[-1] / port.iloc[0]) ** (1 / years) - 1
    psharpe = ret.mean() / ret.std() * np.sqrt(ANN[rule])
    pmdd = (port / port.cummax() - 1).min()
    print(f"  三品种组合 EMA20/k8: 年化 {pann:+.1%} | Sharpe {psharpe:.2f} | MaxDD {pmdd:.1%} | Calmar {pann/abs(pmdd):.2f}")
    print()
