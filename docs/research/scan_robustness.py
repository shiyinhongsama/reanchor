"""日线 EMA 回归策略稳健性扫描: EMA周期 x 斜率窗口, 输出汇总表和热力图"""
import os
import re
import subprocess
import sys
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

EMAS = [10, 15, 20, 30]
KS = [3, 5, 8, 10]
BAR_H = sys.argv[1] if len(sys.argv) > 1 else "24"
CSV = sys.argv[2] if len(sys.argv) > 2 else "btc_1h.csv"
SCRIPT = sys.argv[3] if len(sys.argv) > 3 else "ema_pullback.py"
SYM = CSV.split("_")[0]
MODE = "trend" if "trend" in SCRIPT else "pullback"
MA_TYPE = os.environ.get("MA_TYPE", "ema")
SM = os.environ.get("SLOPE_SMOOTH", "0")
CA = os.environ.get("CHOP_ADAPT", "0")
TAG0 = f"scan_{MODE}_{MA_TYPE}_sm{SM}_ca{CA}_{SYM}_{BAR_H}h"
LINE = re.compile(r"区间收益 ([+\-\d.]+)% \| 年化 [^|]+\| Sharpe ([\-\d.]+) \| 最大回撤 ([\-\d.]+)%")

rows = []
for ema in EMAS:
    for k in KS:
        tag = f"{TAG0}_e{ema}_k{k}"
        p = subprocess.run([sys.executable, SCRIPT, BAR_H, str(ema), str(k), tag, CSV],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = p.stdout
        sections = re.split(r"=== (.+?) ===", out)
        parsed = []
        # sections: ['', 名称1, body1, 名称2, body2, '全区间', body3]
        for i in range(1, len(sections) - 1, 2):
            m = LINE.search(sections[i + 1])
            parsed.append((float(m.group(1)), float(m.group(2)), float(m.group(3))) if m else (None,) * 3)
        # 按位置: 前半段, 后半段, 全区间 (后半段可能缺失)
        is_r = parsed[0] if len(parsed) > 0 else (None,) * 3
        oos_r = parsed[1] if len(parsed) > 2 else (None,) * 3
        all_r = parsed[-1] if parsed else (None,) * 3
        rows.append({"ema": ema, "k": k,
                     "is_ret": is_r[0], "oos_ret": oos_r[0],
                     "full_ret": all_r[0], "full_sharpe": all_r[1], "full_dd": all_r[2]})
        print(f"EMA{ema} k={k}: IS {is_r[0]}% | OOS {oos_r[0]}% | 全区间 {all_r[0]}% Sharpe {all_r[1]}")

df = pd.DataFrame(rows)
df.to_csv(f"scan_robustness_{MODE}_{MA_TYPE}_sm{SM}_ca{CA}_{SYM}_{BAR_H}h.csv", index=False)
print("\n全区间收益矩阵 (行=EMA, 列=k):")
print(df.pivot(index="ema", columns="k", values="full_ret").round(1).to_string())
print("\n样本外收益矩阵:")
print(df.pivot(index="ema", columns="k", values="oos_ret").round(1).to_string())

fig, axes = plt.subplots(1, 2, figsize=(13, 5))
for ax, col, title in [
    (axes[0], "full_ret", "Full-period return % (2023-01 ~ 2026-10)"),
    (axes[1], "oos_ret", "Out-of-sample return % (2025-01 ~ 2026-10)"),
]:
    piv = df.pivot(index="ema", columns="k", values=col)
    im = ax.imshow(piv.values, cmap="RdYlGn", aspect="auto",
                   vmin=-abs(piv.values).max(), vmax=abs(piv.values).max())
    ax.set_xticks(range(len(piv.columns)), [f"k={c}" for c in piv.columns])
    ax.set_yticks(range(len(piv.index)), [f"EMA{r}" for r in piv.index])
    for i in range(piv.shape[0]):
        for j in range(piv.shape[1]):
            ax.text(j, i, f"{piv.values[i, j]:.1f}", ha="center", va="center", fontsize=11)
    ax.set_title(title)
    fig.colorbar(im, ax=ax, shrink=0.8)
plt.suptitle(f"{SYM.upper()} Perp {BAR_H}h | {MA_TYPE.upper()} slope {MODE} robustness scan")
plt.tight_layout()
plt.savefig(f"scan_robustness_{MODE}_{MA_TYPE}_sm{SM}_ca{CA}_{SYM}_{BAR_H}h.png", dpi=130)
print(f"\n已保存 scan_robustness_{MODE}_{MA_TYPE}_sm{SM}_ca{CA}_{SYM}_{BAR_H}h.csv / scan_robustness_{MODE}_{MA_TYPE}_sm{SM}_ca{CA}_{SYM}_{BAR_H}h.png")
