"""新闻管道测试：RSS/Atom 解析、两级去重、情绪打标注入、聚合衰减（无外网）。"""
import asyncio
import tempfile
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.models import Base, NewsModel
from app.services import news_service as ns

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
engine = create_async_engine(f"sqlite+aiosqlite:///{_tmp.name}")
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Bitcoin surges past $100k as ETFs see record inflows</title>
<link>https://example.com/a1</link><pubDate>Wed, 25 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Major exchange halts withdrawals amid hack fears</title>
<link>https://example.com/a2</link><pubDate>Wed, 25 Nov 2026 11:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Markets steady as traders await Fed decision</title>
<link rel="alternate" href="https://example.com/b1"/>
<published>2026-11-25T12:00:00Z</published></entry>
</feed>"""


def test_parse_feed():
    items = ns.parse_feed(RSS, "coindesk", "en")
    assert len(items) == 2
    assert items[0]["source"] == "coindesk"
    assert items[0]["published_at"].year == 2026
    atom = ns.parse_feed(ATOM, "theblock", "en")
    assert len(atom) == 1 and atom[0]["url"] == "https://example.com/b1"
    assert ns.parse_feed("<broken", "x", "en") == []       # 坏 XML 返回空
    print("parse_feed tests passed")


async def _collect_round(svc: ns.NewsService):
    return await svc.collect_once()


async def test_collect_dedupe_and_score():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    def _boom():
        raise RuntimeError("boom")
    feeds = {"coindesk": RSS, "cointelegraph": RSS,      # 同稿不同源 → 标题去重
             "theblock": ATOM, "decrypt": _boom}

    async def fake_fetch(url: str) -> str:
        for name, body in feeds.items():
            if name in url:
                return body() if callable(body) else body
        raise RuntimeError("unknown url")

    async def fake_classify(texts):
        return [("bullish", 0.9) if "surges" in t else
                ("bearish", -0.8) if "hack" in t else
                ("neutral", 0.0) for t in texts]

    svc = ns.NewsService(Session, fetcher=fake_fetch, classifier=fake_classify)
    stats = await svc.collect_once()
    assert stats["decrypt"] == -1                        # 单源失败不阻塞
    assert stats["coindesk"] == 2 and stats["cointelegraph"] == 0   # 同稿去重
    assert stats["theblock"] == 1 and stats["new"] == 3

    stats2 = await svc.collect_once()                    # 幂等：重跑不入库
    assert stats2["new"] == 0

    rows = await svc.recent(hours=24 * 365, limit=10)    # 测试数据日期在"现在"之前
    assert len(rows) == 3
    by_title = {r.title: r for r in rows}
    sur = next(r for r in rows if "surges" in r.title)
    assert sur.sentiment == "bullish" and sur.score == 0.9
    hack = next(r for r in rows if "hack" in r.title)
    assert hack.sentiment == "bearish" and hack.score == -0.8
    print("collect/dedupe/score tests passed")


def test_aggregate_decay():
    now = datetime.now(timezone.utc)
    fresh = NewsModel(id="f", title_hash="f", source="x", title="t", lang="en",
                      published_at=now - timedelta(hours=1), sentiment="bullish", score=1.0)
    stale = NewsModel(id="s", title_hash="s", source="x", title="t", lang="en",
                      published_at=now - timedelta(hours=36), sentiment="bearish", score=-1.0)
    agg = ns.aggregate([fresh, stale])
    # 新新闻权重 2^(-1/12)≈0.944，旧新闻 2^(-3)=0.125 → 分数应接近 +0.77 偏向多头
    assert agg["score"] is not None and agg["score"] > 0.5, agg
    assert agg["bullish"] == 1 and agg["bearish"] == 1 and agg["count"] == 2
    assert ns.aggregate([])["score"] is None
    print("aggregate tests passed")


if __name__ == "__main__":
    test_parse_feed()
    asyncio.run(test_collect_dedupe_and_score())
    test_aggregate_decay()
    print("news tests passed")
