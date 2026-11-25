"""大盘总览测试：ticker 解析 / 涨跌家数 / 缓存 TTL（注入假数据源，无外网）。"""
import asyncio

from app.services import market_service as ms


def _raw():
    return [
        {"contract": "BTC_USDT", "last": "97000", "change_percentage": "2.5",
         "volume_24h_usd": "5000000000", "high_24h": "98000", "low_24h": "94000"},
        {"contract": "ETH_USDT", "last": "3400", "change_percentage": "-1.2",
         "volume_24h_usd": "2000000000", "high_24h": "3500", "low_24h": "3300"},
        {"contract": "SOL_USDT", "last": "210", "change_percentage": "0.0",
         "volume_24h_usd": "800000000", "high_24h": "215", "low_24h": "205"},
        {"contract": "DOGE_USDT", "last": "0.4", "change_percentage": "-3.1",
         "volume_24h_usd": "300000000", "high_24h": "0.42", "low_24h": "0.38"},
        {"contract": "XAU_USDT", "last": "4000", "change_percentage": "-1",  # TradFi，过滤
         "volume_24h_usd": "3000000000"},
        {"contract": "BTC_USD", "last": "1", "change_percentage": "0",  # 非 USDT 结算，过滤
         "volume_24h_usd": "999999999"},
        {"contract": "XYZ_USDT", "last": "0", "change_percentage": "5",  # 价格非法，过滤
         "volume_24h_usd": "1000000"},
        {"contract": "BAD_USDT", "last": "abc"},                        # 脏数据，跳过
    ]


def test_parse_tickers():
    rows = ms.parse_tickers(_raw())
    assert [r["symbol"] for r in rows] == ["BTC", "ETH", "SOL", "DOGE"]  # 按成交额排序
    assert rows[0]["change_pct"] == 2.5 and rows[0]["last"] == 97000
    print("parse_tickers tests passed")


def test_breadth():
    b = ms.breadth(ms.parse_tickers(_raw()))
    assert b["advancers"] == 1 and b["decliners"] == 2 and b["flat"] == 1
    assert abs(b["avg_change_pct"] - (2.5 - 1.2 + 0.0 - 3.1) / 4) < 1e-9
    assert b["total_volume_usd"] == 5_000_000_000 + 2_000_000_000 + 800_000_000 + 300_000_000
    assert ms.breadth([])["avg_change_pct"] is None
    print("breadth tests passed")


async def test_overview_cache_and_degrade():
    calls = {"tickers": 0, "fng": 0}

    async def fake_tickers():
        calls["tickers"] += 1
        return _raw()

    async def fake_fng_fail():
        calls["fng"] += 1
        return None                     # FNG 源失败 → 降级为 None

    ms._cache.update({"t": 0.0, "data": None})
    d1 = await ms.get_overview(fake_tickers, fake_fng_fail, force=True)
    assert d1["fng"] is None                        # 外部源失败不阻塞
    assert len(d1["heatmap"]) == 4
    assert d1["heatmap"][0]["symbol"] == "BTC"
    assert d1["breadth"]["advancers"] == 1

    d2 = await ms.get_overview(fake_tickers, fake_fng_fail)   # TTL 内命中缓存
    assert calls == {"tickers": 1, "fng": 1}, f"缓存未生效: {calls}"
    assert d2 is d1
    print("overview cache tests passed")


if __name__ == "__main__":
    test_parse_tickers()
    test_breadth()
    asyncio.run(test_overview_cache_and_degrade())
    print("market tests passed")
