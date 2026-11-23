# ATR止损优化测试：现状(peak锚定) vs 斜率同向时重算(close锚定) vs 持续3日才重锚
import numpy as np
import pandas as pd

EMA_N, SLOPE_K = 20, 8
ATR_N, ATR_MULT = 14, 3.0
RISK_FRAC = 0.05
VOL_TARGET, VOL_LOOKBACK, VOL_CAP, VOL_FLOOR = 0.25, 20, 2.5, 0.25
MAX_LEVERAGE = 3.0
FEE, SLIP = 0.0005, 0.0002
INITIAL = 10_000.0
WARMUP = 60

def run(csv, variant):
    df = pd.read_csv(csv, parse_dates=["open_time"]).set_index("open_time")
    d = df.resample("1D", label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna().reset_index()
    c = d["close"]
    d["ema"] = c.ewm(span=EMA_N, adjust=False).mean()
    d["slope"] = d["ema"] - d["ema"].shift(SLOPE_K)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - c.shift(1)).abs(),
                    (d["low"] - c.shift(1)).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / ATR_N, adjust=False).mean()
    sig_long = (c.shift(1) > d["ema"].shift(1)) & (d["low"] <= d["ema"]) & (c > d["ema"]) & (d["slope"] > 0)
    sig_short = (c.shift(1) < d["ema"].shift(1)) & (d["high"] >= d["ema"]) & (c < d["ema"]) & (d["slope"] < 0)

    n = len(d)
    equity, pos, entry_px, stop, peak = INITIAL, 0.0, 0.0, np.nan, np.nan
    entry_stop = np.nan
    eq_curve = np.full(n, np.nan)
    eq_ret_hist, trades = [], []
    aligned_days = 0

    def record(i, px, reason):
        nonlocal equity, pos
        pnl = pos * (px - entry_px) - abs(pos) * px * FEE
        equity += pnl
        trades.append((pnl, reason))
        pos = 0.0

    for t in range(WARMUP, n):
        row = d.iloc[t]
        o, h, l, cl = row.open, row.high, row.low, row.close
        if equity <= 0:
            break
        sig_exit = (pos > 0 and d.slope.iloc[t - 1] < 0) or (pos < 0 and d.slope.iloc[t - 1] > 0)
        if pos != 0 and sig_exit:
            record(t, o * (1 - SLIP) if pos > 0 else o * (1 + SLIP), "exit")
        if pos == 0 and equity > 0:
            direction = 1 if sig_long.iloc[t - 1] else (-1 if sig_short.iloc[t - 1] else 0)
            if direction != 0:
                dist = ATR_MULT * d.atr.iloc[t - 1]
                fill = o * (1 + SLIP) if direction > 0 else o * (1 - SLIP)
                scalar = 1.0
                if len(eq_ret_hist) >= VOL_LOOKBACK:
                    sig = float(np.std(eq_ret_hist[-VOL_LOOKBACK:]) * np.sqrt(365))
                    if sig > 0:
                        scalar = min(max(VOL_TARGET / sig, VOL_FLOOR), VOL_CAP)
                qty = min(equity * RISK_FRAC * scalar / dist, equity * MAX_LEVERAGE / fill)
                if qty > 0:
                    equity -= qty * fill * FEE
                    pos, entry_px = qty * direction, fill
                    peak = cl
                    stop = (cl - dist) if direction > 0 else (cl + dist)
                    entry_stop = stop
                    aligned_days = 1
        # 盘中止损
        if pos != 0:
            hit = (pos > 0 and l <= stop) or (pos < 0 and h >= stop)
            if hit:
                record(t, (min(o, stop) * (1 - SLIP)) if pos > 0 else (max(o, stop) * (1 + SLIP)), "atr_stop")
        # 收盘后更新止损
        if pos != 0:
            aligned = (pos > 0 and d.slope.iloc[t] > 0) or (pos < 0 and d.slope.iloc[t] < 0)
            aligned_days = aligned_days + 1 if aligned else 0
            dist_now = ATR_MULT * d.atr.iloc[t]
            if variant == "v0":  # 现状：peak 锚定，只进不退
                if pos > 0:
                    peak = max(peak, cl)
                    stop = max(stop, peak - dist_now)
                else:
                    peak = min(peak, cl)
                    stop = min(stop, peak + dist_now)
            elif variant == "v1":  # 斜率同向则每日以当前收盘重锚（不低于初始止损）
                if pos > 0:
                    new_stop = cl - dist_now
                    stop = max(entry_stop, new_stop)
                else:
                    new_stop = cl + dist_now
                    stop = min(entry_stop, new_stop)
            elif variant == "v2":  # 斜率持续同向>=3天才重锚，否则沿用 peak 跟踪
                if pos > 0:
                    peak = max(peak, cl)
                    trail = peak - dist_now
                    if aligned_days >= 3:
                        stop = max(entry_stop, max(stop, cl - dist_now), trail) if cl - dist_now < trail else max(stop, trail)
                        stop = max(entry_stop, max(stop, cl - dist_now))
                    else:
                        stop = max(stop, trail)
                else:
                    peak = min(peak, cl)
                    trail = peak + dist_now
                    if aligned_days >= 3:
                        stop = min(entry_stop, min(stop, cl + dist_now))
                    else:
                        stop = min(stop, trail)
        eq_curve[t] = equity + pos * (cl - entry_px) if pos != 0 else equity
        if t > WARMUP and not np.isnan(eq_curve[t - 1]) and eq_curve[t - 1] > 0:
            eq_ret_hist.append(eq_curve[t] / eq_curve[t - 1] - 1)

    eq = pd.Series(eq_curve).ffill().dropna()
    years = (d.open_time.iloc[-1] - d.open_time.iloc[WARMUP]).days / 365.25
    ann = (eq.iloc[-1] / INITIAL) ** (1 / years) - 1
    rets = eq.pct_change().dropna()
    sharpe = rets.mean() / rets.std() * np.sqrt(365)
    mdd = (eq / eq.cummax() - 1).min()
    n_stop = sum(1 for _, r in trades if r == "atr_stop")
    return ann, sharpe, mdd, len(trades), n_stop

print(f"{'变体':<26}{'三品种年化':>9}{'Sharpe':>8}{'MaxDD':>9}{'年笔数':>7}{'止损出场占比':>10}   单品种年化")
for variant, label in [("v0", "现状 peak锚定"), ("v1", "v1 同向每日重锚"), ("v2", "v2 持续3日才重锚")]:
    anns, sharpes, mdds, trs, stops = [], [], [], [], []
    for sym in ["btc", "ethusdt", "solusdt"]:
        a, s, m, t, ns = run(f"{sym}_1h.csv", variant)
        anns.append(a); sharpes.append(s); mdds.append(m); trs.append(t); stops.append(ns)
    print(f"{label:<24}{np.mean(anns):>+9.1%}{np.mean(sharpes):>8.2f}{np.mean(mdds):>9.1%}"
          f"{np.mean(trs)/3.7:>7.0f}{np.mean(stops)/max(np.mean(trs),1):>10.0%}   "
          f"{' / '.join(f'{a:+.1%}' for a in anns)}")
