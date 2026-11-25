"""新闻采集管道：多源 RSS 定时抓取 → 去重入库 → 情绪打标 → 聚合查询。

- 源全部走公开 RSS，无需 key；单源失败不影响其他源；
- 去重两级：url sha1（主键幂等）+ 规范化标题 hash（跨源同稿）；
- 情绪：英文条目走 CryptoBERT（sentiment 模块，可注入假实现测试）；
  中文条目暂不打分；
- 调度：独立 AsyncIOScheduler，15 分钟一拍，主程序 lifespan 里 start。
"""
from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..infrastructure.models import NewsModel
from . import sentiment

log = logging.getLogger(__name__)

# (名称, RSS URL, 语言, 源权重)
SOURCES: list[tuple[str, str, str, float]] = [
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "en", 1.0),
    ("cointelegraph", "https://cointelegraph.com/rss", "en", 1.0),
    ("theblock", "https://www.theblock.co/rss.xml", "en", 1.0),
    ("decrypt", "https://decrypt.co/feed", "en", 0.8),
]

_ATOM = "{http://www.w3.org/2005/Atom}"
_WS = re.compile(r"\s+")


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def _norm_title(t: str) -> str:
    return _WS.sub(" ", t.strip().lower())


def parse_feed(xml_text: str, source: str, lang: str) -> list[dict]:
    """解析 RSS2/Atom 为统一条目；单条解析失败跳过，不炸整个源。"""
    out: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out

    def emit(title: str | None, url: str | None, published: str | None) -> None:
        title = (title or "").strip()
        if not title:
            return
        ts = None
        if published:
            try:
                ts = parsedate_to_datetime(published)
            except (TypeError, ValueError):
                try:
                    ts = datetime.fromisoformat(published.replace("Z", "+00:00"))
                except ValueError:
                    ts = None
            if ts is not None and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        url = (url or "").strip()
        out.append({
            "id": _hash(url or f"{source}:{title}"),
            "title_hash": _hash(_norm_title(title)),
            "source": source, "lang": lang, "title": title[:490], "url": url[:990],
            "published_at": ts,
        })

    for item in root.iter("item"):                      # RSS 2.0
        emit(item.findtext("title"), item.findtext("link"),
             item.findtext("pubDate") or item.findtext("date"))
    for entry in root.iter(f"{_ATOM}entry"):            # Atom
        link = ""
        for l in entry.findall(f"{_ATOM}link"):
            if l.get("rel") in (None, "alternate"):
                link = l.get("href", "")
                break
        emit(entry.findtext(f"{_ATOM}title"), link,
             entry.findtext(f"{_ATOM}published") or entry.findtext(f"{_ATOM}updated"))
    return out


async def _fetch(url: str) -> str:
    """拉 RSS：直连优先，失败且配置了 TP_NEWS_PROXY 时回退代理（部分源国内不可达）。"""
    from ..core.config import get_settings
    proxy = get_settings().news_proxy or None
    for attempt, px in enumerate([None, proxy] if proxy else [None]):
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True,
                                         proxy=px) as client:
                r = await client.get(url, headers={"User-Agent": "ReAnchor/0.2 news-collector"})
                r.raise_for_status()
                return r.text
        except Exception:
            if attempt == (1 if proxy else 0):
                raise
    return ""  # unreachable


def aggregate(rows: list[NewsModel], hours: int = 24) -> dict:
    """24h 加权情绪分：score × 源权重 × 时间衰减（半衰期 12h），另报分布。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # DB 存的是朴素 UTC
    scored = [n for n in rows if n.score is not None]
    num = den = 0.0
    dist = {"bullish": 0, "bearish": 0, "neutral": 0}
    for n in scored:
        dist[n.sentiment] = dist.get(n.sentiment, 0) + 1
        ts = n.published_at or n.created_at
        if ts.tzinfo is not None:  # 兼容感知型时间戳
            ts = ts.replace(tzinfo=None)
        age_h = max((now - ts).total_seconds() / 3600, 0)
        w = 2 ** (-age_h / 12)
        num += n.score * w
        den += w
    return {
        "hours": hours,
        "score": round(num / den, 4) if den else None,
        "count": len(scored),
        **dist,
    }


class NewsService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession],
                 fetcher: Callable[[str], Awaitable[str]] = _fetch,
                 classifier=sentiment.classify):
        self._sf = session_factory
        self._fetch = fetcher
        self._classify = classifier
        self._scheduler = None

    async def collect_once(self) -> dict:
        """全源采集一轮：抓 → 解析 → 去重入库 → 英文新条目打标。返回统计。"""
        stats: dict[str, int] = {}
        new_items: list[dict] = []
        async with self._sf() as s:
            for name, url, lang, _w in SOURCES:
                try:
                    items = parse_feed(await self._fetch(url), name, lang)
                except Exception as e:  # noqa: BLE001 - 单源失败不阻塞
                    log.warning("news source %s failed: %s", name, e)
                    stats[name] = -1
                    continue
                added = 0
                for it in items:
                    exists = await s.get(NewsModel, it["id"])
                    if exists:
                        continue
                    dup = await s.scalar(select(NewsModel.id)
                                         .where(NewsModel.title_hash == it["title_hash"]))
                    if dup:
                        continue
                    s.add(NewsModel(**it))
                    new_items.append(it)
                    added += 1
                stats[name] = added
            await s.commit()

        # 情绪打标：英文且未打分的条目（含历史漏打的，模型曾不可用时可自愈）
        async with self._sf() as s:
            unscored = (await s.execute(
                select(NewsModel)
                .where(NewsModel.lang == "en", NewsModel.sentiment.is_(None))
                .order_by(NewsModel.created_at.desc()).limit(100))).scalars().all()
        if unscored:
            results = await self._classify([n.title for n in unscored])
            async with self._sf() as s:
                for row_id, (label, score) in zip([n.id for n in unscored], results):
                    row = await s.get(NewsModel, row_id)
                    if row and label:
                        row.sentiment, row.score = label, score
                await s.commit()
        stats["scored"] = len(unscored)
        stats["new"] = len(new_items)
        return stats

    async def recent(self, hours: int = 48, limit: int = 100) -> list[NewsModel]:
        cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
        async with self._sf() as s:
            rows = (await s.execute(
                select(NewsModel).order_by(NewsModel.published_at.desc().nullslast(),
                                           NewsModel.created_at.desc())
                .limit(min(limit, 500)))).scalars().all()
        return [n for n in rows
                if (n.published_at or n.created_at).timestamp() >= cutoff]

    async def purge(self, keep_days: int = 14) -> int:
        """清理过期新闻（情绪看的是近期流，老数据无价值）。"""
        cutoff = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() - keep_days * 86400, tz=timezone.utc)
        async with self._sf() as s:
            r = await s.execute(delete(NewsModel).where(NewsModel.created_at < cutoff))
            await s.commit()
            return r.rowcount or 0

    def start(self, interval_min: int = 15) -> None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        self._scheduler = AsyncIOScheduler(timezone=timezone.utc)
        self._scheduler.add_job(self.collect_once, IntervalTrigger(minutes=interval_min),
                                id="news_collect", replace_existing=True,
                                next_run_time=datetime.now(timezone.utc))
        self._scheduler.add_job(self.purge, IntervalTrigger(hours=24),
                                id="news_purge", replace_existing=True)
        self._scheduler.start()
        log.info("news collector started, every %d min", interval_min)

    async def shutdown(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)


from ..infrastructure.db import SessionLocal  # noqa: E402

news_service = NewsService(SessionLocal)
