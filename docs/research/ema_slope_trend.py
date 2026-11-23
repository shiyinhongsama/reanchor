"""
纯趋势跟踪版: EMA 斜率过零入场, 斜率反转出场(可反手), 无 ATR 止损
- 入场(多): bar t-1 收盘时 slope 由 <=0 变为 >0 -> bar t 开盘做多
- 出场: 斜率反向过零, 下一根开盘离场并允许立即反手
- 仓位与 pullback 版一致: 1% 风险 / (3*ATR 参考距离), 3 倍杠杆上限, 便于两版对比
防前视: 信号用 bar t-1 收盘数据, 成交在 bar t 开盘价
用法: python ema_slope_trend.py [周期小时数] [EMA_N] [SLOPE_K] [标签] [CSV]
"""
import os
import sys
import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

BAR_H = int(sys.argv[1]) if len(sys.argv) > 1 else 24
EMA_N = int(sys.argv[2]) if len(sys.argv) > 2 else 20
SLOPE_K = int(sys.argv[3]) if len(sys.argv) > 3 else 5
TAG = sys.argv[4] if len(sys.argv) > 4 else f"{BAR_H}h_ema{EMA_N}_k{SLOPE_K}"
CSV = sys.argv[5] if len(sys.argv) > 5 else "btc_1h.csv"
ATR_N = 14
ATR_REF_MULT = 3.0
RISK_FRAC = float(os.environ.get("RISK_FRAC", "0.01"))
MAX_LEVERAGE = 3.0
FEE = 0.0005
SLIP = 0.0002
FUNDING_PER_8H = 0.0001
INITIAL_EQUITY = 10_000.0
WARMUP = max(EMA_N + SLOPE_K + 20, 60)
VOL_TARGET = float(os.environ.get("VOL_TARGET", "0"))
VOL_LOOKBACK = int(os.environ.get("VOL_LOOKBACK", "20"))
VOL_CAP, VOL_FLOOR = 2.5, 0.25
eq_ret_hist = []

df = pd.read_csv(CSV, parse_dates=["open_time"]).reset_index(drop=True)
if BAR_H > 1:
    df = df.set_index("open_time").resample(f"{BAR_H}h", label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna().reset_index()

MA_TYPE = os.environ.get("MA_TYPE", "ema")

def ma_series(c, n, kind):
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
    if kind == "hma":
        half = max(int(n / 2), 1)
        sqrtn = max(int(round(np.sqrt(n))), 1)
        w = lambda m: np.arange(1, m + 1, dtype=float)
        wma = lambda x, m: x.rolling(m).apply(lambda v: np.dot(v, w(m)) / w(m).sum(), raw=True)
        return wma(2 * wma(c, half) - wma(c, n), sqrtn)
    raise ValueError(kind)

c = df["close"]
df["ema"] = ma_series(c, EMA_N, MA_TYPE)
raw_slope = df["ema"] - df["ema"].shift(SLOPE_K)
SLOPE_SMOOTH = int(os.environ.get("SLOPE_SMOOTH", "0"))
df["slope"] = raw_slope.ewm(span=SLOPE_SMOOTH, adjust=False).mean() if SLOPE_SMOOTH > 1 else raw_slope
tr = pd.concat([
    df["high"] - df["low"],
    (df["high"] - c.shift(1)).abs(),
    (df["low"] - c.shift(1)).abs(),
], axis=1).max(axis=1)
df["atr"] = tr.ewm(alpha=1 / ATR_N, adjust=False).mean()

# 斜率过零信号 (bar t 收盘时成立)
cross_up = (df["slope"] > 0) & (df["slope"].shift(1) <= 0)
cross_dn = (df["slope"] < 0) & (df["slope"].shift(1) >= 0)

n = len(df)
equity = INITIAL_EQUITY
pos = 0.0
entry_px = 0.0
entry_idx = -1
equity_curve = np.full(n, np.nan)
trades = []
liquidated = False

def record_trade(exit_i, exit_px, reason):
    global equity, pos
    pnl = pos * (exit_px - entry_px) - abs(pos) * exit_px * FEE
    equity += pnl
    trades.append({
        "entry_time": df.open_time.iloc[entry_idx], "exit_time": df.open_time.iloc[exit_i],
        "side": "long" if pos > 0 else "short", "qty": abs(pos),
        "entry_px": entry_px, "exit_px": exit_px, "pnl": pnl,
        "reason": reason, "equity_after": equity,
    })
    pos = 0.0

for t in range(WARMUP, n):
    row = df.iloc[t]
    o, cl = row.open, row.close

    if equity <= 0:
        liquidated = True
        break

    # 开盘: 执行 bar t-1 的信号 —— 先反手/离场, 再入场
    sig_exit = (pos > 0 and cross_dn.iloc[t - 1]) or (pos < 0 and cross_up.iloc[t - 1])
    if pos != 0 and sig_exit:
        exit_px = o * (1 - SLIP) if pos > 0 else o * (1 + SLIP)
        record_trade(t, exit_px, "slope_flip")

    if pos == 0 and equity > 0:
        direction = 0
        if cross_up.iloc[t - 1]:
            direction = 1
        elif cross_dn.iloc[t - 1]:
            direction = -1
        if direction != 0:
            ref_dist = ATR_REF_MULT * df.atr.iloc[t - 1]
            fill = o * (1 + SLIP) if direction > 0 else o * (1 - SLIP)
            scalar = 1.0
            if VOL_TARGET > 0 and len(eq_ret_hist) >= VOL_LOOKBACK:
                sig = float(np.std(eq_ret_hist[-VOL_LOOKBACK:]) * np.sqrt(365 * 24 / BAR_H))
                if sig > 0:
                    scalar = min(max(VOL_TARGET / sig, VOL_FLOOR), VOL_CAP)
            qty = min(equity * RISK_FRAC * scalar / ref_dist, equity * MAX_LEVERAGE / fill)
            if qty > 0:
                equity -= qty * fill * FEE
                pos = qty * direction
                entry_px = fill
                entry_idx = t

    if pos != 0 and row.open_time.hour % 8 == 0:
        equity -= pos * cl * FUNDING_PER_8H

    equity_curve[t] = equity + pos * (cl - entry_px) if pos != 0 else equity
    if t > WARMUP and equity_curve[t - 1] > 0 and not np.isnan(equity_curve[t - 1]):
        eq_ret_hist.append(equity_curve[t] / equity_curve[t - 1] - 1)

if pos != 0:
    last = df.iloc[n - 1]
    exit_px = last.close * (1 - SLIP if pos > 0 else 1 + SLIP)
    record_trade(n - 1, exit_px, "end_of_data")
    equity_curve[n - 1] = equity

res = df.copy()
res["equity"] = pd.Series(equity_curve).ffill().values
res["bh"] = INITIAL_EQUITY * res["close"] / res["close"].iloc[WARMUP]

def metrics(sub, trades_df, label):
    eq = sub["equity"].dropna()
    rets = eq.pct_change().dropna()
    hrs = (sub.open_time.iloc[-1] - sub.open_time.iloc[0]).total_seconds() / 3600
    years = hrs / (24 * 365)
    total_ret = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan
    sharpe = rets.mean() / rets.std() * np.sqrt(24 * 365 / BAR_H) if rets.std() > 0 else np.nan
    dd = eq / eq.cummax() - 1
    max_dd = dd.min()
    calmar = cagr / abs(max_dd) if max_dd < 0 else np.nan
    wins = trades_df[trades_df.pnl > 0]
    losses = trades_df[trades_df.pnl <= 0]
    pf = wins.pnl.sum() / abs(losses.pnl.sum()) if len(losses) and losses.pnl.sum() != 0 else np.nan
    longs = trades_df[trades_df.side == "long"]
    shorts = trades_df[trades_df.side == "short"]
    print(f"\n=== {label} ===")
    print(f"区间收益 {total_ret:+.1%} | 年化 {cagr:+.1%} | Sharpe {sharpe:.2f} | 最大回撤 {max_dd:.1%} | Calmar {calmar:.2f}")
    print(f"交易 {len(trades_df)} 笔(多{len(longs)}/空{len(shorts)}) | 胜率 {len(wins)/max(len(trades_df),1):.1%} | PF {pf:.2f} | 多头PnL {longs.pnl.sum():+.0f} 空头PnL {shorts.pnl.sum():+.0f}")

trades_df = pd.DataFrame(trades)
trades_df.to_csv(f"trades_trend_{TAG}.csv", index=False)
res.to_csv(f"backtest_trend_{TAG}.csv", index=False)

split = pd.Timestamp(os.environ.get("SPLIT", "2025-01-01"), tz="UTC")
if (res.open_time < split).any():
    metrics(res[res.open_time < split], trades_df[trades_df.exit_time < split], f"前半段 < {split.date()}")
if (res.open_time >= split).any():
    metrics(res[res.open_time >= split], trades_df[trades_df.exit_time >= split], f"后半段 >= {split.date()}")
metrics(res, trades_df, "全区间")
print(f"\n同期买入持有: {res.bh.iloc[-1] / res.bh.iloc[WARMUP] - 1:+.1%} | 爆仓: {'是' if liquidated else '否'}")

symbol = CSV.split("_")[0].upper()
fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True,
                         gridspec_kw={"height_ratios": [3, 2, 1]})
axes[0].plot(res.open_time, res.equity, label="Strategy", lw=1.2)
axes[0].plot(res.open_time, res.bh, label="Buy & Hold", lw=1, alpha=0.7)
axes[0].set_yscale("log"); axes[0].legend(); axes[0].grid(alpha=0.3)
axes[0].set_title(f"{symbol} Perp {BAR_H}h | EMA{EMA_N} slope(k={SLOPE_K}) zero-cross trend following (1% risk, 3x max)")
axes[1].plot(res.open_time, res.close, lw=0.7, color="gray", label="Close")
axes[1].plot(res.open_time, res.ema, lw=0.9, color="orange", label=f"EMA{EMA_N}")
if len(trades_df):
    ent = trades_df.set_index("entry_time")
    axes[1].scatter(ent.index, ent.entry_px, marker="^", s=18, c="green", label="entry", zorder=3)
    ext = trades_df.set_index("exit_time")
    axes[1].scatter(ext.index, ext.exit_px, marker="v", s=18, c="red", label="exit", zorder=3)
axes[1].legend(); axes[1].grid(alpha=0.3)
dd = res.equity / res.equity.cummax() - 1
axes[2].fill_between(res.open_time, dd, 0, color="tomato", alpha=0.6)
axes[2].set_ylabel("Drawdown"); axes[2].grid(alpha=0.3)
plt.tight_layout()
plt.savefig(f"backtest_trend_{TAG}.png", dpi=130)
print(f"已保存 trades_trend_{TAG}.csv / backtest_trend_{TAG}.png")
