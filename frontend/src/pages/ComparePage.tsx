import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { RefreshCw, Trash2 } from "lucide-react";
import { api, type BacktestRunSummary, type EquityPoint } from "@/lib/api";
import { num, pct, pnlClass, ts } from "@/lib/format";
import EquityChart, { type EquitySeries } from "@/components/EquityChart";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const COLORS = ["#22d3ee", "#f59e0b", "#a78bfa", "#34d399"];
const MAX_SELECTED = COLORS.length;

/** 存档标签：品种 + 周期 + 风险 + 参数差异，一眼区分多次回测。 */
function runLabel(r: BacktestRunSummary): string {
  const syms = r.symbols.length > 1 ? `${r.symbols.length}品种` : r.symbols[0] ?? "?";
  const ps = Object.entries(r.params).map(([k, v]) => `${k}=${v}`).join(",");
  return `${syms} ${r.timeframe} r${(r.risk_frac * 100).toFixed(0)}%${ps ? " " + ps : ""}`;
}

/** 归一化到首点 = 100，消除初始资金差异。 */
function normalize(points: EquityPoint[]): EquityPoint[] {
  const base = points[0]?.v;
  if (!base) return points;
  return points.map((p) => ({ t: p.t, v: (p.v / base) * 100 }));
}

export default function ComparePage() {
  const [runs, setRuns] = useState<BacktestRunSummary[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [curves, setCurves] = useState<Record<string, EquityPoint[]>>({});
  const [normalized, setNormalized] = useState(true);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const list = (await api.backtestRuns()).filter((r) => r.status === "done");
      setRuns(list);
      setSelected((sel) => sel.filter((id) => list.some((r) => r.job_id === id)));
    } catch {
      toast.error("无法加载回测存档：请确认后端已启动");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // 按需拉取选中回测的组合权益曲线（未选中时释放缓存）
  useEffect(() => {
    let stop = false;
    (async () => {
      const missing = selected.filter((id) => !curves[id]);
      if (!missing.length) return;
      const fetched: Record<string, EquityPoint[]> = {};
      for (const id of missing) {
        try {
          fetched[id] = (await api.backtestEquity(id)).points;
        } catch { /* 单条失败不阻塞其他曲线 */ }
      }
      if (!stop) setCurves((c) => ({ ...c, ...fetched }));
    })();
    return () => { stop = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  const toggle = (id: string) => {
    setSelected((sel) => {
      if (sel.includes(id)) return sel.filter((x) => x !== id);
      if (sel.length >= MAX_SELECTED) {
        toast.warning(`最多同时对比 ${MAX_SELECTED} 条`);
        return sel;
      }
      return [...sel, id];
    });
  };

  const remove = async (id: string) => {
    try {
      await api.deleteBacktest(id);
      setSelected((sel) => sel.filter((x) => x !== id));
      setRuns((rs) => rs.filter((r) => r.job_id !== id));
      toast.success("已删除存档");
    } catch (e) {
      toast.error(`删除失败：${e instanceof Error ? e.message : e}`);
    }
  };

  const selectedRuns = useMemo(
    () => selected.map((id) => runs.find((r) => r.job_id === id)).filter(Boolean) as BacktestRunSummary[],
    [selected, runs]);

  const series: EquitySeries[] = useMemo(() => selectedRuns
    .map((r, i) => {
      const pts = curves[r.job_id];
      if (!pts?.length) return null;
      return {
        name: runLabel(r),
        color: COLORS[i % COLORS.length],
        width: 2 as const,
        points: normalized ? normalize(pts) : pts,
      };
    })
    .filter(Boolean) as EquitySeries[], [selectedRuns, curves, normalized]);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xs tracking-[0.2em] text-muted-foreground">
          COMPARE / 回测对比（{selected.length}/{MAX_SELECTED}）
        </h2>
        <div className="flex items-center gap-2">
          <Button
            variant="outline" size="sm"
            className={normalized ? "border-primary text-primary" : ""}
            onClick={() => setNormalized((n) => !n)}
          >
            {normalized ? "归一化 = 100" : "原始权益"}
          </Button>
          <Button variant="outline" size="sm" onClick={refresh} disabled={loading}>
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} /> 刷新
          </Button>
        </div>
      </div>

      {/* 叠加权益曲线 */}
      <div className="rounded-md border border-border bg-card p-3">
        {series.length ? (
          <EquityChart series={series} height={360} />
        ) : (
          <div className="flex h-[360px] items-center justify-center text-xs text-muted-foreground">
            {runs.length ? "在下方列表勾选要对比的回测（最多 4 条）" : "暂无回测存档——先到回测工作台跑一次"}
          </div>
        )}
        {series.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
            {series.map((s) => (
              <span key={s.name} className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <span className="inline-block h-[2px] w-4" style={{ background: s.color }} />
                {s.name}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* 指标对比表 */}
      {selectedRuns.length > 0 && (
        <div className="rounded-md border border-border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>回测</TableHead>
                <TableHead className="text-right">总收益</TableHead>
                <TableHead className="text-right">年化</TableHead>
                <TableHead className="text-right">夏普</TableHead>
                <TableHead className="text-right">最大回撤</TableHead>
                <TableHead className="text-right">卡玛</TableHead>
                <TableHead className="text-right">笔数</TableHead>
                <TableHead className="text-right">盈亏比</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {selectedRuns.map((r, i) => {
                const m = r.metrics;
                return (
                  <TableRow key={r.job_id}>
                    <TableCell className="text-xs">
                      <span className="mr-2 inline-block h-2 w-2 rounded-full"
                            style={{ background: COLORS[i % COLORS.length] }} />
                      {runLabel(r)}
                    </TableCell>
                    <TableCell className={`num text-right ${pnlClass(m?.total_return)}`}>{pct(m?.total_return)}</TableCell>
                    <TableCell className={`num text-right ${pnlClass(m?.annualized)}`}>{pct(m?.annualized)}</TableCell>
                    <TableCell className="num text-right">{num(m?.sharpe)}</TableCell>
                    <TableCell className="num text-right text-down">{pct(m?.max_drawdown)}</TableCell>
                    <TableCell className="num text-right">{num(m?.calmar)}</TableCell>
                    <TableCell className="num text-right">{m?.trades ?? "—"}</TableCell>
                    <TableCell className="num text-right">{num(m?.profit_factor)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      {/* 存档列表 */}
      <div className="rounded-md border border-border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>时间 (UTC)</TableHead>
              <TableHead>策略</TableHead>
              <TableHead>品种</TableHead>
              <TableHead>周期</TableHead>
              <TableHead className="text-right">风险</TableHead>
              <TableHead className="text-right">VT</TableHead>
              <TableHead>参数</TableHead>
              <TableHead className="text-right">总收益</TableHead>
              <TableHead className="text-right">夏普</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.map((r) => {
              const on = selected.includes(r.job_id);
              const color = on ? COLORS[selected.indexOf(r.job_id) % COLORS.length] : "transparent";
              return (
                <TableRow key={r.job_id} className="cursor-pointer" onClick={() => toggle(r.job_id)}>
                  <TableCell>
                    <span className="inline-block h-3 w-3 rounded-sm border border-muted-foreground"
                          style={{ background: color }} />
                  </TableCell>
                  <TableCell className="num text-xs">{ts(r.created_at)}</TableCell>
                  <TableCell className="text-xs">{r.strategy}</TableCell>
                  <TableCell className="text-xs">{r.symbols.join("+")}</TableCell>
                  <TableCell className="text-xs">{r.timeframe}</TableCell>
                  <TableCell className="num text-right text-xs">{(r.risk_frac * 100).toFixed(0)}%</TableCell>
                  <TableCell className="num text-right text-xs">{num(r.vol_target, 2)}</TableCell>
                  <TableCell className="max-w-[220px] truncate text-xs text-muted-foreground">
                    {Object.entries(r.params).map(([k, v]) => `${k}=${v}`).join(" ") || "默认"}
                  </TableCell>
                  <TableCell className={`num text-right text-xs ${pnlClass(r.metrics?.total_return)}`}>
                    {pct(r.metrics?.total_return)}
                  </TableCell>
                  <TableCell className="num text-right text-xs">{num(r.metrics?.sharpe)}</TableCell>
                  <TableCell>
                    <button
                      className="text-muted-foreground hover:text-down"
                      onClick={(e) => { e.stopPropagation(); remove(r.job_id); }}
                      title="删除存档"
                    >
                      <Trash2 size={13} />
                    </button>
                  </TableCell>
                </TableRow>
              );
            })}
            {!runs.length && (
              <TableRow>
                <TableCell colSpan={11} className="py-8 text-center text-xs text-muted-foreground">
                  暂无回测存档
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
