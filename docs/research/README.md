# 研究归档：EMA 斜率趋势策略

本目录归档论文 **[../EMA_Slope_Trend_Strategy_Paper.pdf](../EMA_Slope_Trend_Strategy_Paper.pdf)**
中所有关键数字的可复现脚本与输入数据。平台代码（`backend/app/domain` 与
`backend/app/services/backtest_engine.py`）是这些研究脚本的工程化移植，
冒烟测试锚定的两条基线（v1 +23.4% / v2 +25.1%）即来自 `atr_reset_test.py`。

## 数据

`data/` 目录（1h K 线，USDT-M 永续，UTC）：

| 文件 | 说明 |
|---|---|
| `btc_1h.csv` / `ethusdt_1h.csv` / `solusdt_1h.csv` | 三个研究品种 1h K 线 |
| `fear_greed.csv` | 恐慌贪婪指数（情绪过滤实验用） |

脚本默认在当前目录找数据文件，运行前先 `cp data/*.csv .`（或把数据放回同级）。
重新下载用 `fetch_data.py`（币安公开数据仓库，可能需要网络环境支持）。

## 脚本索引

### 主线（论文最终策略：回调版 + v1 重锚 + 5% 风险 + VT 25%）

| 脚本 | 产出 |
|---|---|
| `ema_pullback.py` | 回调版信号回测：回踩 EMA 收回入场、斜率翻转出场 |
| `ema_slope_trend.py` | 纯趋势版（斜率过零入场、反转出场）——被回调版淘汰的对照 |
| `atr_reset_test.py` | **重锚止损 v0/v1/v2 对比**，`ReanchorStop` 的语义源头 |
| `scan_robustness.py` | 参数稳健性扫描（EMA 周期 × 斜率窗口热力图） |
| `slope_smooth_test.py` | 斜率平滑对比（raw / EMA3 / EMA5 / 连续 2 日确认） |
| `vol_target_test.py` | 波动率目标 A/B（VT 开/关，6 子账户） |
| `risk_scale_full.py` | 十三档风险刻度（0.5%–50%）全量对比 |
| `portfolio.py` / `v1_portfolio_check.py` | 组合级验证（三品种等权子账户，v0 vs v1，含定投现金流） |
| `timeframe_12h_2d.py` | 周期检验（12h 与 2d 级别） |
| `walkforward.py` | 滚动起点检验（训练 24 个月 / 测试 6 个月） |
| `dca_sim.py` / `dca_risk_scale_v1.py` | 定投模拟（月投现金流下的策略表现） |
| `live_signal.py` | 实盘信号核对（与交易所 K 线对照） |
| `make_paper.py` | 生成论文 PDF |

## 复现论文核心数字

```bash
cd docs/research
cp data/*.csv .
python atr_reset_test.py     # v0 +25.2% / v1 +26.6% / v2 +25.6%（三品种平均年化）
```

平台侧的等价验证见 `backend/tests/test_engine_smoke.py`。
