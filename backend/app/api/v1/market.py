"""大盘总览 API：公开行情聚合（Gate 永续 ticker + 恐慌贪婪指数）。"""
from fastapi import APIRouter, HTTPException

from ...services import market_service

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/overview")
async def market_overview():
    """大盘总览：恐慌贪婪指数、涨跌家数、成交额 TOP24 热力图。60s 缓存。"""
    try:
        return await market_service.get_overview()
    except Exception as e:  # noqa: BLE001 - 行情源故障对外报 502
        raise HTTPException(502, f"行情源暂不可用：{e}") from e
