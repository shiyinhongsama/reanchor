import { useCallback, useEffect, useState } from "react";
import { Newspaper } from "lucide-react";
import { api, type NewsItem, type NewsSentiment } from "@/lib/api";
import { num, ts } from "@/lib/format";

const SENT_STYLE: Record<string, { label: string; cls: string }> = {
  bullish: { label: "多", cls: "text-up" },
  bearish: { label: "空", cls: "text-down" },
  neutral: { label: "中", cls: "text-muted-foreground" },
};

/** 新闻情绪板块：24h 加权情绪分 + 新闻流（CryptoBERT 打标）。 */
export default function NewsPanel() {
  const [sent, setSent] = useState<NewsSentiment | null>(null);
  const [news, setNews] = useState<NewsItem[]>([]);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, n] = await Promise.all([api.newsSentiment(24), api.news(48, 60)]);
      setSent(s);
      setNews(n.news);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 120_000);     // 新闻流 2 分钟一拍
    return () => clearInterval(id);
  }, [load]);

  const scorePct = sent?.score != null ? (sent.score + 1) / 2 : null;  // [-1,1] → [0,1]

  return (
    <div className="rounded-md border border-border bg-card p-3">
      <div className="mb-2 flex items-center gap-3">
        <span className="section-label">NEWS / 新闻情绪</span>
        {sent && sent.count > 0 && (
          <span className="text-[10px] text-muted-foreground">
            24h <span className="num text-foreground">{sent.count}</span> 条 ·
            多 <span className="num text-up">{sent.bullish}</span> /
            空 <span className="num text-down">{sent.bearish}</span> /
            中 <span className="num">{sent.neutral}</span>
          </span>
        )}
        {failed && <span className="text-[10px] text-down">新闻服务暂不可用</span>}
      </div>

      {/* 情绪条：左空右多，指针为加权分 */}
      {scorePct != null && (
        <div className="mb-3">
          <div className="relative h-2 rounded bg-gradient-to-r from-down/60 via-panel to-up/60">
            <div className="absolute top-1/2 h-3.5 w-3.5 -translate-y-1/2 rounded-full border-2 border-background bg-primary"
              style={{ left: `calc(${scorePct * 100}% - 7px)` }} />
          </div>
          <div className="mt-1 flex justify-between text-[10px] text-muted-foreground">
            <span>恐慌/利空</span>
            <span className={`num font-bold ${sent!.score! >= 0.1 ? "text-up" : sent!.score! <= -0.1 ? "text-down" : ""}`}>
              {sent!.score! >= 0 ? "+" : ""}{num(sent!.score!, 3)}
            </span>
            <span>贪婪/利好</span>
          </div>
        </div>
      )}

      {news.length > 0 ? (
        <div className="max-h-72 space-y-0.5 overflow-y-auto">
          {news.map((n) => {
            const s = n.sentiment ? SENT_STYLE[n.sentiment] : null;
            return (
              <a key={n.id} href={n.url || undefined} target="_blank" rel="noreferrer"
                className="flex items-baseline gap-2 rounded-sm px-1.5 py-1 transition-colors hover:bg-panel">
                <span className={`shrink-0 text-[10px] font-bold ${s?.cls ?? "text-muted-foreground/40"}`}>
                  {s?.label ?? "·"}
                </span>
                <span className="min-w-0 flex-1 truncate text-[11px]">{n.title}</span>
                <span className="shrink-0 text-[10px] text-muted-foreground">{n.source}</span>
                <span className="num shrink-0 text-[10px] text-muted-foreground">
                  {n.published_at ? ts(n.published_at) : "—"}
                </span>
              </a>
            );
          })}
        </div>
      ) : (
        <div className="flex h-16 items-center justify-center text-xs text-muted-foreground">
          <Newspaper size={13} className="mr-1.5" />
          {failed ? "—" : "新闻采集中（每 15 分钟一轮），稍后自动出现"}
        </div>
      )}
    </div>
  );
}
