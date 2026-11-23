# 最终配置（回调+v1+VT25%）下不同风险比例的定投表现
import numpy as np
import pandas as pd

EMA_N, SLOPE_K = 20, 8
ATR_N, ATR_MULT = 14, 3.0
VOL_TARGET, VOL_LOOKBACK, VOL_CAP, VOL_FLOOR = 0.25, 20, 2.5, 0.25
MAX_LEVERAGE = 3.0
FEE, SLIP = 0.0005, 0.0002
INITIAL = 10_000.0
WARMUP = 60

def run(csv, risk):
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
    equity, pos, entry_px, stop = INITIAL, 0.0, 0.0, np.nan
    entry_stop = np.nan
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
                    sig = float(np.std(eq_ret_hist[-VOL_LOOKBACK:]) * np.sqrt(365))
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
        if pos != 0:  # v1 重锚
            dist_now = ATR_MULT * d.atr.iloc[t]
            if pos > 0:
                stop = max(entry_stop, cl - dist_now)
            else:
                stop = min(entry_stop, cl + dist_now)
        eq_curve[t] = equity + pos * (cl - entry_px) if pos != 0 else equity
        if t > WARMUP and not np.isnan(eq_curve[t - 1]) and eq_curve[t - 1] > 0:
            eq_ret_hist.append(eq_curve[t] / eq_curve[t - 1] - 1)
    return pd.Series(eq_curve, index=d["open_time"]).ffill().dropna()

SYMS = ["btc", "ethusdt", "solusdt"]

def dca_stats(port):
    ret = port.pct_change().fillna(0.0)
    e, cfs, month_seen, total_in = 1000.0, [(ret.index[0], 1000.0)], None, 1000.0
    eqs = []
    for t, r in ret.items():
        e *= (1 + r)
        if t.month != month_seen and t != ret.index[0]:
            e += 500.0; total_in += 500.0; cfs.append((t, 500.0))
        month_seen = t.month
        eqs.append(e)
    s = pd.Series(eqs, index=ret.index)
    cfs.append((ret.index[-1], -e))
    def xnpv(rate):
        t0 = cfs[0][0]
        return sum(cf / (1 + rate) ** ((t - t0).days / 365.25) for t, cf in cfs)
    lo, hi = -0.99, 10.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if xnpv(mid) > 0: hi = mid
        else: lo = mid
    unit = [1.0]; prev = s.iloc[0]
    for i in range(1, len(s)):
        flow = 500.0 if s.index[i].month != s.index[i-1].month else 0.0
        unit.append(unit[-1] * (s.iloc[i] - flow) / prev)
        prev = s.iloc[i]
    unit = pd.Series(unit, index=s.index)
    udd = (unit / unit.cummax() - 1).min()
    inv_line = 1000 + 500 * np.arange(len(s)) / ((len(s) - 1) / 43.0)
    below = (s.values < inv_line).mean()
    return s, total_in, (lo + hi) / 2, udd, below

print(f"{'风险':>6} {'一次性年化':>9} {'一次性MaxDD':>10} | {'定投期末':>9} {'XIRR':>8} {'净值MaxDD':>9} {'低于本金':>8}")
results = {}
for risk in (0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15):
    eqs = [run(f"{sy}_1h.csv", risk) for sy in SYMS]
    port = pd.concat(eqs, axis=1).ffill().bfill().mean(axis=1)
    ret = port.pct_change().dropna()
    years = (port.index[-1] - port.index[0]).days / 365.25
    ann = (port.iloc[-1] / port.iloc[0]) ** (1 / years) - 1
    mdd = (port / port.cummax() - 1).min()
    s, total_in, xirr, udd, below = dca_stats(port)
    results[risk] = s
    print(f"{risk:>6.1%} {ann:>+9.1%} {mdd:>10.1%} | {s.iloc[-1]:>9,.0f} {xirr:>+8.1%} {udd:>9.1%} {below:>8.0%}")

# 绘图
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
for f in font_manager.findSystemFonts():
    if "msyh" in f.lower() or "simhei" in f.lower():
        font_manager.fontManager.addfont(f)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=f).get_name()
        break
plt.rcParams["axes.unicode_minus"] = False
fig, ax = plt.subplots(figsize=(9.5, 5.2))
cmap = plt.cm.viridis(np.linspace(0.05, 0.85, len(results)))
for (risk, s), cc in zip(results.items(), cmap):
    ax.plot(s.index, s.values, lw=2.0 if risk in (0.02, 0.05, 0.15) else 1.1,
            color=cc, label=f"{risk:.1%}（期末 {s.iloc[-1]:,.0f} U）")
inv_line = 1000 + 500 * np.arange(len(list(results.values())[0])) / ((len(list(results.values())[0]) - 1) / 43.0)
ax.plot(list(results.values())[0].index, inv_line, color="black", lw=1.0, alpha=0.5, label="累计投入本金")
ax.set_ylabel("账户权益（USDT）")
ax.set_title("最终配置（回调 + v1 重锚 + VT25%）：不同风险比例的定投资金曲线（1,000 U + 每月 500 U）", fontsize=10.5)
ax.legend(fontsize=8, frameon=False, loc="upper left")
ax.grid(alpha=0.3)
fig.tight_layout()
fig.savefig("dca_risk_scale_v1.png", dpi=170)
print("saved dca_risk_scale_v1.png")
