import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { ChevronDown, ChevronRight, Play } from "lucide-react";
import {
  api, TIMEFRAMES,
  type CostSensitivityJob, type StrategyInfo,
} from "@/lib/api";
import { num, pct } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

function parseList(s: string): number[] {
  return s.split(/[,，\s]+/).map((x) => x.trim()).filter(Boolean)
    .map(Number).filter((x) => !Number.isNaN(x));
}

type MetricKey = "annualized" | "sharpe" | "max_drawdown";
const METRIC_LABEL: Record<MetricKey, string> = {
  annualized: "年化", sharpe: "夏普", max_drawdown: "最大回撤",
};

/** 单元格底色：正绿负红，透明度随 |值|/max|v| 加深。 */
function cellBg(v: number | undefined, maxAbs: number): string {
  if (v == null || !Number.isFinite(v) || maxAbs <= 0) return "transparent";
  const a = 0.08 + 0.3 * Math.min(Math.abs(v) / maxAbs, 1);
  return v >= 0 ? `rgba(52, 211, 153, ${a})` : `rgba(248, 113, 113, ${a})`;
}

/** 成本敏感性面板：费率 × 滑点网格重跑，热力表看策略对成本的耐受度。 */
export default function CostSensitivityPanel() {
  const [open, setOpen] = useState(false);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [form, setForm] = useState({
    strategy: "ema_pullback_v1", symbols: "BTCUSDT", timeframe: "1d",
    start: "2023-01-01", risk_pct: 5, vol_target: 0.25,
    fees_bp: "2,5,10", slippage_bp: "1,2,5",
  });
  const [metric, setMetric] = useState<MetricKey>("annualized");
  const [job, setJob] = useState<CostSensitivityJob | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    api.strategies().then((r) => setStrategies(r.strategies)).catch(() => { /* 可降级 */ });
  }, []);

  const stopPoll = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  };
  useEffect(() => stopPoll, []);

  const fees = useMemo(() => parseList(form.fees_bp).map((x) => x / 10000), [form.fees_bp]);
  const slips = useMemo(() => parseList(form.slippage_bp).map((x) => x / 10000), [form.slippage_bp]);
  const cells = fees.length * slips.length;
  const running = job?.status === "running";

  const submit = async () => {
    const symbols = form.symbols.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean);
    if (!symbols.length || !fees.length || !slips.length) {
      toast.error("交易对 / 费率 / 滑点都不能为空");
      return;
    }
    try {
      const j = await api.costSensitivity({
        strategy: form.strategy, symbols, timeframe: form.timeframe,
        start: form.start || null, params: {},
        risk_frac: form.risk_pct / 100, vol_target: form.vol_target,
        max_leverage: 3, initial_equity: 10000, fees, slippages: slips,
      });
      setJob({ ...j, status: "running" });
      stopPoll();
      pollRef.current = setInterval(async () => {
        try {
          const st = await api.costSensitivityStatus(j.cs_id);
          setJob(st);
          if (st.status !== "running") {
            stopPoll();
            if (st.status === "failed") toast.error(`成本敏感性失败：${st.error?.split("\n")[0]}`);
          }
        } catch { /* 下一拍重试 */ }
      }, 1500);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };

  const res = job?.result;
  const maxAbs = useMemo(() => {
    if (!res) return 0;
    return Math.max(...res.cells.map((c) => Math.abs(c[metric] ?? 0)), 0);
  }, [res, metric]);
  const cellAt = (fee: number, slip: number) =>
    res?.cells.find((c) => Math.abs(c.fee - fee) < 1e-12 && Math.abs(c.slippage - slip) < 1e-12);
  const fmtCell = (v: number | undefined) =>
    v == null ? "—" : metric === "sharpe" ? num(v) : pct(v);

  return (
    <div className="rounded-md border border-border bg-card">
      <button className="flex w-full items-center gap-2 px-3 py-2.5 text-left"
        onClick={() => setOpen((o) => !o)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="section-label">COST / 成本敏感性</span>
        <span className="text-[10px] text-muted-foreground">
          费率 × 滑点网格重跑：同一交易序列在不同成本下还能剩多少收益
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
              <Label className="text-[10px] text-muted-foreground">交易对</Label>
              <Input value={form.symbols} onChange={(e) => setForm({ ...form, symbols: e.target.value })}
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
              <Label className="text-[10px] text-muted-foreground">费率网格 bp</Label>
              <Input value={form.fees_bp} placeholder="2,5,10"
                onChange={(e) => setForm({ ...form, fees_bp: e.target.value })}
                className="h-8 font-mono text-xs" />
            </div>
            <div>
              <Label className="text-[10px] text-muted-foreground">滑点网格 bp</Label>
              <Input value={form.slippage_bp} placeholder="1,2,5"
                onChange={(e) => setForm({ ...form, slippage_bp: e.target.value })}
                className="h-8 font-mono text-xs" />
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
            <div className="flex items-end">
              <Button size="sm" className="h-8 w-full text-xs" onClick={submit} disabled={running || cells > 16}>
                <Play size={12} className="mr-1" />
                {running ? `重跑中…（${job?.done ?? 0}/${job?.total ?? cells}）` : `重跑 ${cells} 格`}
              </Button>
            </div>
          </div>
          {cells > 16 && (
            <div className="text-[10px] text-down">网格 {cells} 格超过上限 16，请收敛费率或滑点候选</div>
          )}

          {res && (
            <div className="space-y-2 border-t border-border pt-3">
              <div className="flex items-center gap-1">
                <span className="section-label mr-2">热力指标</span>
                {(Object.keys(METRIC_LABEL) as MetricKey[]).map((m) => (
                  <button key={m} onClick={() => setMetric(m)}
                    className={`px-2.5 py-1 text-[10px] tracking-wider transition-colors ${
                      metric === m ? "bg-primary/15 text-primary" : "text-muted-foreground hover:text-foreground"
                    }`}>
                    {METRIC_LABEL[m]}
                  </button>
                ))}
              </div>
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-center">
                  <thead>
                    <tr>
                      <th className="p-1.5 text-left text-[10px] font-normal text-muted-foreground">费率 \ 滑点</th>
                      {res.slippages.map((s) => (
                        <th key={s} className="num p-1.5 text-[10px] font-normal text-muted-foreground">
                          {(s * 10000).toFixed(0)}bp
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {res.fees.map((f) => (
                      <tr key={f}>
                        <td className="num p-1.5 text-left text-[10px] text-muted-foreground">
                          {(f * 10000).toFixed(0)}bp
                        </td>
                        {res.slippages.map((s) => {
                          const c = cellAt(f, s);
                          const v = c?.[metric];
                          return (
                            <td key={s}
                              title={c && !c.error
                                ? `总收益 ${pct(c.total_return)} · 夏普 ${num(c.sharpe)} · 回撤 ${pct(c.max_drawdown)} · ${c.trades ?? "—"} 笔`
                                : c?.error ?? ""}
                              className="num border border-border/50 p-2 text-[11px]"
                              style={{ background: cellBg(v, maxAbs) }}>
                              {c?.error ? "×" : fmtCell(v)}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="text-[10px] text-muted-foreground">
                全部单元格共享一份 K 线，交易序列一致；悬停查看完整指标。成本越高颜色越淡 = 策略利润被成本吃掉
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
