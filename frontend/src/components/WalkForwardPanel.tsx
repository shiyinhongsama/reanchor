import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { ChevronDown, ChevronRight, Play } from "lucide-react";
import {
  api, TIMEFRAMES,
  type StrategyInfo, type WalkForwardJob,
} from "@/lib/api";
import { num, pct, pnlClass } from "@/lib/format";
import EquityChart from "@/components/EquityChart";
import Metric from "@/components/Metric";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

/** 解析逗号/空白分隔的数字列表，非法项丢弃。 */
function parseList(s: string): number[] {
  return s.split(/[,，\s]+/).map((x) => x.trim()).filter(Boolean)
    .map(Number).filter((x) => !Number.isNaN(x));
}

/** 走步验证面板：切折 → 训练段寻优 → 样本外测试 → OOS 净值拼接。 */
export default function WalkForwardPanel() {
  const [open, setOpen] = useState(false);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [form, setForm] = useState({
    strategy: "ema_pullback_v1", symbol: "BTCUSDT", timeframe: "1d",
    start: "2023-01-01", folds: 5, train_frac: 0.7, criterion: "calmar",
    risk_pct: 5, vol_target: 0.25,
  });
  const [grid, setGrid] = useState<Record<string, string>>({});
  const [job, setJob] = useState<WalkForwardJob | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    api.strategies().then((r) => setStrategies(r.strategies)).catch(() => { /* 可降级 */ });
  }, []);
  const schema = strategies.find((s) => s.name === form.strategy)?.params ?? {};
  const numParams = Object.entries(schema).filter(([, spec]) => !spec.options);

  const stopPoll = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  };
  useEffect(() => stopPoll, []);

  const combos = useMemo(() => {
    let p = 1;
    for (const v of Object.values(grid)) {
      const n = parseList(v).length;
      if (n) p *= n;
    }
    return p;
  }, [grid]);

  const running = job?.status === "running";

  const submit = async () => {
    const paramGrid = Object.fromEntries(
      Object.entries(grid).map(([k, v]) => [k, parseList(v)] as const).filter(([, v]) => v.length));
    try {
      const { wf_id } = await api.walkforward({
        strategy: form.strategy, symbol: form.symbol.trim(), timeframe: form.timeframe,
        start: form.start || null, folds: form.folds, train_frac: form.train_frac,
        criterion: form.criterion, params: {}, param_grid: paramGrid,
        risk_frac: form.risk_pct / 100, vol_target: form.vol_target,
        max_leverage: 3, fee: 0.0005, slippage: 0.0002, initial_equity: 10000,
      });
      setJob({ wf_id, status: "running" });
      stopPoll();
      pollRef.current = setInterval(async () => {
        try {
          const j = await api.walkforwardStatus(wf_id);
          setJob(j);
          if (j.status !== "running") {
            stopPoll();
            if (j.status === "failed") toast.error(`走步验证失败：${j.error?.split("\n")[0]}`);
          }
        } catch { /* 下一拍重试 */ }
      }, 2000);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };

  const res = job?.result;

  return (
    <div className="rounded-md border border-border bg-card">
      <button className="flex w-full items-center gap-2 px-3 py-2.5 text-left"
        onClick={() => setOpen((o) => !o)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="section-label">WALK-FORWARD / 走步验证</span>
        <span className="text-[10px] text-muted-foreground">
          切折 → 训练段寻优 → 样本外评估，检验参数的时间稳定性
        </span>
      </button>

      {open && (
        <div className="space-y-3 border-t border-border px-3 py-3">
          <div className="grid grid-cols-4 gap-2">
            <div>
              <Label className="text-[10px] text-muted-foreground">策略模板</Label>
              <Select value={form.strategy} onValueChange={(v) => setForm({ ...form, strategy: v })}>
                <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {strategies.map((s) => <SelectItem key={s.name} value={s.name} className="text-xs">{s.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-[10px] text-muted-foreground">交易对（单品种）</Label>
              <Input value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })}
                className="h-8 font-mono text-xs" />
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
              <Label className="text-[10px] text-muted-foreground">开始</Label>
              <Input type="date" value={form.start}
                onChange={(e) => setForm({ ...form, start: e.target.value })} className="h-8 text-xs" />
            </div>
            <div>
              <Label className="text-[10px] text-muted-foreground">折数 [2–10]</Label>
              <Input type="number" min={2} max={10} value={form.folds}
                onChange={(e) => setForm({ ...form, folds: +e.target.value })} className="h-8 text-xs" />
            </div>
            <div>
              <Label className="text-[10px] text-muted-foreground">训练占比 [0.3–0.95]</Label>
              <Input type="number" step={0.05} value={form.train_frac}
                onChange={(e) => setForm({ ...form, train_frac: +e.target.value })} className="h-8 text-xs" />
            </div>
            <div>
              <Label className="text-[10px] text-muted-foreground">寻优准则</Label>
              <Select value={form.criterion} onValueChange={(v) => setForm({ ...form, criterion: v })}>
                <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="calmar" className="text-xs">calmar</SelectItem>
                  <SelectItem value="sharpe" className="text-xs">sharpe</SelectItem>
                  <SelectItem value="annualized" className="text-xs">annualized</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-[10px] text-muted-foreground">风险 % / VT</Label>
              <div className="flex gap-1">
                <Input type="number" step={0.5} value={form.risk_pct}
                  onChange={(e) => setForm({ ...form, risk_pct: +e.target.value })} className="h-8 text-xs" />
                <Input type="number" step={0.05} value={form.vol_target}
                  onChange={(e) => setForm({ ...form, vol_target: +e.target.value })} className="h-8 text-xs" />
              </div>
            </div>
          </div>

          {numParams.length > 0 && (
            <div>
              <div className="section-label mb-1.5">寻优网格（逗号分隔；留空 = 固定默认值）</div>
              <div className="grid grid-cols-4 gap-2">
                {numParams.map(([k, spec]) => (
                  <div key={k}>
                    <Label className="text-[10px] text-muted-foreground">{k}（默认 {String(spec.default)}）</Label>
                    <Input value={grid[k] ?? ""} placeholder="如 5,8"
                      onChange={(e) => setGrid({ ...grid, [k]: e.target.value })}
                      className="h-8 font-mono text-xs" />
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="flex items-center gap-3">
            <Button size="sm" className="h-8 text-xs" onClick={submit} disabled={running}>
              <Play size={12} className="mr-1" />
              {running ? `验证中…（第 ${job?.done ?? 0} 折）` : "开始验证"}
            </Button>
            <span className="text-[10px] text-muted-foreground">
              每折 {combos} 组参数 × {form.folds} 折；测试段平地起跑，不继承训练段持仓
            </span>
          </div>

          {res && (
            <div className="space-y-3 border-t border-border pt-3">
              <div className="grid grid-cols-6 gap-2">
                <Metric label="盈利折数" value={res.aggregate.profitable_folds} />
                <Metric label="OOS 平均年化" value={pct(res.aggregate.mean_annualized)}
                  tone={pnlClass(res.aggregate.mean_annualized) === "text-up" ? "up" : "down"} />
                <Metric label="OOS 年化标准差" value={pct(res.aggregate.std_annualized)} />
                <Metric label="OOS 平均夏普" value={num(res.aggregate.mean_sharpe)} />
                <Metric label="最差单折回撤" value={pct(res.aggregate.worst_drawdown)} tone="down" />
                <Metric label="拼接净值夏普" value={num(res.oos_metrics?.sharpe)} />
              </div>

              {res.oos_equity.length > 1 && (
                <div className="border border-border p-1">
                  <EquityChart height={200}
                    series={[{ name: "OOS 拼接净值", color: "#22d3ee", width: 2, points: res.oos_equity }]} />
                </div>
              )}

              <Table>
                <TableHeader>
                  <TableRow className="border-border hover:bg-transparent">
                    <TableHead className="text-[10px]">折</TableHead>
                    <TableHead className="text-[10px]">测试段</TableHead>
                    <TableHead className="text-[10px]">选中参数</TableHead>
                    <TableHead className="text-[10px] text-right">训练年化</TableHead>
                    <TableHead className="text-[10px] text-right">OOS 年化</TableHead>
                    <TableHead className="text-[10px] text-right">OOS 夏普</TableHead>
                    <TableHead className="text-[10px] text-right">OOS 回撤</TableHead>
                    <TableHead className="text-[10px] text-right">笔数</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {res.folds.map((f) => (
                    <TableRow key={f.i} className="border-border/60 hover:bg-panel">
                      <TableCell className="num py-1.5 text-[11px]">#{f.i + 1}</TableCell>
                      <TableCell className="num py-1.5 text-[11px] text-muted-foreground">
                        {f.test[0].slice(0, 10)} ~ {f.test[1].slice(0, 10)}
                      </TableCell>
                      <TableCell className="max-w-[180px] truncate py-1.5 text-[11px] text-muted-foreground">
                        {Object.entries(f.params).map(([k, v]) => `${k}=${v}`).join(" ") || "默认"}
                      </TableCell>
                      <TableCell className={`num py-1.5 text-right text-[11px] ${pnlClass(f.train_metrics?.annualized)}`}>
                        {pct(f.train_metrics?.annualized)}
                      </TableCell>
                      <TableCell className={`num py-1.5 text-right text-[11px] ${pnlClass(f.test_metrics?.annualized)}`}>
                        {pct(f.test_metrics?.annualized)}
                      </TableCell>
                      <TableCell className="num py-1.5 text-right text-[11px]">{num(f.test_metrics?.sharpe)}</TableCell>
                      <TableCell className="num py-1.5 text-right text-[11px] text-down">{pct(f.test_metrics?.max_drawdown)}</TableCell>
                      <TableCell className="num py-1.5 text-right text-[11px]">{f.test_metrics?.trades ?? "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <div className="text-[10px] text-muted-foreground">
                寻优准则：{res.criterion}（训练段）· 训练年化与 OOS 年化差距越小、折间参数越稳定，策略越可信
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
