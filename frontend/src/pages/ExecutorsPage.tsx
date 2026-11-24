import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Pause, Play, Plus, RefreshCw, Trash2 } from "lucide-react";
import {
  api, seenSignals, TIMEFRAMES,
  type Executor, type ExecutorSignal, type SchedulerJob, type SignalLog, type StrategyInfo,
} from "@/lib/api";
import { num, pct, pnlClass, px, ts } from "@/lib/format";
import EquityChart from "@/components/EquityChart";
import CandleChart, { type Candle, type TradeMarker } from "@/components/CandleChart";
import Metric from "@/components/Metric";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const STATUS_LED: Record<string, { cls: string; label: string }> = {
  running: { cls: "led-live", label: "运行中" },
  paused: { cls: "led-off", label: "已暂停" },
  stopped: { cls: "led-down", label: "已停止" },
};

const ACTION_LABEL: Record<string, { label: string; cls: string }> = {
  open: { label: "开仓", cls: "text-up" },
  close: { label: "平仓", cls: "text-down" },
  trail_stop: { label: "重锚止损", cls: "text-primary" },
  hold: { label: "持有/等待", cls: "text-muted-foreground" },
  error: { label: "错误", cls: "text-warn" },
  circuit_break: { label: "熔断暂停", cls: "text-down" },
};

function CreateExecutorDialog({ strategies, onCreated }: { strategies: StrategyInfo[]; onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    name: "", strategy: "ema_pullback_v1", symbol: "BTCUSDT", timeframe: "1d",
    equity_budget: 1000, risk_pct: 5, vol_target: 0.25, max_leverage: 3,
  });
  const [params, setParams] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);
  const info = strategies.find((s) => s.name === form.strategy);

  const submit = async () => {
    setBusy(true);
    try {
      await api.createExecutor({
        name: form.name || undefined,
        strategy: form.strategy,
        symbol: form.symbol,
        timeframe: form.timeframe,
        params,
        equity_budget: form.equity_budget,
        risk_frac: form.risk_pct / 100,
        vol_target: form.vol_target,
        max_leverage: form.max_leverage,
      });
      toast.success("执行器已创建（默认暂停状态）");
      setOpen(false);
      onCreated();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" className="h-8 text-xs"><Plus size={13} className="mr-1" />新建执行器</Button>
      </DialogTrigger>
      <DialogContent className="border-border bg-card sm:max-w-md">
        <DialogHeader><DialogTitle className="text-sm tracking-wider">从策略模板创建执行器</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <div className="col-span-2">
              <Label className="text-[10px] text-muted-foreground">名称（留空自动生成）</Label>
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="h-8 text-xs" />
            </div>
            <div>
              <Label className="text-[10px] text-muted-foreground">策略模板</Label>
              <Select value={form.strategy} onValueChange={(v) => {
                setForm({ ...form, strategy: v });
                const si = strategies.find((s) => s.name === v);
                if (si) setParams(Object.fromEntries(Object.entries(si.params).map(([k, p]) => [k, p.default])));
              }}>
                <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {strategies.map((s) => <SelectItem key={s.name} value={s.name} className="text-xs">{s.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-[10px] text-muted-foreground">交易对</Label>
              <Input value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })} className="h-8 font-mono text-xs" />
            </div>
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
              <Label className="text-[10px] text-muted-foreground">资金预算 USDT</Label>
              <Input type="number" value={form.equity_budget}
                onChange={(e) => setForm({ ...form, equity_budget: +e.target.value })} className="h-8 text-xs" />
            </div>
            <div>
              <Label className="text-[10px] text-muted-foreground">单笔风险 %</Label>
              <Input type="number" step={0.5} value={form.risk_pct}
                onChange={(e) => setForm({ ...form, risk_pct: +e.target.value })} className="h-8 text-xs" />
            </div>
            <div>
              <Label className="text-[10px] text-muted-foreground">VT / 杠杆上限</Label>
              <div className="flex gap-1">
                <Input type="number" step={0.05} value={form.vol_target}
                  onChange={(e) => setForm({ ...form, vol_target: +e.target.value })} className="h-8 text-xs" />
                <Input type="number" step={0.5} value={form.max_leverage}
                  onChange={(e) => setForm({ ...form, max_leverage: +e.target.value })} className="h-8 text-xs" />
              </div>
            </div>
          </div>
          {info && Object.keys(info.params).length > 0 && (
            <div>
              <div className="section-label mb-2">策略参数</div>
              <div className="grid grid-cols-2 gap-2">
                {Object.entries(info.params).map(([k, spec]) => (
                  <div key={k}>
                    <Label className="text-[10px] text-muted-foreground">{k}</Label>
                    <Input type="number" step={spec.type === "int" ? 1 : 0.1}
                      value={params[k] ?? spec.default}
                      onChange={(e) => setParams({ ...params, [k]: +e.target.value })}
                      className="h-8 text-xs" />
                  </div>
                ))}
              </div>
            </div>
          )}
          <Button onClick={submit} disabled={busy} className="h-8 w-full text-xs">创建</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default function ExecutorsPage() {
  const [executors, setExecutors] = useState<Executor[]>([]);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [jobs, setJobs] = useState<SchedulerJob[]>([]);
  const [current, setCurrent] = useState<Executor | null>(null);
  const [signal, setSignal] = useState<ExecutorSignal | null>(null);
  const [logs, setLogs] = useState<SignalLog[]>([]);
  const [equityPts, setEquityPts] = useState<{ t: string; equity: number; cash: number; unrealized: number }[]>([]);
  const [bars, setBars] = useState<Candle[]>([]);
  const [barsLoading, setBarsLoading] = useState(false);
  const [signalBusy, setSignalBusy] = useState(false);
  const [, setSeenTick] = useState(0);
  // 抽屉打开时的已读水位：流水里 id 大于它的行视为"新信号"高亮
  const seenAtOpen = useRef<Record<string, number>>({});

  const reload = useCallback(async (silent = false) => {
    try {
      const [ex, sc] = await Promise.all([api.executors(), api.scheduler()]);
      setExecutors(ex);
      setJobs(sc.jobs);
      // 抽屉开着时同步最新状态（持仓/权益/状态机），不打断用户查看
      setCurrent((cur) => (cur ? ex.find((e) => e.id === cur.id) ?? cur : cur));
    } catch {
      if (!silent) toast.error("无法连接后端");
    }
  }, []);

  useEffect(() => {
    reload();
    api.strategies().then((r) => setStrategies(r.strategies)).catch(() => {});
    const id = setInterval(() => reload(true), 5_000);   // 列表 5s 静默轮询
    return () => clearInterval(id);
  }, [reload]);

  const markSeen = (executorId: string, signals: SignalLog[]) => {
    const maxId = Math.max(0, ...signals.map((s) => s.id));
    if (maxId) {
      seenSignals.mark(executorId, maxId);
      setSeenTick((t) => t + 1);
    }
  };

  // 抽屉打开期间：流水与权益 5s 轮询；K 线 30s 一拍；看流水即视为已读
  useEffect(() => {
    if (!current) return;
    let tick = 0;
    const id = setInterval(async () => {
      tick += 1;
      try {
        const lg = await api.executorSignals(current.id);
        setLogs(lg.signals);
        markSeen(current.id, lg.signals);
      } catch { /* 静默，下一拍重试 */ }
      try {
        const eq = await api.executorEquity(current.id);
        setEquityPts(eq.points);
      } catch { /* 静默 */ }
      if (tick % 6 === 1) {
        try {
          const b = await api.executorBars(current.id);
          setBars(b.bars);
        } catch { /* 静默 */ }
      }
    }, 5_000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current?.id]);

  const openDetail = async (ex: Executor) => {
    seenAtOpen.current[ex.id] = seenSignals.get()[ex.id] ?? 0;
    setCurrent(ex);
    setSignal(null);
    setBars([]);
    try {
      const lg = await api.executorSignals(ex.id);
      setLogs(lg.signals);
      markSeen(ex.id, lg.signals);
    } catch { setLogs([]); }
    try {
      const eq = await api.executorEquity(ex.id);
      setEquityPts(eq.points);
    } catch { setEquityPts([]); }
    setBarsLoading(true);
    try {
      const b = await api.executorBars(ex.id);
      setBars(b.bars);
    } catch { /* 图表区显示空态 */ } finally {
      setBarsLoading(false);
    }
  };

  // 信号流水 → K 线标记：开仓箭头 / 平仓圆点（配对开仓价定盈亏色）/ 重锚小方块
  const markers: TradeMarker[] = (() => {
    const chrono = [...logs].reverse();          // 流水是倒序，翻回时间正序配对
    const out: TradeMarker[] = [];
    let open: { side: 1 | -1; px: number | null } | null = null;
    for (const lg of chrono) {
      if (!lg.bar_time) continue;
      const closePx = typeof lg.detail?.close === "number" ? lg.detail.close : null;
      if (lg.action === "open" && lg.side) {
        open = { side: lg.side > 0 ? 1 : -1, px: closePx };
        out.push({ t: lg.bar_time, side: open.side, phase: "entry",
                   text: open.side > 0 ? "开多" : "开空" });
      } else if (lg.action === "close" && open) {
        const win = open.px != null && closePx != null
          ? (closePx - open.px) * open.side > 0 : undefined;
        out.push({ t: lg.bar_time, side: open.side, phase: "exit", win, text: "平" });
        open = null;
      } else if (lg.action === "trail_stop" && lg.side) {
        out.push({ t: lg.bar_time, side: lg.side > 0 ? 1 : -1, phase: "trail", text: "锚" });
      }
    }
    return out;
  })();

  const toggle = async (ex: Executor) => {
    const next = ex.status === "running" ? "paused" : "running";
    try {
      await api.updateExecutor(ex.id, { status: next });
      toast.success(next === "running" ? `已启动：${ex.name}` : `已暂停：${ex.name}`);
      reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };

  const fetchSignal = async () => {
    if (!current) return;
    setSignalBusy(true);
    try {
      setSignal(await api.executorSignal(current.id));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setSignalBusy(false);
    }
  };

  const nextRun = (id: string) => jobs.find((j) => j.executor_id === id)?.next_run;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <span className="section-label">EXECUTORS / 执行器（{executors.length}）</span>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" className="h-8 text-xs" onClick={() => reload()}>
            <RefreshCw size={12} className="mr-1" />刷新
          </Button>
          <CreateExecutorDialog strategies={strategies} onCreated={() => reload()} />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {executors.length === 0 ? (
          <div className="m-4 flex h-48 items-center justify-center border border-dashed border-border text-xs text-muted-foreground">
            &gt; 还没有执行器。从策略模板创建一个，启动后它将按周期自动产生信号。
          </div>
        ) : (
          <Table>
            <TableHeader className="sticky top-0 bg-card">
              <TableRow className="border-border hover:bg-transparent">
                <TableHead className="text-[10px]">状态</TableHead>
                <TableHead className="text-[10px]">名称</TableHead>
                <TableHead className="text-[10px]">品种</TableHead>
                <TableHead className="text-[10px]">周期</TableHead>
                <TableHead className="text-[10px] text-right">风险</TableHead>
                <TableHead className="text-[10px] text-right">预算</TableHead>
                <TableHead className="text-[10px] text-right">账面权益</TableHead>
                <TableHead className="text-[10px]">持仓</TableHead>
                <TableHead className="text-[10px]">下次触发</TableHead>
                <TableHead className="text-[10px] text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {executors.map((ex) => {
                const st = STATUS_LED[ex.status] ?? STATUS_LED.paused;
                const pos = ex.state?.position;
                const unread = ex.latest_signal != null
                  && ex.latest_signal.id > (seenSignals.get()[ex.id] ?? 0);
                const latestAction = ex.latest_signal
                  ? (ACTION_LABEL[ex.latest_signal.action]?.label ?? ex.latest_signal.action)
                  : null;
                return (
                  <TableRow key={ex.id} className="cursor-pointer border-border/60 transition-colors hover:bg-panel"
                    onClick={() => openDetail(ex)}>
                    <TableCell className="py-2">
                      <span className="flex items-center gap-1.5 text-[11px]">
                        <span className={st.cls} />{st.label}
                      </span>
                    </TableCell>
                    <TableCell className="py-2 text-[11px] font-medium">
                      {ex.name}
                      {unread && (
                        <span className="ml-2 inline-flex items-center gap-1 text-[10px] text-primary"
                              title={`新信号：${latestAction}`}>
                          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
                          {latestAction}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="py-2 font-mono text-[11px]">{ex.symbol}</TableCell>
                    <TableCell className="py-2 text-[11px] text-muted-foreground">{ex.timeframe}</TableCell>
                    <TableCell className="num py-2 text-right text-[11px]">{pct(ex.risk_frac, 1).replace("+", "")}</TableCell>
                    <TableCell className="num py-2 text-right text-[11px]">{num(ex.equity_budget, 0)}</TableCell>
                    <TableCell className="num py-2 text-right text-[11px]">
                      {ex.state?.last_equity != null ? (() => {
                        const pnlR = ex.state.last_equity! / ex.equity_budget - 1;
                        return (
                          <span className={pnlClass(pnlR)}>
                            {num(ex.state.last_equity, 1)}
                            <span className="ml-1 text-[10px] opacity-80">{pct(pnlR, 1)}</span>
                          </span>
                        );
                      })() : <span className="text-muted-foreground">—</span>}
                    </TableCell>
                    <TableCell className="py-2 text-[11px]">
                      {pos ? (
                        <span className={pos.side > 0 ? "text-up" : "text-down"}>
                          {pos.side > 0 ? "多" : "空"} {num(pos.qty, 4)}
                        </span>
                      ) : <span className="text-muted-foreground">空仓</span>}
                    </TableCell>
                    <TableCell className="num py-2 text-[11px] text-muted-foreground">
                      {ex.status === "running" ? ts(nextRun(ex.id)) : "—"}
                    </TableCell>
                    <TableCell className="py-2 text-right" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center justify-end gap-1">
                        {ex.status !== "stopped" && (
                          <Button size="sm" variant="ghost" className="h-7 w-7 p-0" onClick={() => toggle(ex)}
                            title={ex.status === "running" ? "暂停" : "启动"}>
                            {ex.status === "running" ? <Pause size={12} /> : <Play size={12} />}
                          </Button>
                        )}
                        {ex.status !== "stopped" && (
                          <AlertDialog>
                            <AlertDialogTrigger asChild>
                              <Button size="sm" variant="ghost" className="h-7 w-7 p-0 text-muted-foreground hover:text-down" title="停止">
                                <Trash2 size={12} />
                              </Button>
                            </AlertDialogTrigger>
                            <AlertDialogContent className="border-border bg-card">
                              <AlertDialogHeader>
                                <AlertDialogTitle className="text-sm">停止执行器 {ex.name}？</AlertDialogTitle>
                                <AlertDialogDescription className="text-xs">
                                  停止后将摘除调度任务。当前为 dry-run 模式，不会产生真实平仓单。
                                </AlertDialogDescription>
                              </AlertDialogHeader>
                              <AlertDialogFooter>
                                <AlertDialogCancel className="h-8 text-xs">取消</AlertDialogCancel>
                                <AlertDialogAction className="h-8 bg-destructive text-xs text-destructive-foreground"
                                  onClick={async () => {
                                    try {
                                      await api.deleteExecutor(ex.id);
                                      toast.success("已停止");
                                      reload();
                                    } catch (err) {
                                      toast.error(err instanceof Error ? err.message : String(err));
                                    }
                                  }}>
                                  确认停止
                                </AlertDialogAction>
                              </AlertDialogFooter>
                            </AlertDialogContent>
                          </AlertDialog>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </div>

      {/* 执行器详情抽屉 */}
      <Sheet open={!!current} onOpenChange={(o) => !o && setCurrent(null)}>
        <SheetContent className="w-[560px] overflow-y-auto border-l border-border bg-card sm:max-w-[560px]">
          {current && (
            <>
              <SheetHeader>
                <SheetTitle className="flex items-center gap-2 text-sm">
                  <span className={STATUS_LED[current.status]?.cls ?? "led-off"} />
                  {current.name}
                </SheetTitle>
              </SheetHeader>
              <div className="mt-4 space-y-4">
                <div className="grid grid-cols-2 gap-2 text-[11px]">
                  <div className="border border-border p-2"><div className="section-label">品种/周期</div>
                    <div className="mt-1 font-mono">{current.symbol} · {current.timeframe}</div></div>
                  <div className="border border-border p-2"><div className="section-label">风险/预算</div>
                    <div className="num mt-1">{pct(current.risk_frac, 1).replace("+", "")} · {num(current.equity_budget, 0)}U</div></div>
                </div>

                {/* 账面权益：dry-run 记账曲线 */}
                <div>
                  <div className="section-label mb-2">账面权益（DRY-RUN）</div>
                  {equityPts.length > 0 ? (() => {
                    const latest = equityPts[equityPts.length - 1];
                    const pnl = latest.equity - current.equity_budget;
                    return (
                      <>
                        <div className="mb-2 grid grid-cols-3 gap-2">
                          <Metric label="账面权益" value={`${num(latest.equity, 2)}U`} />
                          <Metric label="累计盈亏" value={`${pnl >= 0 ? "+" : ""}${num(pnl, 2)}U`}
                            tone={pnl > 0 ? "up" : pnl < 0 ? "down" : "plain"} />
                          <Metric label="持仓浮盈" value={`${latest.unrealized >= 0 ? "+" : ""}${num(latest.unrealized, 2)}U`}
                            tone={latest.unrealized > 0 ? "up" : latest.unrealized < 0 ? "down" : "plain"} />
                        </div>
                        <div className="border border-border p-1">
                          <EquityChart
                            series={[{ name: "equity", color: "#22d3ee", width: 2,
                              points: equityPts.map((p) => ({ t: p.t, v: p.equity })) }]}
                            height={170} />
                        </div>
                      </>
                    );
                  })() : (
                    <div className="flex h-20 items-center justify-center border border-dashed border-border text-[11px] text-muted-foreground">
                      暂无记账（启动后每根 bar 收盘落一条快照）
                    </div>
                  )}
                </div>

                {current.state?.position && (
                  <div className="border border-border bg-panel p-3">
                    <div className="section-label mb-2">当前持仓</div>
                    <div className="grid grid-cols-3 gap-2 text-[11px]">
                      <div>方向 <span className={current.state.position.side > 0 ? "text-up" : "text-down"}>
                        {current.state.position.side > 0 ? "多" : "空"}</span></div>
                      <div>数量 <span className="num">{num(current.state.position.qty, 4)}</span></div>
                      <div>止损 <span className="num">{px(current.state.position.current_stop)}</span></div>
                    </div>
                  </div>
                )}

                <div>
                  <div className="mb-2 flex items-center justify-between">
                    <span className="section-label">当前信号（DRY-RUN）</span>
                    <Button size="sm" variant="outline" className="h-7 text-[11px]" onClick={fetchSignal} disabled={signalBusy}>
                      {signalBusy ? "计算中…" : "计算当前信号"}
                    </Button>
                  </div>
                  {signal ? (
                    <div className="grid grid-cols-2 gap-2">
                      <Metric label="入场信号"
                        value={signal.entry_signal > 0 ? "多 ▲" : signal.entry_signal < 0 ? "空 ▼" : "无"}
                        tone={signal.entry_signal > 0 ? "up" : signal.entry_signal < 0 ? "down" : "plain"} />
                      <Metric label="出场信号" value={signal.exit_signal ? "是" : "否"} />
                      <Metric label="收盘价" value={px(signal.close)} />
                      <Metric label="ATR / 斜率" value={`${px(signal.atr)} / ${num(signal.slope, 2)}`} />
                      {signal.suggested_qty > 0 && <>
                        <Metric label="建议数量" value={num(signal.suggested_qty, 4)} />
                        <Metric label="建议止损" value={px(signal.suggested_stop)} />
                      </>}
                      <div className="col-span-2 text-[10px] text-muted-foreground">
                        基于 {ts(signal.as_of)} 已收盘 bar · {signal.note}
                      </div>
                    </div>
                  ) : (
                    <div className="flex h-20 items-center justify-center border border-dashed border-border text-[11px] text-muted-foreground">
                      点击"计算当前信号"获取实时快照
                    </div>
                  )}
                </div>

                {/* K 线 + 信号标记：开平仓/重锚位置对照 */}
                <div>
                  <div className="mb-2 flex items-center justify-between">
                    <span className="section-label">K 线 · {current.symbol} {current.timeframe}</span>
                    <span className="text-[10px] text-muted-foreground">
                      ▲开多 ▼开空 ●平 ■重锚
                    </span>
                  </div>
                  {bars.length > 0 ? (
                    <div className="border border-border p-1">
                      <CandleChart candles={bars} markers={markers} height={230} />
                    </div>
                  ) : (
                    <div className="flex h-20 items-center justify-center border border-dashed border-border text-[11px] text-muted-foreground">
                      {barsLoading ? "K 线加载中…" : "K 线暂不可用（交易所数据获取失败）"}
                    </div>
                  )}
                </div>

                <div>
                  <div className="section-label mb-2">信号流水</div>
                  {logs.length > 0 ? (
                    <Table>
                      <TableHeader>
                        <TableRow className="border-border hover:bg-transparent">
                          <TableHead className="text-[10px]">时间</TableHead>
                          <TableHead className="text-[10px]">动作</TableHead>
                          <TableHead className="text-[10px] text-right">止损</TableHead>
                          <TableHead className="text-[10px] text-right">斜率</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {logs.map((lg) => {
                          const a = ACTION_LABEL[lg.action] ?? { label: lg.action, cls: "text-muted-foreground" };
                          const isNew = lg.id > (seenAtOpen.current[current.id] ?? 0);
                          return (
                            <TableRow key={lg.id}
                              className={`border-border/60 hover:bg-panel ${isNew ? "bg-primary/5" : ""}`}>
                              <TableCell className="num py-1.5 text-[11px] text-muted-foreground">
                                {isNew && <span className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-primary" />}
                                {ts(lg.bar_time ?? lg.created_at)}
                              </TableCell>
                              <TableCell className={`py-1.5 text-[11px] ${a.cls}`}>{a.label}</TableCell>
                              <TableCell className="num py-1.5 text-right text-[11px]">{px(lg.stop)}</TableCell>
                              <TableCell className={`num py-1.5 text-right text-[11px] ${pnlClass(lg.slope)}`}>{num(lg.slope, 1)}</TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  ) : (
                    <div className="flex h-20 items-center justify-center border border-dashed border-border text-[11px] text-muted-foreground">
                      暂无流水（启动后每根 bar 收盘落一条）
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
