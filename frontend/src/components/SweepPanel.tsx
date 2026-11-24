import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { ChevronDown, ChevronRight, Play } from "lucide-react";
import { api, TIMEFRAMES, type StrategyInfo, type SweepJob } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const MAX_COMBOS = 24;

/** 解析逗号/空白分隔的数字列表，非法项丢弃。 */
function parseList(s: string): number[] {
  return s.split(/[,，\s]+/).map((x) => x.trim()).filter(Boolean)
    .map(Number).filter((x) => !Number.isNaN(x));
}

/** 参数扫描面板：网格展开 → 批量回测 → 结果自动进存档列表。 */
export default function SweepPanel({ onDone }: { onDone: (jobIds: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [form, setForm] = useState({
    symbols: "BTCUSDT,ETHUSDT", timeframe: "1d", start: "2024-01-01",
    risks: "5,15", vts: "0.25",
  });
  const [grid, setGrid] = useState<Record<string, string>>({});
  const [sweep, setSweep] = useState<SweepJob | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    api.strategies().then((r) => setStrategies(r.strategies)).catch(() => { /* 面板可降级 */ });
  }, []);
  const schema = strategies.find((s) => s.name === "ema_pullback_v1")?.params ?? {};

  const stopPoll = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  };
  useEffect(() => stopPoll, []);

  const combos = useMemo(() => {
    const r = parseList(form.risks).length || 1;
    const v = parseList(form.vts).length || 1;
    let p = 1;
    for (const val of Object.values(grid)) {
      const n = parseList(val).length;
      if (n) p *= n;
    }
    return r * v * p;
  }, [form.risks, form.vts, grid]);

  const running = sweep?.status === "running";

  const submit = async () => {
    const symbols = form.symbols.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean);
    const risks = parseList(form.risks).map((x) => x / 100);
    const vts = parseList(form.vts);
    const paramGrid = Object.fromEntries(
      Object.entries(grid).map(([k, v]) => [k, parseList(v)] as const).filter(([, v]) => v.length));
    if (!symbols.length) { toast.error("至少填写一个交易对"); return; }
    if (!risks.length) { toast.error("至少一个风险值"); return; }
    if (combos > MAX_COMBOS) { toast.error(`组合数 ${combos} 超过上限 ${MAX_COMBOS}`); return; }
    try {
      const sj = await api.createSweep({
        strategy: "ema_pullback_v1", symbols, timeframe: form.timeframe,
        start: form.start || null,
        risk_fracs: risks, vol_targets: vts.length ? vts : [0.25], param_grid: paramGrid,
      });
      setSweep(sj);
      stopPoll();
      pollRef.current = setInterval(async () => {
        try {
          const st = await api.sweep(sj.sweep_id);
          setSweep(st);
          if (st.status === "done") {
            stopPoll();
            const failed = st.total - st.done;
            toast.success(`扫描完成：${st.done} 组已存档${failed ? `（${failed} 组失败）` : ""}`);
            onDone(st.job_ids);
          }
        } catch { /* 轮询失败等下一拍 */ }
      }, 3000);
    } catch (e) {
      toast.error(`扫描提交失败：${e instanceof Error ? e.message : e}`);
    }
  };

  const field = "h-8 bg-background text-xs num";

  return (
    <div className="rounded-md border border-border bg-card">
      <button
        className="flex w-full items-center gap-2 px-3 py-2 text-xs tracking-[0.15em] text-muted-foreground hover:text-foreground"
        onClick={() => setOpen((o) => !o)}
      >
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        SWEEP / 参数扫描
        {sweep && (
          <span className={`ml-auto num ${running ? "text-primary" : "text-muted-foreground"}`}>
            {running ? `扫描中 ${sweep.done}/${sweep.total}` : `上次扫描 ${sweep.done}/${sweep.total}`}
          </span>
        )}
      </button>

      {open && (
        <div className="border-t border-border p-3">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <div>
              <Label className="text-[11px] text-muted-foreground">品种（逗号分隔）</Label>
              <Input className={field} value={form.symbols}
                     onChange={(e) => setForm({ ...form, symbols: e.target.value })} />
            </div>
            <div>
              <Label className="text-[11px] text-muted-foreground">周期</Label>
              <Select value={form.timeframe}
                      onValueChange={(v) => setForm({ ...form, timeframe: v })}>
                <SelectTrigger className={field}><SelectValue /></SelectTrigger>
                <SelectContent>
                  {TIMEFRAMES.map((tf) => <SelectItem key={tf} value={tf}>{tf}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-[11px] text-muted-foreground">开始日期</Label>
              <Input className={field} type="date" value={form.start}
                     onChange={(e) => setForm({ ...form, start: e.target.value })} />
            </div>
            <div>
              <Label className="text-[11px] text-muted-foreground">风险 %（网格）</Label>
              <Input className={field} value={form.risks} placeholder="5,15"
                     onChange={(e) => setForm({ ...form, risks: e.target.value })} />
            </div>
            <div>
              <Label className="text-[11px] text-muted-foreground">VT（网格）</Label>
              <Input className={field} value={form.vts} placeholder="0.25"
                     onChange={(e) => setForm({ ...form, vts: e.target.value })} />
            </div>
            {Object.entries(schema).map(([key, meta]) => (
              <div key={key}>
                <Label className="text-[11px] text-muted-foreground">
                  {key}（默认 {meta.default}）
                </Label>
                <Input className={field} value={grid[key] ?? ""} placeholder="留空=默认"
                       onChange={(e) => setGrid({ ...grid, [key]: e.target.value })} />
              </div>
            ))}
          </div>

          <div className="mt-3 flex items-center gap-3">
            <Button size="sm" onClick={submit} disabled={running}>
              <Play size={13} /> {running ? `扫描中 ${sweep?.done}/${sweep?.total}` : "开始扫描"}
            </Button>
            <span className={`num text-[11px] ${combos > MAX_COMBOS ? "text-down" : "text-muted-foreground"}`}>
              组合数 {combos} / {MAX_COMBOS}
            </span>
            {running && (
              <div className="h-1 flex-1 overflow-hidden rounded bg-border">
                <div className="h-full bg-primary transition-all"
                     style={{ width: `${(sweep.done / Math.max(sweep.total, 1)) * 100}%` }} />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
