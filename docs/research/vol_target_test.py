"""波动率目标 A/B 测试: 6子账户, 2%基准风险, VOL_TARGET 开/关对比"""
import os
import subprocess
import sys
import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

SLEEVES = [
    ("pb_btc", "ema_pullback.py", "20", "8", "btc_1h.csv"),
    ("pb_eth", "ema_pullback.py", "20", "8", "ethusdt_1h.csv"),
    ("pb_sol", "ema_pullback.py", "20", "8", "solusdt_1h.csv"),
    ("tr_btc", "ema_slope_trend.py", "20", "3", "btc_1h.csv"),
    ("tr_eth", "ema_slope_trend.py", "20", "3", "ethusdt_1h.csv"),
    ("tr_sol", "ema_slope_trend.py", "20", "3", "solusdt_1h.csv"),
]
CONFIGS = [("Fixed risk (no VT)", "0"), ("Vol target 10%", "0.10"),
           ("Vol target 15%", "0.15"), ("Vol target 25%", "0.25")]
BASE_RISK = sys.argv[1] if len(sys.argv) > 1 else "0.02"
split = pd.Timestamp("2025-01-01", tz="UTC")

def stat(eq):
    rets = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).total_seconds() / 3600 / (24 * 365)
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
    sharpe = rets.mean() / rets.std() * np.sqrt(365) if rets.std() > 0 else np.nan
    dd = (eq / eq.cummax() - 1).min()
    return total, cagr, sharpe, dd, (cagr / abs(dd) if dd < 0 else np.nan)

curves = {}
print(f"{'config':<20}{'total':>9}{'cagr':>8}{'Sharpe':>8}{'maxDD':>9}{'Calmar':>8}{'OOS_cagr':>10}{'OOS_DD':>9}")
for label, vt in CONFIGS:
    eqs = []
    for name, script, ema, k, csv in SLEEVES:
        tag = f"vt{vt.replace('.','')}_{name}"
        env = dict(os.environ, RISK_FRAC=BASE_RISK, VOL_TARGET=vt)
        subprocess.run([sys.executable, script, "24", ema, k, tag, csv],
                       capture_output=True, env=env)
        result = f"backtest_{'pullback' if 'pullback' in script else 'trend'}_{tag}.csv"
        d = pd.read_csv(result, parse_dates=["open_time"]).set_index("open_time")["equity"].dropna()
        eqs.append(d)
    port = pd.concat(eqs, axis=1).ffill().dropna().mean(axis=1)
    curves[label] = port
    t, c, s, dd, cal = stat(port)
    oos = port[port.index >= split]
    t2, c2, s2, dd2, _ = stat(oos)
    print(f"{label:<18}{t:>+8.1%}{c:>+8.1%}{s:>8.2f}{dd:>9.1%}{cal:>8.2f}{c2:>+10.1%}{dd2:>10.1%}")

fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
for label, port in curves.items():
    axes[0].plot(port.index, port.values, label=label, lw=1.4)
axes[0].set_yscale("log"); axes[0].legend(); axes[0].grid(alpha=0.3)
axes[0].set_title(f"Portfolio: fixed {float(BASE_RISK):.0%} risk vs volatility-targeted sizing (6 sleeves)")
for label, port in curves.items():
    dd = port / port.cummax() - 1
    axes[1].plot(port.index, dd, label=label, lw=1.1)
axes[1].set_ylabel("Drawdown"); axes[1].grid(alpha=0.3); axes[1].legend()
plt.tight_layout()
plt.savefig("vol_target_test.png", dpi=130)

# 清理中间文件
for name, script, ema, k, csv in SLEEVES:
    for vt in ["0", "0.10", "0.07"]:
        tag = f"vt{vt.replace('.','')}_{name}"
        kind = "pullback" if "pullback" in script else "trend"
        for f in [f"backtest_{kind}_{tag}.csv", f"trades_{kind}_{tag}.csv",
                  f"backtest_{kind}_{tag}.png"]:
            if os.path.exists(f):
                os.remove(f)
print("\n已保存 vol_target_test.png")
