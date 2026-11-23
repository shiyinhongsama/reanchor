# v1 重锚止损的最终组合级验证：三子账户等权 + 定投现金流，v0 vs v1
import numpy as np
import pandas as pd

src = open("atr_reset_test.py", encoding="utf-8").read()
exec(src.split("print(f")[0])  # 载入 run() 与参数

# 让 run() 返回权益曲线：重打包一个带 eq 返回的版本
import types

def run_with_eq(csv, variant):
    # 复制 atr_reset_test.run 并在末尾返回权益序列
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
    eq_ret_hist = []
    aligned_days = 0

    def record(i, px):
        nonlocal equity, pos
        pnl = pos * (px - entry_px) - abs(pos) * px * FEE
        equity += pnl
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
                qty = min(equity * RISK_FRAC * scalar / dist, equity * MAX_LEVERAGE / fill)
                if qty > 0:
                    equity -= qty * fill * FEE
                    pos, entry_px = qty * direction, fill
                    peak = cl
                    stop = (cl - dist) if direction > 0 else (cl + dist)
                    entry_stop = stop
        if pos != 0:
            hit = (pos > 0 and l <= stop) or (pos < 0 and h >= stop)
            if hit:
                record(t, (min(o, stop) * (1 - SLIP)) if pos > 0 else (max(o, stop) * (1 + SLIP)))
        if pos != 0:
            dist_now = ATR_MULT * d.atr.iloc[t]
            if variant == "v0":
                if pos > 0:
                    peak = max(peak, cl)
                    stop = max(stop, peak - dist_now)
                else:
                    peak = min(peak, cl)
                    stop = min(stop, peak + dist_now)
            else:  # v1
                if pos > 0:
                    stop = max(entry_stop, cl - dist_now)
                else:
                    stop = min(entry_stop, cl + dist_now)
        eq_curve[t] = equity + pos * (cl - entry_px) if pos != 0 else equity
        if t > WARMUP and not np.isnan(eq_curve[t - 1]) and eq_curve[t - 1] > 0:
            eq_ret_hist.append(eq_curve[t] / eq_curve[t - 1] - 1)
    return pd.Series(eq_curve, index=d["open_time"]).ffill().dropna()

def portfolio_stats(eq, label):
    ret = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    ann = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
    sharpe = ret.mean() / ret.std() * np.sqrt(365)
    mdd = (eq / eq.cummax() - 1).min()
    print(f"  [{label} 一次性] 年化 {ann:+.1%} | Sharpe {sharpe:.2f} | MaxDD {mdd:.1%} | Calmar {ann/abs(mdd):.2f}")
    return dict(ann=ann, sharpe=sharpe, mdd=mdd)

def dca(eq, label):
    ret = eq.pct_change().fillna(0.0)
    e, eqs, cfs = 1000.0, [], [(ret.index[0], 1000.0)]
    month_seen, total_in = None, 1000.0
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
    xirr = (lo + hi) / 2
    unit = [1.0]; prev = s.iloc[0]
    for i in range(1, len(s)):
        flow = 500.0 if s.index[i].month != s.index[i-1].month else 0.0
        unit.append(unit[-1] * (s.iloc[i] - flow) / prev)
        prev = s.iloc[i]
    unit = pd.Series(unit, index=s.index)
    udd = (unit / unit.cummax() - 1).min()
    inv_line = 1000 + 500 * np.arange(len(s)) / ((len(s) - 1) / 43.0)
    below = (s.values < inv_line).mean()
    print(f"  [{label} 定投] 投入 {total_in:,.0f} U | 期末 {s.iloc[-1]:,.0f} U | XIRR {xirr:+.1%} | "
          f"净值MaxDD {udd:.1%} | 低于本金 {below:.0%}")
    return dict(xirr=xirr, udd=udd, below=below)

SYMS = ["btc", "ethusdt", "solusdt"]
print("=== 主区间 2023-03 ~ 2026-11（纯回调 5% + VT25%，三子账户等权）===")
for v in ("v0", "v1"):
    eqs = [run_with_eq(f"{sy}_1h.csv", v) for sy in SYMS]
    port = sum(eqs) / 3
    portfolio_stats(port, v)
    dca(port, v)

print("\n=== 熊市压力 2020–2022 ===")
for v in ("v0", "v1"):
    eqs = [run_with_eq(f"{sy}_1h_bear.csv", v) for sy in SYMS]
    port = sum(eqs) / 3
    portfolio_stats(port, v)
