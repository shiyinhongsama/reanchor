"""组合净值: 3品种 x 2版本 等权子账户(各1/6资金, 0.5%风险, 无再平衡)"""
import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

SLEEVES = {
    "BTC pullback": "backtest_pullback_p_pb_btc.csv",
    "ETH pullback": "backtest_pullback_p_pb_eth.csv",
    "SOL pullback": "backtest_pullback_p_pb_sol.csv",
    "BTC trend": "backtest_trend_p_tr_btc.csv",
    "ETH trend": "backtest_trend_p_tr_eth.csv",
    "SOL trend": "backtest_trend_p_tr_sol.csv",
}
INIT = 10_000.0

base = None
eqs = {}
for name, f in SLEEVES.items():
    d = pd.read_csv(f, parse_dates=["open_time"])[["open_time", "equity"]].dropna()
    d = d.set_index("open_time")["equity"]
    eqs[name] = d
    base = d if base is None else base

pf = pd.DataFrame(eqs).ffill().dropna()
# 每个子账户从 INIT 起步 -> 组合 = 6 条曲线的均值 (各占 1/6 资金)
port = pf.mean(axis=1)

# 逐品种买入持有参照 (等权)
bh = []
for sym, f in [("btc", "backtest_trend_p_tr_btc.csv"), ("eth", "backtest_trend_p_tr_eth.csv"),
               ("sol", "backtest_trend_p_tr_sol.csv")]:
    d = pd.read_csv(f, parse_dates=["open_time"])
    d = d[d.open_time >= pf.index[0]]
    bh.append(INIT * d["close"].values / d["close"].iloc[0])
bh_eq = pd.Series(np.mean(bh, axis=0), index=d.open_time.values[: len(bh[0])])

def stat(eq, label, hrs_per_bar=24):
    rets = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).total_seconds() / 3600 / (24 * 365)
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
    sharpe = rets.mean() / rets.std() * np.sqrt(24 * 365 / hrs_per_bar) if rets.std() > 0 else np.nan
    dd = (eq / eq.cummax() - 1).min()
    calmar = cagr / abs(dd) if dd < 0 else np.nan
    print(f"{label:<28} 总收益 {total:+7.1%} | 年化 {cagr:+5.1%} | Sharpe {sharpe:5.2f} | 最大回撤 {dd:6.1%} | Calmar {calmar:5.2f}")

print("=== 组合 vs 各子账户 (2023-01 ~ 2026-10, 每子账户0.5%风险) ===")
stat(port, "★ 组合 (6子账户等权)")
for c in pf.columns:
    stat(pf[c], c)
bh_eq.index = pd.to_datetime(bh_eq.index)
stat(bh_eq, "买入持有 (3品种等权)")

# 样本外单独看
split = pd.Timestamp("2025-01-01", tz="UTC")
print("\n=== 样本外 2025-01 ~ 2026-10 ===")
p_oos = port[port.index >= split]
stat(p_oos / p_oos.iloc[0] * INIT, "★ 组合")

fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                         gridspec_kw={"height_ratios": [3, 1]})
axes[0].plot(port.index, port.values, label="Portfolio (6 sleeves, 0.5% risk each)", lw=1.6, color="black")
axes[0].plot(bh_eq.index, bh_eq.values, label="Buy & Hold (3 assets eq-weight)", lw=1, alpha=0.6, color="orange")
for c in pf.columns:
    axes[0].plot(pf.index, pf[c], lw=0.5, alpha=0.35)
axes[0].set_yscale("log"); axes[0].legend(); axes[0].grid(alpha=0.3)
axes[0].set_title("BTC+ETH+SOL daily | pullback(EMA20/k8) + trend(EMA20/k3) portfolio")
dd = port / port.cummax() - 1
axes[1].fill_between(port.index, dd, 0, color="tomato", alpha=0.6)
axes[1].set_ylabel("Drawdown"); axes[1].grid(alpha=0.3)
plt.tight_layout()
plt.savefig("portfolio.png", dpi=130)
port.to_frame("equity").to_csv("portfolio_equity.csv")
print("\n已保存 portfolio.png / portfolio_equity.csv")
