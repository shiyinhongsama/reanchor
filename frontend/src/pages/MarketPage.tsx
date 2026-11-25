import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api, type MarketOverview, type MarketTicker } from "@/lib/api";
import { num, pct } from "@/lib/format";
import Metric from "@/components/Metric";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const FNG_CN: Record<string, string> = {
  "Extreme Fear": "极度恐慌", Fear: "恐慌", Neutral: "中性",
  Greed: "贪婪", "Extreme Greed": "极度贪婪",
};

function fngTone(v: number): "up" | "down" | undefined {
  if (v <= 25) return "down";
  if (v >= 75) return "up";
  return undefined;
}

/** 涨跌 → 瓦片底色：±5% 饱和。 */
function tileBg(change: number): string {
  const a = 0.12 + 0.38 * Math.min(Math.abs(change) / 5, 1);
  return change >= 0 ? `rgba(52, 211, 153, ${a})` : `rgba(248, 113, 113, ${a})`;
}

/** 成交额排名 → 瓦片网格占位：前 2 名大瓦片，3–8 名中瓦片，其余小瓦片。 */
function tileSpan(rank: number): string {
  if (rank < 2) return "col-span-2 row-span-2";
  if (rank < 8) return "col-span-2";
  return "";
}

function compactUsd(v: number): string {
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(0)}M`;
  return num(v);
}

export default function MarketPage() {
  const [data, setData] = useState<MarketOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api.marketOverview());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 60_000);      // 后端有 60s 缓存，前端同频刷新
    return () => clearInterval(id);
  }, [load]);

  const b = data?.breadth;
  const fng = data?.fng;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
      <div className="flex items-center gap-3">
        <span className="section-label">MARKET / 大盘总览</span>
        {data && (
          <span className="text-[10px] text-muted-foreground">
            截至 {data.as_of.replace("T", " ").slice(0, 19)}
          </span>
        )}
        <Button size="sm" variant="outline" className="ml-auto h-7 text-xs" onClick={load} disabled={loading}>
          <RefreshCw size={12} className={`mr-1 ${loading ? "animate-spin" : ""}`} /> 刷新
        </Button>
      </div>

      {error && <div className="border border-down/40 bg-down/10 p-2 text-[11px] text-down">{error}</div>}

      {/* 指标卡 */}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
        <Metric
          label="恐慌贪婪指数"
          value={fng ? `${fng.value} · ${FNG_CN[fng.label] ?? fng.label}` : "暂不可用"}
          tone={fng ? fngTone(fng.value) : undefined}
        />
        <Metric
          label="较昨日"
          value={fng?.prev_value != null ? `${fng.value - fng.prev_value >= 0 ? "+" : ""}${fng.value - fng.prev_value}` : "—"}
          tone={fng?.prev_value != null ? (fng.value - fng.prev_value >= 0 ? "up" : "down") : undefined}
        />
        <Metric label="上涨家数" value={b ? String(b.advancers) : "—"} tone="up" />
        <Metric label="下跌家数" value={b ? String(b.decliners) : "—"} tone="down" />
        <Metric label="平均涨跌（TOP100）" value={b?.avg_change_pct != null ? pct(b.avg_change_pct / 100) : "—"}
          tone={b?.avg_change_pct != null ? (b.avg_change_pct >= 0 ? "up" : "down") : undefined} />
        <Metric label="24h 总成交" value={b ? `$${compactUsd(b.total_volume_usd)}` : "—"} />
      </div>

      {/* 热力图：成交额 TOP24，瓦片大小 = 成交额权重，颜色 = 24h 涨跌 */}
      <div className="rounded-md border border-border bg-card p-3">
        <div className="section-label mb-2">HEATMAP / 热力图（按 24h 成交额）</div>
        {data && data.heatmap.length > 0 ? (
          <div className="grid auto-rows-[64px] grid-cols-6 gap-1">
            {data.heatmap.map((t, i) => (
              <div key={t.symbol}
                className={`flex flex-col justify-between overflow-hidden rounded-sm p-2 transition-transform hover:scale-[1.02] ${tileSpan(i)}`}
                style={{ background: tileBg(t.change_pct) }}
                title={`${t.symbol} · $${num(t.last)} · ${pct(t.change_pct / 100)} · 成交 $${compactUsd(t.volume_usd)}`}>
                <div className="flex items-baseline justify-between gap-1">
                  <span className={`font-bold ${i < 2 ? "text-sm" : "text-[11px]"}`}>{t.symbol}</span>
                  {i < 8 && <span className="num text-[10px] text-white/70">${compactUsd(t.volume_usd)}</span>}
                </div>
                <div className={`num font-medium ${i < 2 ? "text-base" : "text-[11px]"}`}>
                  {t.change_pct >= 0 ? "+" : ""}{t.change_pct.toFixed(2)}%
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="flex h-40 items-center justify-center text-xs text-muted-foreground">
            {loading ? "行情加载中…" : "暂无数据"}
          </div>
        )}
      </div>

      {/* 明细表 */}
      {data && data.heatmap.length > 0 && (
        <div className="rounded-md border border-border bg-card">
          <Table>
            <TableHeader>
              <TableRow className="border-border hover:bg-transparent">
                <TableHead className="text-[10px]">品种</TableHead>
                <TableHead className="text-[10px] text-right">最新价</TableHead>
                <TableHead className="text-[10px] text-right">24h 涨跌</TableHead>
                <TableHead className="text-[10px] text-right">24h 成交</TableHead>
                <TableHead className="text-[10px]">24h 区间位置</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.heatmap.map((t: MarketTicker) => {
                const range = t.high_24h - t.low_24h;
                const pos = range > 0 ? (t.last - t.low_24h) / range : 0.5;
                return (
                  <TableRow key={t.symbol} className="border-border/60 hover:bg-panel">
                    <TableCell className="py-1.5 text-[11px] font-bold">{t.symbol}</TableCell>
                    <TableCell className="num py-1.5 text-right text-[11px]">${num(t.last)}</TableCell>
                    <TableCell className={`num py-1.5 text-right text-[11px] font-medium ${t.change_pct >= 0 ? "text-up" : "text-down"}`}>
                      {t.change_pct >= 0 ? "+" : ""}{t.change_pct.toFixed(2)}%
                    </TableCell>
                    <TableCell className="num py-1.5 text-right text-[11px] text-muted-foreground">
                      ${compactUsd(t.volume_usd)}
                    </TableCell>
                    <TableCell className="py-1.5">
                      <div className="relative h-1.5 w-32 rounded bg-panel">
                        <div className="absolute top-1/2 h-2 w-2 -translate-y-1/2 rounded-full bg-primary"
                          style={{ left: `calc(${Math.min(Math.max(pos, 0), 1) * 100}% - 4px)` }} />
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
