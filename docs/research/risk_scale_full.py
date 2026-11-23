# 重新生成十三档风险刻度图（修复图例混入 _child1 的问题）
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

# 中文字体
for f in font_manager.findSystemFonts():
    if "msyh" in f.lower() or "simhei" in f.lower():
        font_manager.fontManager.addfont(f)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=f).get_name()
        break
plt.rcParams["axes.unicode_minus"] = False

RISKS = [0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]

import glob, re
risk_files = {}
for fn in glob.glob("portfolio_r*.csv"):
    risk_files[round(float(re.search(r"portfolio_r([\d.]+)\.csv", fn).group(1)), 4)] = fn

curves = {}
stats = []
for r in RISKS:
    df = pd.read_csv(risk_files[round(r, 4)], parse_dates=["open_time"])
    eq = df["equity"].astype(float)
    curves[r] = (df["open_time"], eq)
    ret = eq.pct_change().dropna()
    total = eq.iloc[-1] / eq.iloc[0] - 1
    years = (df["open_time"].iloc[-1] - df["open_time"].iloc[0]).days / 365.25
    ann = (1 + total) ** (1 / years) - 1
    sharpe = ret.mean() / ret.std() * np.sqrt(365)
    peak = eq.cummax()
    mdd = (eq / peak - 1).min()
    calmar = ann / abs(mdd) if mdd != 0 else np.nan
    stats.append((r, total, ann, sharpe, mdd, calmar, ret.min()))

print(f"{'风险':>6} {'总收益':>9} {'年化':>8} {'Sharpe':>7} {'MaxDD':>8} {'Calmar':>7} {'最差单日':>9}")
for r, total, ann, sharpe, mdd, calmar, worst in stats:
    print(f"{r:>6.1%} {total:>9.1%} {ann:>8.1%} {sharpe:>7.2f} {mdd:>8.1%} {calmar:>7.2f} {worst:>9.1%}")

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 5.4),
                               gridspec_kw={"height_ratios": [1.35, 1]}, sharex=False)

# (a) 净值曲线
cmap = plt.cm.viridis(np.linspace(0.05, 0.9, len(RISKS)))
for (r, (t, eq)), c in zip(curves.items(), cmap):
    lw = 2.2 if r in (0.05, 0.20, 0.50) else 1.0
    ax1.plot(t, eq / eq.iloc[0], color=c, lw=lw, label=f"{r:.1%}")
ax1.set_yscale("log")
ax1.set_ylabel("净值（log）")
ax1.legend(ncol=7, fontsize=7.5, frameon=False, title="单笔风险", title_fontsize=8,
           loc="upper left", handlelength=1.4, columnspacing=0.8)
ax1.grid(alpha=0.3, which="both")
ax1.set_title("(a) 十三档风险刻度的组合净值", fontsize=10)

# (b) 年化与 Calmar 风险响应
rp = np.array([s[0] for s in stats]) * 100
anns = np.array([s[2] for s in stats]) * 100
calmars = np.array([s[5] for s in stats])
mdds = np.array([abs(s[4]) for s in stats]) * 100

ln1, = ax2.plot(rp, anns, "o-", color="tab:blue", lw=1.8, ms=4, label="年化收益")
ax2.set_xlabel("单笔风险（%）")
ax2.set_ylabel("年化收益（%）", color="tab:blue")
ax2.tick_params(axis="y", labelcolor="tab:blue")
ax2.grid(alpha=0.3)

ax2b = ax2.twinx()
ln2, = ax2b.plot(rp, calmars, "s--", color="tab:red", lw=1.8, ms=4, label="Calmar 比率")
ax2b.set_ylabel("Calmar 比率", color="tab:red")
ax2b.tick_params(axis="y", labelcolor="tab:red")
ax2b.axhline(0, color="gray", lw=0.8, alpha=0.5)  # 不进图例

ax2.legend(handles=[ln1, ln2], loc="upper left", fontsize=8, frameon=False)
ax2.set_title("(b) 年化收益与 Calmar 的风险响应（凯利穹顶）", fontsize=10)

fig.tight_layout()
fig.savefig("risk_scale_full.png", dpi=170)
print("saved risk_scale_full.png")
