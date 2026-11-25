# AGENTS.md — ReAnchor

合约量化交易平台：策略模板 → 执行器（持有参数配置）→ 回测 / dry-run 实盘。全部 dry-run，无真实下单。

## 技术栈与结构

- **后端** `backend/`：FastAPI + SQLite(aiosqlite) + APScheduler，端口 8000
  - `app/strategies/` 策略模板（`ema_pullback_v1` 回归版、`ema_trend_v1` 趋势版；均线族在 `ma.py`：ema/sma/wma/dema/tema）
  - `app/services/` backtest_runner（回测/扫参/走步）、live_runner（执行器调度）、bars_service（K线缓存）、executor_service
  - `app/api/` 路由；`app/exchange/` 交易所统一接口（ccxt，一期 gateio）
- **前端** `frontend/`：React + Vite + Tailwind + shadcn/ui + lightweight-charts v5，dev 端口 3000，`/api` 代理 8000
- 技术设计文档：`docs/technical-design.md`；策略研究论文：`docs/EMA_Slope_Trend_Strategy_Paper.pdf`

## 常用命令

```bash
# 后端（在 backend/ 下）
PYTHONPATH=. python -m uvicorn app.main:app --port 8000 --log-level warning
PYTHONPATH=. python tests/<name>.py          # 单组测试，共13组

# 前端（在 frontend/ 下）
npm run dev -- --host 127.0.0.1
npm run build                                 # 提交前必跑
```

## 硬性规则（改动前必读）

1. **无前视偏差**：信号只用已收盘 K 线；`pd.Timestamp(ms, unit="ms", tz="UTC")`（缺 `unit="ms"` 会把毫秒当纳秒）。
2. **测试先行**：13 组测试全绿才能提交。`test_engine_smoke` 锚定研究基线 ±3pp——改信号/仓位/止损逻辑必红，需先确认是有意变更。
3. **测试隔离**：换 FakeAdapter 的模块必须同步替换模块级 `SessionLocal`（`bt.SessionLocal = Session` 等），否则 K 线缓存污染开发库。
4. **走步验证存档**：WF 的 OOS 拼接结果自动落 `backtest_runs`，策略名带 `·WF` 后缀；OOS 段跨折不逐笔记录，笔数为 0 属正常。
5. **K线缓存**：只入库已收盘 K 线，尾部 2 根每次回拉校验；读写异常须降级为直拉，不得中断主流程。

## 验证流程

实现 → 后端测试 → `npm run build` → Playwright 截图核对 UI → commit + push。

- Playwright：登录页密码 `reanchor`；等待用 `page.wait_for_timeout(ms)`；抽屉内元素用 `page.locator('[data-slot="sheet-content"]')` 限定作用域。
- git push 走仓库级代理 `http://127.0.0.1:7897`；偶发 SSL 握手失败，重试即可。

## 路线图

- 一期收尾：成本敏感性批量重跑
- 二期：AI Agent 交易助手（只读工具集起步）
