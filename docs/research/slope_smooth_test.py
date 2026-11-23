# 斜率平滑对比：raw / EMA3平滑 / EMA5平滑 / 连续2日确认 —— 回调版 5%+VT25%
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
    raw_slope = d["ema"] - d["ema"].shift(SLOPE_K)
    if variant == "sm3":
        d["slope"] = raw_slope.ewm(span=3, adjust=False).mean()
    elif variant == "sm5":
        d["slope"] = raw_slope.ewm(span=5, adjust=False).mean()
    else:
        d["slope"] = raw_slope
    tr = pd.concat([d["high"] - d["low"], (d["high"] - c.shift(1)).abs(),
                    (d["low"] - c.shift(1)).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / ATR_N, adjust=False).mean()

    sig_long = (c.shift(1) > d["ema"].shift(1)) & (d["low"] <= d["ema"]) & (c > d["ema"]) & (d["slope"] > 0)
    sig_short = (c.shift(1) < d["ema"].shift(1)) & (d["high"] >= d["ema"]) & (c < d["ema"]) & (d["slope"] < 0)

    n = len(d)
    equity, pos, entry_px, stop, peak = INITIAL, 0.0, 0.0, np.nan, np.nan
    eq_curve = np.full(n, np.nan)
    eq_ret_hist, trades = [], []
    flip_count = 0  # 连续反向计数（conf2 用）

    def record(i, px, reason):
        nonlocal equity, pos
        pnl = pos * (px - entry_px) - abs(pos) * px * FEE
        equity += pnl
        trades.append(pnl)
        pos = 0.0

    for t in range(WARMUP, n):
        row = d.iloc[t]
        o, h, l, cl = row.open, row.high, row.low, row.close
        if equity <= 0:
            break
        if variant == "conf2":
            # 连续两日反向才出场
            if pos > 0:
                flip_count = flip_count + 1 if d.slope.iloc[t - 1] < 0 else 0
            elif pos < 0:
                flip_count = flip_count + 1 if d.slope.iloc[t - 1] > 0 else 0
            sig_exit = pos != 0 and flip_count >= 2
        else:
            sig_exit = (pos > 0 and d.slope.iloc[t - 1] < 0) or (pos < 0 and d.slope.iloc[t - 1] > 0)
        if pos != 0 and sig_exit:
            record(t, o * (1 - SLIP) if pos > 0 else o * (1 + SLIP), "exit")
            flip_count = 0
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
                    flip_count = 0
        if pos != 0:
            hit = (pos > 0 and l <= stop) or (pos < 0 and h >= stop)
            if hit:
                record(t, (min(o, stop) * (1 - SLIP)) if pos > 0 else (max(o, stop) * (1 + SLIP)), "atr_stop")
        if pos != 0:
            if pos > 0:
                peak = max(peak, cl)
                stop = max(stop, peak - ATR_MULT * d.atr.iloc[t])
            else:
                peak = min(peak, cl)
                stop = min(stop, peak + ATR_MULT * d.atr.iloc[t])
        eq_curve[t] = equity + pos * (cl - entry_px) if pos != 0 else equity
        if t > WARMUP and not np.isnan(eq_curve[t - 1]) and eq_curve[t - 1] > 0:
            eq_ret_hist.append(eq_curve[t] / eq_curve[t - 1] - 1)

    eq = pd.Series(eq_curve).ffill().dropna()
    years = (d.open_time.iloc[-1] - d.open_time.iloc[WARMUP]).days / 365.25
    ann = (eq.iloc[-1] / INITIAL) ** (1 / years) - 1
    rets = eq.pct_change().dropna()
    sharpe = rets.mean() / rets.std() * np.sqrt(365)
    mdd = (eq / eq.cummax() - 1).min()
    return ann, sharpe, mdd, len(trades)

# 斜率平滑度统计（BTC）
df = pd.read_csv("btc_1h.csv", parse_dates=["open_time"]).set_index("open_time")
d = df.resample("1D", label="right", closed="right").agg({"close": "last"}).dropna()
ema = d["close"].ewm(span=20, adjust=False).mean()
raw = ema - ema.shift(8)
years = (d.index[-1] - d.index[0]).days / 365.25
for name, s in [("原始斜率", raw), ("EMA3 平滑", raw.ewm(span=3, adjust=False).mean()),
                ("EMA5 平滑", raw.ewm(span=5, adjust=False).mean())]:
    signs = np.sign(s.dropna())
    crosses = (signs != signs.shift()).sum() - 1
    print(f"{name}: 每年过零 {crosses / years:.1f} 次，正值区段平均持续 {(signs > 0).sum() / max((signs != signs.shift()).sum() / 2, 1):.0f} 天")
print("=" * 78)

for variant in ["raw", "sm3", "sm5", "conf2"]:
    anns, sharpes, mdds, trs = [], [], [], []
    for sym in ["btc", "ethusdt", "solusdt"]:
        a, s, m, tr = run(f"{sym}_1h.csv", variant)
        anns.append(a); sharpes.append(s); mdds.append(m); trs.append(tr)
    print(f"{variant:<6} 三品种均值: 年化 {np.mean(anns):+.1%} | Sharpe {np.mean(sharpes):.2f} | "
          f"MaxDD {np.mean(mdds):.1%} | 年交易 {np.mean(trs) / 3.7:.0f} 笔   "
          f"(单品种年化: {' / '.join(f'{a:+.1%}' for a in anns)})")
