"""Walk-forward 滚动验证 (BTC, 回调版):
训练窗24个月选参数(EMA x k 9格, 按Sharpe), 测试窗6个月滚动,
对比 walk-forward 动态选参 vs 固定 EMA20/k8
"""
import os
import re
import subprocess
import sys
import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 合并 2020-2022 与 2023-2026 数据
a = pd.read_csv("btc_1h_bear.csv", parse_dates=["open_time"])
b = pd.read_csv("btc_1h.csv", parse_dates=["open_time"])
df = pd.concat([a, b]).drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)

GRID_EMA = [15, 20, 30]
GRID_K = [3, 5, 8]
FIXED = (20, 8)
LINE = re.compile(r"区间收益 ([+\-\d.]+)% \| 年化 [^|]+\| Sharpe ([\-\d.]+) \| 最大回撤 ([\-\d.]+)%")

def run_slice(slice_df, ema, k, tag):
    slice_df.to_csv("wf_tmp.csv", index=False)
    env = dict(os.environ, SPLIT="2030-01-01")
    p = subprocess.run([sys.executable, "ema_pullback.py", "24", str(ema), str(k), tag, "wf_tmp.csv"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    m = None
    for sec in re.split(r"=== .+? ===", p.stdout):
        mm = LINE.search(sec)
        if mm:
            m = mm  # 最后一个匹配是 全区间
    if m is None:
        return None, None
    return float(m.group(1)), float(m.group(2))

windows = []
start = pd.Timestamp("2020-01-01", tz="UTC")
while True:
    tr_end = start + pd.DateOffset(months=24)
    te_end = tr_end + pd.DateOffset(months=6)
    if tr_end > df.open_time.iloc[-1]:
        break
    windows.append((start, tr_end, min(te_end, df.open_time.iloc[-1] + pd.Timedelta(hours=1))))
    start += pd.DateOffset(months=6)

print(f"共 {len(windows)} 个滚动窗口")
rows = []
for tr_start, tr_end, te_end in windows:
    tr = df[(df.open_time >= tr_start) & (df.open_time < tr_end)]
    te = df[(df.open_time >= tr_end) & (df.open_time < te_end)]
    if len(te) < 500:
        continue
    best, best_sharpe = None, -999
    for ema in GRID_EMA:
        for k in GRID_K:
            ret, sharpe = run_slice(tr, ema, k, f"wfsel_{ema}_{k}")
            if sharpe is not None and sharpe > best_sharpe:
                best_sharpe, best = sharpe, (ema, k)
    wf_ret, _ = run_slice(te, best[0], best[1], "wfsel_best")
    fx_ret, fx_sharpe = run_slice(te, FIXED[0], FIXED[1], "wffixed")
    rows.append({"test_start": tr_end, "test_end": te_end, "wf_ema": best[0], "wf_k": best[1],
                 "train_sharpe": best_sharpe, "wf_ret": wf_ret, "fixed_ret": fx_ret})
    print(f"测试窗 {tr_end.date()} ~ {te_end.date()}: WF选中 EMA{best[0]}/k{best[1]} "
          f"(训练Sharpe {best_sharpe:.2f}) -> 测试 {wf_ret:+.1f}% | 固定EMA20/k8 -> {fx_ret:+.1f}%")

res = pd.DataFrame(rows)
res.to_csv("walkforward_result.csv", index=False)
wf_eq = (1 + res.wf_ret / 100).cumprod() * 10000
fx_eq = (1 + res.fixed_ret / 100).cumprod() * 10000
print(f"\nWF 动态选参累计收益: {wf_eq.iloc[-1] / 10000 - 1:+.1%} | 固定参数累计收益: {fx_eq.iloc[-1] / 10000 - 1:+.1%}")
print(f"WF 测试窗胜率(相对固定): {(res.wf_ret > res.fixed_ret).mean():.0%}")
print(f"WF 选中参数分布: {dict(res.groupby(['wf_ema','wf_k']).size())}")

fig, ax = plt.subplots(figsize=(12, 5))
ax.plot(res.test_end, wf_eq.values, marker="o", label="Walk-forward selected", lw=1.5)
ax.plot(res.test_end, fx_eq.values, marker="s", label="Fixed EMA20/k8", lw=1.5)
ax.legend(); ax.grid(alpha=0.3)
ax.set_title("Walk-forward (24m train / 6m test) vs fixed params | BTC daily pullback")
plt.tight_layout()
plt.savefig("walkforward.png", dpi=130)

# 清理临时文件
for f in ["wf_tmp.csv"]:
    if os.path.exists(f):
        os.remove(f)
import glob
for f in glob.glob("backtest_pullback_wf*") + glob.glob("trades_pullback_wf*"):
    os.remove(f)
print("已保存 walkforward_result.csv / walkforward.png")
