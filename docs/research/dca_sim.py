# 定投模拟：10000U 起步 + 每月初 +500U，三档配置 vs 定投BTC
import numpy as np
import pandas as pd

SYMS = ["btc", "ethusdt", "solusdt"]
MONTHLY_ADD = 500.0

def compose(patterns):
    eqs = None
    for p in patterns:
        df = pd.read_csv(p, parse_dates=["open_time"])
        s = df.set_index("open_time")["equity"].astype(float)
        eqs = s if eqs is None else eqs.add(s, fill_value=np.nan)
    return (eqs / 6.0).dropna()

configs = {
    "保守 1% 风险": ["portfolio_r0.01.csv"],
    "均衡 2% 风险 + VT10%": [f"backtest_fg_eq_bal_{st}_{sy}.csv" for st in ("pullback", "trend") for sy in SYMS],
    "进取 5% 风险 + VT25%": [f"backtest_fg_eq_agg_{st}_{sy}.csv" for st in ("pullback", "trend") for sy in SYMS],
}

def dca_sim(ret, monthly_add=500.0, initial=10000.0):
    """ret: 日收益率序列(带日期索引)。每月第一个交易日注入 monthly_add。"""
    eq = initial
    eqs, cfs = [], []
    month_seen = None
    total_in = initial
    cf_events = [(ret.index[0], initial)]
    for t, r in ret.items():
        eq *= (1 + r)
        if t.month != month_seen:  # 每月首个交易日
            if t != ret.index[0]:
                eq += monthly_add
                total_in += monthly_add
                cf_events.append((t, monthly_add))
        month_seen = t.month
        eqs.append(eq)
    s = pd.Series(eqs, index=ret.index)
    # XIRR（资金加权收益率）
    cf_events.append((ret.index[-1], -eq))
    def xnpv(rate):
        t0 = cf_events[0][0]
        return sum(cf / (1 + rate) ** ((t - t0).days / 365.25) for t, cf in cf_events)
    lo, hi = -0.99, 10.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if xnpv(mid) > 0: hi = mid
        else: lo = mid
    xirr = (lo + hi) / 2
    return s, total_in, xirr

results = {}
for name, pats in configs.items():
    if len(pats) == 1:
        df = pd.read_csv(pats[0], parse_dates=["open_time"])
        eq = df.set_index("open_time")["equity"].astype(float)
    else:
        eq = compose(pats)
    ret = eq.pct_change().fillna(0.0)
    s, total_in, xirr = dca_sim(ret, MONTHLY_ADD)
    # 单位净值回撤（剔除入金影响）
    unit = [1.0]
    prev = s.iloc[0]
    for i in range(1, len(s)):
        flow = MONTHLY_ADD if s.index[i].month != s.index[i-1].month else 0.0
        unit.append(unit[-1] * (s.iloc[i] - flow) / prev)
        prev = s.iloc[i]
    unit = pd.Series(unit, index=s.index)
    mdd = (unit / unit.cummax() - 1).min()
    n_months = (ret.index[-1].year - ret.index[0].year) * 12 + ret.index[-1].month - ret.index[0].month
    results[name] = dict(final=s.iloc[-1], invested=total_in, profit=s.iloc[-1]-total_in,
                         xirr=xirr, mdd=mdd, n_months=n_months, curve=s)
    print(f"{name:<20} 投入 {total_in:>7,.0f} U（{n_months}期）| 期末 {s.iloc[-1]:>8,.0f} U | "
          f"利润 {s.iloc[-1]-total_in:>+7,.0f} U | XIRR {xirr:+.1%}/年 | 单位净值最大回撤 {mdd:.1%}")

# 对照：同样现金流定投 BTC（严格对齐策略日历）
cal = list(results.values())[0]["curve"].index
btc = pd.read_csv("btc_1h.csv", parse_dates=["open_time"]).set_index("open_time")["close"].resample("1D").last().dropna()
btc = btc.reindex(cal).ffill()
btc_ret = btc.pct_change().fillna(0.0)
s_b, in_b, x_b = dca_sim(btc_ret, MONTHLY_ADD)
print(f"{'定投 BTC 持有':<20} 投入 {in_b:>7,.0f} U | 期末 {s_b.iloc[-1]:>8,.0f} U | "
      f"利润 {s_b.iloc[-1]-in_b:>+7,.0f} U | XIRR {x_b:+.1%}/年")

# 画图
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
colors = {"保守 1% 风险": "#1f77b4", "均衡 2% 风险 + VT10%": "#2ca02c", "进取 5% 风险 + VT25%": "#d62728"}
for name, r in results.items():
    ax.plot(r["curve"].index, r["curve"].values, lw=1.8, color=colors[name],
            label=f"{name}（期末 {r['curve'].iloc[-1]:,.0f} U，XIRR {r['xirr']:+.1%}）")
ax.plot(s_b.index, s_b.values, lw=1.2, color="gray", ls="--", alpha=0.7,
        label=f"定投 BTC（期末 {s_b.iloc[-1]:,.0f} U，XIRR {x_b:+.1%}）")
# 累计投入本金线
inv = pd.Series({t: 10000 + 500 * i for i, t in enumerate(results["保守 1% 风险"]["curve"].index[::22])})
inv_line = 10000 + 500 * np.arange(len(results["保守 1% 风险"]["curve"].index)) / 21.7
ax.plot(results["保守 1% 风险"]["curve"].index, inv_line, color="black", lw=1.0, alpha=0.5, label="累计投入本金")
ax.set_ylabel("账户权益（USDT）")
ax.set_title("10,000 U 起步 + 每月定投 500 U：三档配置 vs 定投 BTC（2023-03 ~ 2026-11）", fontsize=11)
ax.legend(fontsize=8.5, frameon=False, loc="upper left")
ax.grid(alpha=0.3)
fig.tight_layout()
fig.savefig("dca_sim.png", dpi=170)
print("saved dca_sim.png")
