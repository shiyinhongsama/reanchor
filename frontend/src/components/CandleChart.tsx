import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  type UTCTimestamp,
} from "lightweight-charts";

export interface Candle { t: string; o: number; h: number; l: number; c: number }

export interface TradeMarker {
  t: string;
  side: 1 | -1;              // 持仓方向：1 多 / -1 空
  phase: "entry" | "exit" | "trail";
  win?: boolean;             // exit 用：盈亏定色
  text: string;
}

/** K 线图：蜡烛 + 开平仓标记（lightweight-charts v5，终端暗色）。 */
export default function CandleChart({ candles, markers, height = 420 }:
  { candles: Candle[]; markers: TradeMarker[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const key = `${candles.length}:${candles[0]?.t ?? ""}:${candles.at(-1)?.t ?? ""}:${markers.length}`;

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

    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#34d399", downColor: "#f87171",
      wickUpColor: "#34d399", wickDownColor: "#f87171",
      borderVisible: false,
    });
    const toTs = (t: string) => (new Date(t).getTime() / 1000) as UTCTimestamp;
    series.setData(candles.map((c) => ({
      time: toTs(c.t), open: c.o, high: c.h, low: c.l, close: c.c,
    })));

    createSeriesMarkers(series, markers.map((m) => {
      if (m.phase === "trail") {
        // 重锚止损：小方块，顺持仓方向挂边
        return {
          time: toTs(m.t),
          position: (m.side > 0 ? "belowBar" : "aboveBar") as "aboveBar" | "belowBar",
          shape: "square" as const,
          color: "#22d3ee",
          text: m.text,
          size: 0.4,
        };
      }
      if (m.phase === "entry") {
        return m.side > 0
          ? { time: toTs(m.t), position: "belowBar" as const, shape: "arrowUp" as const, color: "#34d399", text: m.text, size: 1 }
          : { time: toTs(m.t), position: "aboveBar" as const, shape: "arrowDown" as const, color: "#f87171", text: m.text, size: 1 };
      }
      // 平仓：多平挂在 bar 上方，空平挂在下方；盈利青色、亏损红色
      return {
        time: toTs(m.t),
        position: (m.side > 0 ? "aboveBar" : "belowBar") as "aboveBar" | "belowBar",
        shape: "circle" as const,
        color: m.win ? "#22d3ee" : "#f87171",
        text: m.text,
        size: 0.5,
      };
    }));

    chart.timeScale().fitContent();
    return () => chart.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return <div ref={ref} style={{ height }} className="w-full" />;
}
