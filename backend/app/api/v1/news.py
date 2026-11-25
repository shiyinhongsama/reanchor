"""新闻流 API：采集到的新闻与情绪聚合。"""
from fastapi import APIRouter

from ...services import news_service as ns

router = APIRouter(prefix="/news", tags=["news"])


@router.get("")
async def list_news(hours: int = 48, limit: int = 100):
    """最近 N 小时新闻流（新→旧），含情绪标签。"""
    rows = await ns.news_service.recent(hours=hours, limit=limit)
    return {"news": [{
        "id": n.id, "source": n.source, "title": n.title, "url": n.url,
        "lang": n.lang,
        "published_at": n.published_at.isoformat() if n.published_at else None,
        "sentiment": n.sentiment, "score": n.score,
    } for n in rows]}


@router.get("/sentiment")
async def news_sentiment(hours: int = 24):
    """最近 N 小时加权情绪聚合（时间衰减半衰期 12h）。"""
    rows = await ns.news_service.recent(hours=hours, limit=500)
    return ns.aggregate(rows, hours=hours)


@router.post("/collect")
async def collect_now():
    """手动触发一轮采集（调试用；正常由 15 分钟定时任务驱动）。"""
    return await ns.news_service.collect_once()
