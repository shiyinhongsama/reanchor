# -*- coding: utf-8 -*-
"""
实盘信号计算器 —— 纯回调版 5% 风险 + VT25%（日线 EMA20 / 斜率 k=8 / ATR14×3）
用法：
    python live_signal.py --csv BTCUSDT_1d.csv --equity 3500
    python live_signal.py --csv BTCUSDT_1d.csv --equity 3500 --pos long --stop 61200 --peak 66800
    python live_signal.py --csv BTCUSDT_1d.csv --equity 3500 --eq-hist equity_btc.csv

CSV 要求：列 open_time,open,high,low,close（日线，最后一行为已收盘K线）
--eq-hist：本子账户权益曲线（列 equity），用于 VT 波动率目标缩放（需≥20 条）
每日收盘后运行一次，按输出的"操作"执行即可。信号以最后一根已收盘K线为准，次日开盘执行。
"""
import argparse
import sys
import numpy as np
import pandas as pd

EMA_N, SLOPE_K = 20, 8
ATR_N, ATR_MULT = 14, 3.0
RISK_FRAC = 0.05
VOL_TARGET, VOL_LOOKBACK, VOL_CAP, VOL_FLOOR = 0.25, 20, 2.5, 0.25
MAX_LEVERAGE = 3.0

p = argparse.ArgumentParser()
p.add_argument("--csv", required=True, help="日线K线 CSV")
p.add_argument("--equity", type=float, required=True, help="本子账户当前权益（USDT）")
p.add_argument("--pos", choices=["none", "long", "short"], default="none", help="当前持仓方向")
p.add_argument("--entry-stop", type=float, default=None, help="开仓时的初始止损价（有持仓时必填）")
p.add_argument("--eq-hist", default=None, help="权益曲线 CSV（含 equity 列），用于 VT 缩放")
args = p.parse_args()

d = pd.read_csv(args.csv, parse_dates=["open_time"])
if len(d) < EMA_N + SLOPE_K + 2:
    sys.exit("K线数量不足，至少需要 30 根日线")

c = d["close"]
d["ema"] = c.ewm(span=EMA_N, adjust=False).mean()
d["slope"] = d["ema"] - d["ema"].shift(SLOPE_K)
tr = pd.concat([d["high"] - d["low"], (d["high"] - c.shift(1)).abs(),
                (d["low"] - c.shift(1)).abs()], axis=1).max(axis=1)
d["atr"] = tr.ewm(alpha=1 / ATR_N, adjust=False).mean()

last = d.iloc[-1]      # 最新已收盘K线（信号K线）
prev = d.iloc[-2]

print(f"数据截至: {last.open_time}  收盘 {last.close:,.2f}")
print(f"EMA20={last.ema:,.2f}  斜率(8日)={last.slope:+,.2f}  ATR14={last.atr:,.2f}")
print("-" * 60)

# ---------- 有持仓：先判出场，再按 v1 规则重锚止损 ----------
if args.pos in ("long", "short"):
    if args.entry_stop is None:
        sys.exit("有持仓时必须提供 --entry-stop（开仓时的初始止损价）")
    exit_sig = (args.pos == "long" and last.slope < 0) or (args.pos == "short" and last.slope > 0)
    if exit_sig:
        print(f"【操作】明日开盘平仓离场（斜率翻转：{last.slope:+,.2f}）")
    else:
        dist = ATR_MULT * last.atr
        if args.pos == "long":
            new_stop = max(args.entry_stop, last.close - dist)
        else:
            new_stop = min(args.entry_stop, last.close + dist)
        print(f"【操作】继续持有。止损重锚为 {new_stop:,.2f}（斜率同向，以当日收盘−3×ATR 重算，不越过初始止损 {args.entry_stop:,.2f}）")
        print(f"        止损触发距离现价 {abs(new_stop / last.close - 1):.1%}")
    sys.exit(0)

# ---------- 空仓：判入场 ----------
sig_long = (prev.close > prev.ema) and (last.low <= last.ema) and (last.close > last.ema) and (last.slope > 0)
sig_short = (prev.close < prev.ema) and (last.high >= last.ema) and (last.close < last.ema) and (last.slope < 0)

if not (sig_long or sig_short):
    print("【操作】无信号，继续空仓等待。")
    if last.slope > 0:
        print(f"        环境：斜率为正（{last.slope:+,.2f}），等待价格回踩 EMA20（当前 {last.ema:,.2f}）")
    else:
        print(f"        环境：斜率为负（{last.slope:+,.2f}），等待价格反弹至 EMA20（当前 {last.ema:,.2f}）")
    sys.exit(0)

direction = "long" if sig_long else "short"

# VT 波动率目标缩放
scalar, sigma_ann = 1.0, None
if args.eq_hist:
    eq = pd.read_csv(args.eq_hist)["equity"].astype(float)
    rets = eq.pct_change().dropna().values
    if len(rets) >= VOL_LOOKBACK:
        sigma_ann = float(np.std(rets[-VOL_LOOKBACK:]) * np.sqrt(365))
        if sigma_ann > 0:
            scalar = min(max(VOL_TARGET / sigma_ann, VOL_FLOOR), VOL_CAP)
    else:
        print(f"（提示：权益历史 {len(rets)} 条 < {VOL_LOOKBACK} 条，VT 未启用，scalar=1.0）")

dist = ATR_MULT * last.atr
risk_amt = args.equity * RISK_FRAC * scalar
qty = risk_amt / dist
notional = qty * last.close
lev = notional / args.equity
if lev > MAX_LEVERAGE:
    qty = args.equity * MAX_LEVERAGE / last.close
    notional = qty * last.close
    lev = MAX_LEVERAGE
stop0 = last.close - dist if direction == "long" else last.close + dist

cn = "做多" if direction == "long" else "做空"
print(f"【操作】明日开盘{cn}（{'回调' if direction == 'long' else '反弹'}至 EMA20 后收回，斜率同向）")
print(f"        参考方向: {direction}   信号K线收盘: {last.close:,.2f}")
print(f"        止损距离 3×ATR = {dist:,.2f}（{dist / last.close:.1%}）")
print(f"        初始止损价: {stop0:,.2f}")
print(f"        风险金额 = {args.equity:,.0f} × 5% × VT({scalar:.2f}) = {risk_amt:,.2f} USDT"
      + (f"（滚动年化波动 {sigma_ann:.1%}）" if sigma_ann else "（VT 未启用）"))
print(f"        开仓数量 = {qty:.6f} BTC等值，名义价值 ≈ {notional:,.0f} USDT（杠杆 {lev:.2f}x）")
print(f"        开仓后请立即挂止损单 {stop0:,.2f}，并记录初始止损价（每日重锚的下限）")
