import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";
import { Play, Square } from "lucide-react";
import { api, TIMEFRAMES, type BacktestJob, type BacktestResult, type StrategyInfo, type Trade } from "@/lib/api";
import { num, pct, pnlClass, px, ts } from "@/lib/format";
import EquityChart, { type EquitySeries } from "@/components/EquityChart";
import CandleChart, { type Candle, type TradeMarker } from "@/components/CandleChart";
import Metric from "@/components/Metric";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const SERIES_COLORS = ["#22d3ee", "#34d399", "#fbbf24", "#f472b6", "#a78bfa", "#60a5fa"];

const REASON_LABEL: Record<string, string> = {
  signal_exit: "信号出场",
  atr_stop: "ATR 止损",
  end: "期末强平",
};

export default function BacktestPage() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [form, setForm] = useState({
    strategy: "ema_pullback_v1",
    symbols: "BTCUSDT,ETHUSDT,SOLUSDT",
    timeframe: "1d",
    start: "2023-01-01",
    end: "",
    risk_pct: 5,
    vol_target: 0.25,
    max_leverage: 3,
    fee_bp: 5,
    slippage_bp: 2,
    initial_equity: 10000,
    deposit_amount: 0,
    deposit_every_days: 30,
  });
  const [params, setParams] = useState<Record<string, number | string>>({});
  const [job, setJob] = useState<BacktestJob | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [series, setSeries] = useState<EquitySeries[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [chartMode, setChartMode] = useState<"equity" | "kline">("equity");
  const [candles, setCandles] = useState<Candle[]>([]);
  const [klineBusy, setKlineBusy] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    api.strategies()
      .then((r) => {
        setStrategies(r.strategies);
        const cur = r.strategies.find((s) => s.name === form.strategy);
        if (cur) {
          setParams(Object.fromEntries(Object.entries(cur.params).map(([k, v]) => [k, v.default])));
        }
      })
      .catch(() => toast.error("无法连接后端：请先启动 uvicorn（端口 8000）"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stopPoll = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  };
  useEffect(() => stopPoll, []);

  // 从存档恢复查看（对比页"查看"跳转 ?job=<id>）：
  // 指标与权益曲线走 DB 回源（跨会话可查）；交易明细仅存内存，重启后为空
  const [searchParams] = useSearchParams();
  const archiveJob = searchParams.get("job");
  useEffect(() => {
    if (!archiveJob) return;
    setError(null);
    setResult(null);
    setSeries([]);
    setTrades([]);
    setJob({ job_id: archiveJob, status: "done", created_at: "" });
    loadResult(archiveJob).catch(() => {
      setError("存档加载失败：可能已被删除或尚未完成");
      setJob(null);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [archiveJob]);

  const loadResult = useCallback(async (jobId: string) => {
    const [res, tr] = await Promise.all([api.backtestResult(jobId), api.backtestTrades(jobId)]);
    setResult(res);
    setTrades([...tr.trades].reverse());
    const targets: { symbol: string; color: string; width: 1 | 2 }[] = [];
    if (res.portfolio) targets.push({ symbol: "__portfolio__", color: "#22d3ee", width: 2 });
    res.per_symbol.forEach((s, i) =>
      targets.push({ symbol: s.symbol, color: SERIES_COLORS[(i + 1) % SERIES_COLORS.length], width: 1 }));
    const curves = await Promise.all(targets.map((t) => api.backtestEquity(jobId, t.symbol)));
    setSeries(curves.map((c, i) => ({
      name: targets[i].symbol === "__portfolio__" ? "组合" : targets[i].symbol,
      color: targets[i].color,
      width: targets[i].width,
      points: c.points,
    })));
    setSelected(res.portfolio ? "__portfolio__" : res.per_symbol[0]?.symbol ?? "");
  }, []);

  const submit = async () => {
    setError(null);
    setResult(null);
    setSeries([]);
    setTrades([]);
    const symbols = form.symbols.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean);
    if (!symbols.length) {
      setError("至少填写一个交易对");
      return;
    }
    try {
      const j = await api.createBacktest({
        strategy: form.strategy,
        symbols,
        timeframe: form.timeframe,
        start: form.start || null,
        end: form.end || null,
        params,
        risk_frac: form.risk_pct / 100,
        vol_target: form.vol_target,
        max_leverage: form.max_leverage,
        fee: form.fee_bp / 10000,
        slippage: form.slippage_bp / 10000,
        initial_equity: form.initial_equity,
        deposit_amount: form.deposit_amount,
        deposit_every_days: form.deposit_every_days,
      });
      setJob(j);
      stopPoll();
      pollRef.current = setInterval(async () => {
        try {
          const st = await api.backtest(j.job_id);
          setJob(st);
          if (st.status === "done") {
            stopPoll();
            await loadResult(j.job_id);
          } else if (st.status === "failed") {
            stopPoll();
            setError(st.error || "回测失败");
          }
        } catch { /* 轮询失败下一拍重试 */ }
      }, 1500);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const stratInfo = strategies.find((s) => s.name === form.strategy);

  // K 线模式：组合视图取第一个品种；按选中品种拉 K 线并映射开平仓标记
  const klineSymbol = !selected || selected === "__portfolio__"
    ? result?.per_symbol[0]?.symbol
    : selected;
  useEffect(() => {
    if (chartMode !== "kline" || !job || !klineSymbol) return;
    setKlineBusy(true);
    api.backtestBars(job.job_id, klineSymbol)
      .then((r) => setCandles(r.bars))
      .catch(() => { setCandles([]); toast.error("K 线加载失败"); })
      .finally(() => setKlineBusy(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartMode, job?.job_id, klineSymbol]);

  const markers: TradeMarker[] = chartMode === "kline"
    ? trades.filter((t) => t.symbol === klineSymbol).flatMap((t) => {
        const side = t.side === "long" ? 1 as const : -1 as const;
        return [
          { t: t.entry_time, side, phase: "entry" as const, text: side > 0 ? "开多" : "开空" },
          { t: t.exit_time, side, phase: "exit" as const, win: t.pnl > 0, text: "平" },
        ];
      })
    : [];

  const currentMetrics = result
    ? selected === "__portfolio__"
      ? result.portfolio
      : result.per_symbol.find((s) => s.symbol === selected)
    : null;

  const running = job && (job.status === "pending" || job.status === "running");

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[300px_minmax(0,1fr)_300px]">
      {/* 左：参数面板 */}
      <aside className="space-y-4 overflow-y-auto border-r border-border bg-card p-4">
        <div>
          <div className="section-label mb-2">STRATEGY / 策略</div>
          <Select
            value={form.strategy}
            onValueChange={(v) => {
              setForm({ ...form, strategy: v });
              const info = strategies.find((s) => s.name === v);
              if (info) setParams(Object.fromEntries(Object.entries(info.params).map(([k, p]) => [k, p.default])));
            }}
          >
            <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              {strategies.map((s) => <SelectItem key={s.name} value={s.name} className="text-xs">{s.name}</SelectItem>)}
            </SelectContent>
          </Select>
          {stratInfo && <p className="mt-1.5 text-[10px] leading-relaxed text-muted-foreground">{stratInfo.description}</p>}
        </div>

        <div>
          <div className="section-label mb-2">UNIVERSE / 交易对</div>
          <Input
            value={form.symbols}
            onChange={(e) => setForm({ ...form, symbols: e.target.value })}
            className="h-8 font-mono text-xs"
            placeholder="BTCUSDT,ETHUSDT"
          />
          <div className="mt-3 grid grid-cols-2 gap-2">
            <div>
              <Label className="text-[10px] text-muted-foreground">周期</Label>
              <Select value={form.timeframe} onValueChange={(v) => setForm({ ...form, timeframe: v })}>
                <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {TIMEFRAMES.map((tf) => <SelectItem key={tf} value={tf} className="text-xs">{tf}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-[10px] text-muted-foreground">初始资金</Label>
              <Input type="number" value={form.initial_equity}
                onChange={(e) => setForm({ ...form, initial_equity: +e.target.value })}
                className="h-8 text-xs" />
            </div>
            <div>
              <Label className="text-[10px] text-muted-foreground">定投金额（0=关）</Label>
              <Input type="number" value={form.deposit_amount}
                onChange={(e) => setForm({ ...form, deposit_amount: +e.target.value })}
                className="h-8 text-xs" />
            </div>
            <div>
              <Label className="text-[10px] text-muted-foreground">定投间隔（天）</Label>
              <Input type="number" value={form.deposit_every_days}
                onChange={(e) => setForm({ ...form, deposit_every_days: +e.target.value })}
                className="h-8 text-xs" />
            </div>
            <div>
              <Label className="text-[10px] text-muted-foreground">开始</Label>
              <Input type="date" value={form.start}
                onChange={(e) => setForm({ ...form, start: e.target.value })} className="h-8 text-xs" />
            </div>
            <div>
              <Label className="text-[10px] text-muted-foreground">结束（空=至今）</Label>
              <Input type="date" value={form.end}
                onChange={(e) => setForm({ ...form, end: e.target.value })} className="h-8 text-xs" />
            </div>
          </div>
        </div>

        {stratInfo && Object.keys(stratInfo.params).length > 0 && (
          <div>
            <div className="section-label mb-2">PARAMS / 策略参数</div>
            <div className="grid grid-cols-2 gap-2">
              {Object.entries(stratInfo.params).map(([k, spec]) => (
                <div key={k}>
                  <Label className="text-[10px] text-muted-foreground">
                    {k} {spec.options ? "" : `[${spec.min}–${spec.max}]`}
                  </Label>
                  {spec.options ? (
                    <Select value={String(params[k] ?? spec.default)}
                      onValueChange={(v) => setParams({ ...params, [k]: v })}>
                      <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {spec.options.map((o) => <SelectItem key={o} value={o} className="text-xs">{o}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  ) : (
                    <Input
                      type="number" step={spec.type === "int" ? 1 : 0.1}
                      value={params[k] ?? spec.default as number}
                      onChange={(e) => setParams({ ...params, [k]: +e.target.value })}
                      className="h-8 text-xs"
                    />
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        <div>
          <div className="section-label mb-2">RISK / 风险与成本</div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label className="text-[10px] text-muted-foreground">单笔风险 %</Label>
              <Input type="number" step={0.5} value={form.risk_pct}
                onChange={(e) => setForm({ ...form, risk_pct: +e.target.value })} className="h-8 text-xs" />
            </div>
            <div>
              <Label className="text-[10px] text-muted-foreground">波动目标 VT</Label>
              <Input type="number" step={0.05} value={form.vol_target}
                onChange={(e) => setForm({ ...form, vol_target: +e.target.value })} className="h-8 text-xs" />
            </div>
            <div>
              <Label className="text-[10px] text-muted-foreground">杠杆上限 x</Label>
              <Input type="number" step={0.5} value={form.max_leverage}
                onChange={(e) => setForm({ ...form, max_leverage: +e.target.value })} className="h-8 text-xs" />
            </div>
            <div>
              <Label className="text-[10px] text-muted-foreground">费率 bp / 滑点 bp</Label>
              <div className="flex gap-1">
                <Input type="number" value={form.fee_bp}
                  onChange={(e) => setForm({ ...form, fee_bp: +e.target.value })} className="h-8 text-xs" />
                <Input type="number" value={form.slippage_bp}
                  onChange={(e) => setForm({ ...form, slippage_bp: +e.target.value })} className="h-8 text-xs" />
              </div>
            </div>
          </div>
        </div>

        <Button onClick={submit} disabled={!!running} className="h-9 w-full text-xs tracking-[0.2em]">
          {running ? <><Square size={12} className="mr-2 animate-pulse" />RUNNING…</> : <><Play size={12} className="mr-2" />运行回测</>}
        </Button>
        {error && <div className="border border-down/40 bg-down/10 p-2 text-[11px] text-down">{error}</div>}
        {job && !error && (
          <div className="text-[10px] text-muted-foreground">
            job <span className="text-foreground">{job.job_id}</span> · {job.status}
          </div>
        )}
      </aside>

      {/* 中：资金曲线 / K线 + 交易明细 */}
      <section className="flex min-h-0 flex-col">
        <div className="border-b border-border px-4 py-2">
          <div className="flex items-center gap-4">
            <span className="section-label">
              {chartMode === "equity" ? "EQUITY / 资金曲线" : `KLINE / ${klineSymbol ?? ""}`}
            </span>
            {chartMode === "equity" && series.map((s) => (
              <span key={s.name} className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                <span className="inline-block h-0.5 w-4" style={{ background: s.color }} />
                {s.name}
              </span>
            ))}
            <div className="ml-auto flex gap-1">
              {(["equity", "kline"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setChartMode(m)}
                  className={`px-2.5 py-1 text-[10px] tracking-wider transition-colors ${
                    chartMode === m
                      ? "bg-primary/15 text-primary"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {m === "equity" ? "资金曲线" : "K 线"}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="p-2">
          {chartMode === "kline" ? (
            candles.length > 0 ? (
              <CandleChart candles={candles} markers={markers} height={380} />
            ) : (
              <div className="flex h-[380px] items-center justify-center border border-dashed border-border text-xs text-muted-foreground">
                {klineBusy ? "K 线加载中…" : result ? "K 线暂不可用" : "> 运行回测后切换到 K 线查看买卖点"}
              </div>
            )
          ) : series.length > 0 ? (
            <EquityChart series={series} height={380} />
          ) : (
            <div className="flex h-[380px] items-center justify-center border border-dashed border-border text-xs text-muted-foreground">
              {running ? "回测进行中，正在拉取数据与撮合…" : "> 配置左侧参数，运行回测后此处绘制资金曲线"}
            </div>
          )}
        </div>
        <div className="flex min-h-0 flex-1 flex-col border-t border-border">
          <div className="border-b border-border px-4 py-2">
            <span className="section-label">TRADES / 交易明细（{trades.length}）</span>
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            {trades.length > 0 ? (
              <Table>
                <TableHeader className="sticky top-0 bg-card">
                  <TableRow className="border-border hover:bg-transparent">
                    <TableHead className="text-[10px]">品种</TableHead>
                    <TableHead className="text-[10px]">方向</TableHead>
                    <TableHead className="text-[10px]">开仓时间</TableHead>
                    <TableHead className="text-[10px] text-right">开价</TableHead>
                    <TableHead className="text-[10px]">平仓时间</TableHead>
                    <TableHead className="text-[10px] text-right">平价</TableHead>
                    <TableHead className="text-[10px]">原因</TableHead>
                    <TableHead className="text-[10px] text-right">盈亏</TableHead>
                    <TableHead className="text-[10px] text-right">事后权益</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {trades.map((t, i) => (
                    <TableRow key={i} className="border-border/60 transition-colors hover:bg-panel">
                      <TableCell className="py-1.5 text-[11px]">{t.symbol}</TableCell>
                      <TableCell className="py-1.5">
                        <span className={`text-[10px] font-bold ${t.side === "long" ? "text-up" : "text-down"}`}>
                          {t.side === "long" ? "多" : "空"}
                        </span>
                      </TableCell>
                      <TableCell className="num py-1.5 text-[11px] text-muted-foreground">{ts(t.entry_time)}</TableCell>
                      <TableCell className="num py-1.5 text-right text-[11px]">{px(t.entry_px)}</TableCell>
                      <TableCell className="num py-1.5 text-[11px] text-muted-foreground">{ts(t.exit_time)}</TableCell>
                      <TableCell className="num py-1.5 text-right text-[11px]">{px(t.exit_px)}</TableCell>
                      <TableCell className="py-1.5 text-[11px] text-muted-foreground">{REASON_LABEL[t.reason] ?? t.reason}</TableCell>
                      <TableCell className={`num py-1.5 text-right text-[11px] font-medium ${pnlClass(t.pnl)}`}>
                        {t.pnl >= 0 ? "+" : ""}{num(t.pnl)}
                      </TableCell>
                      <TableCell className="num py-1.5 text-right text-[11px] text-muted-foreground">{num(t.equity_after)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <div className="flex h-24 items-center justify-center text-xs text-muted-foreground">
                {result
                  ? archiveJob
                    ? "存档不含逐笔明细（仅本次会话内跑的回测可查）"
                    : "无交易记录"
                  : "—"}
              </div>
            )}
          </div>
        </div>
      </section>

      {/* 右：指标面板 */}
      <aside className="space-y-3 overflow-y-auto border-l border-border bg-card p-4">
        <div className="flex items-center justify-between">
          <span className="section-label">METRICS / 指标</span>
          {result && (
            <Select value={selected} onValueChange={setSelected}>
              <SelectTrigger className="h-7 w-32 text-[11px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                {result.portfolio && <SelectItem value="__portfolio__" className="text-xs">组合（等权）</SelectItem>}
                {result.per_symbol.map((s) => <SelectItem key={s.symbol} value={s.symbol} className="text-xs">{s.symbol}</SelectItem>)}
              </SelectContent>
            </Select>
          )}
        </div>
        {currentMetrics ? (
          <div className="grid grid-cols-2 gap-2">
            <Metric label="总收益" value={pct(currentMetrics.total_return)}
              tone={currentMetrics.total_return >= 0 ? "up" : "down"} />
            <Metric label="年化" value={pct(currentMetrics.annualized)}
              tone={currentMetrics.annualized >= 0 ? "up" : "down"} />
            <Metric label="SHARPE" value={num(currentMetrics.sharpe)} />
            <Metric label="最大回撤" value={pct(currentMetrics.max_drawdown)} tone="down" />
            <Metric label="CALMAR" value={currentMetrics.calmar != null ? num(currentMetrics.calmar) : "—"} />
            <Metric label="盈亏比" value={currentMetrics.profit_factor != null ? num(currentMetrics.profit_factor) : "—"} />
            <Metric label="交易笔数" value={String(currentMetrics.trades)} />
            {currentMetrics.total_deposited != null && (
              <Metric label="累计入金" value={num(currentMetrics.total_deposited)} />
            )}
            {currentMetrics.net_profit != null && (
              <Metric label="净利润" value={`${currentMetrics.net_profit >= 0 ? "+" : ""}${num(currentMetrics.net_profit)}`}
                tone={currentMetrics.net_profit >= 0 ? "up" : "down"} />
            )}
          </div>
        ) : (
          <div className="flex h-32 items-center justify-center border border-dashed border-border text-xs text-muted-foreground">
            等待回测结果
          </div>
        )}
        {result && (
          <div className="border border-border bg-panel p-3 text-[10px] leading-relaxed text-muted-foreground">
            <div className="section-label mb-1.5">口径说明</div>
            信号在已收盘 K 线产生，下一根开盘价成交；含手续费与滑点；多品种等权合成组合。与实盘执行器共用同一套 domain 逻辑。
            {currentMetrics?.total_deposited != null && (
              <div className="mt-1.5 border-t border-border pt-1.5">
                定投口径：绩效（年化/夏普/回撤）按份额净值计算，剔除入金影响；资金曲线为账户真实余额（含入金）。
              </div>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}
