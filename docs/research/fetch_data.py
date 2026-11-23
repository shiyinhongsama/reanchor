"""从币安公开数据仓库下载 USDT-M 永续 1h K线: python fetch_data.py BTCUSDT"""
import io
import sys
import zipfile
import time
import requests
import pandas as pd

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
START = sys.argv[2] if len(sys.argv) > 2 else "2023-01"
END = sys.argv[3] if len(sys.argv) > 3 else "2026-10"
OUT = sys.argv[4] if len(sys.argv) > 4 else f"{SYMBOL.lower()}_1h.csv"
BASE = f"https://data.binance.vision/data/futures/um/monthly/klines/{SYMBOL}/1h"
MONTHS = pd.period_range(START, END, freq="M")

frames = []
for p in MONTHS:
    name = f"{SYMBOL}-1h-{p.year}-{p.month:02d}.zip"
    url = f"{BASE}/{name}"
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 404:
                print(f"{name}: 404 跳过")
                break
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                csv_name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
                with z.open(csv_name) as f:
                    df = pd.read_csv(f, header=None, names=[
                        "open_time", "open", "high", "low", "close", "volume", "close_time",
                        "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"])
                    # 有些月份带表头行，剔除
                    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
            frames.append(df)
            print(f"{name}: ok ({len(df)} rows)")
            break
        except Exception as e:
            print(f"{name}: 第{attempt+1}次失败 {type(e).__name__} {e}")
            time.sleep(2)

data = pd.concat(frames, ignore_index=True)

# open_time 可能是毫秒或微秒，统一为毫秒
ot = data["open_time"].astype("int64")
ot = ot.where(ot < 10**14, ot // 1000)
data["open_time"] = pd.to_datetime(ot, unit="ms", utc=True)

data = data[["open_time", "open", "high", "low", "close", "volume"]]
data = data.drop_duplicates(subset="open_time").sort_values("open_time").reset_index(drop=True)
for c in ["open", "high", "low", "close", "volume"]:
    data[c] = data[c].astype(float)

data.to_csv(OUT, index=False)
print(f"\n共 {len(data)} 根K线, {data.open_time.iloc[0]} -> {data.open_time.iloc[-1]}")
