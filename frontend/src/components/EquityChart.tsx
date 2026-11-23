import { useEffect, useRef } from "react";
import {
  ColorType,
  createChart,
  LineSeries,
  type UTCTimestamp,
} from "lightweight-charts";

export interface EquitySeries {
  name: string;
  color: string;
  width: 1 | 2;
  points: { t: string; v: number }[];
}

/** 资金曲线：TradingView lightweight-charts，终端暗色定制。 */
export default function EquityChart({ series, height = 420 }: { series: EquitySeries[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const key = series.map((s) => `${s.name}:${s.points.length}:${s.points[0]?.t ?? ""}:${s.points.at(-1)?.t ?? ""}`).join("|");

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#7c8aa8",
        fontFamily: "'JetBrains Mono', ui-monospace, monospace",
        fontSize: 10,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "rgba(26,37,64,0.5)" },
        horzLines: { color: "rgba(26,37,64,0.5)" },
      },
      rightPriceScale: { borderColor: "#1a2540" },
      timeScale: { borderColor: "#1a2540", timeVisible: true, secondsVisible: false },
      crosshair: {
        vertLine: { color: "#22d3ee", labelBackgroundColor: "#111a2b" },
        horzLine: { color: "#22d3ee", labelBackgroundColor: "#111a2b" },
      },
    });
    for (const s of series) {
      const ls = chart.addSeries(LineSeries, {
        color: s.color,
        lineWidth: s.width,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerRadius: 3,
      });
      const seen = new Set<number>();
      ls.setData(
        s.points
          .map((p) => ({ time: (new Date(p.t).getTime() / 1000) as UTCTimestamp, value: p.v }))
          .filter((p) => !seen.has(p.time) && (seen.add(p.time), true)),
      );
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return <div ref={ref} style={{ height }} className="w-full" />;
}
