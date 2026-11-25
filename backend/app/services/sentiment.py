"""情绪分析：CryptoBERT（ElKulako/cryptobert）打标，输出 bullish/bearish/neutral + score。

- 模型懒加载（首次调用才下载/载入，约 500MB）；加载失败时 score 返回 None，
  采集管道不因此中断（新闻仍入库，情绪列为空）；
- 推理较重（CPU 数十 ms/条），通过 asyncio.to_thread 离线程外执行；
- classify 可注入以便测试（不下载真模型）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

log = logging.getLogger(__name__)

_LABEL_SCORE = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}

_pipeline = None
_load_failed = False


def _cache_has_model() -> bool:
    """模型快照是否已在 HF 缓存中（存在即可离线加载）。"""
    import os
    from pathlib import Path
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    snaps = hf_home / "hub" / "models--ElKulako--cryptobert" / "snapshots"
    return snaps.is_dir() and any(snaps.iterdir())


def _load():
    """加载 CryptoBERT 文本分类管道（同步，放线程里跑）。

    缓存命中时先置 HF_HUB_OFFLINE=1（须在 import transformers 之前），
    让所有 Hub 请求直接短路到缓存，避免逐个文件联网超时重试；
    未命中缓存才联网下载，此时若配置了 TP_NEWS_PROXY 则注入代理。
    """
    global _pipeline, _load_failed
    if _pipeline is not None or _load_failed:
        return _pipeline
    import os
    offline = _cache_has_model()
    if offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
    else:
        from app.core.config import get_settings
        proxy = get_settings().news_proxy
        if proxy:
            os.environ.setdefault("HTTPS_PROXY", proxy)
            os.environ.setdefault("HTTP_PROXY", proxy)
    try:
        from transformers import pipeline
        _pipeline = pipeline("text-classification", model="ElKulako/cryptobert",
                             truncation=True, max_length=512)
        log.info("cryptobert loaded (offline=%s)", offline)
    except Exception as e:  # noqa: BLE001 - 模型不可用时降级为不打分
        _load_failed = True
        log.warning("cryptobert unavailable, sentiment disabled: %s", e)
    return _pipeline


def _classify_sync(texts: list[str]) -> list[tuple[str | None, float | None]]:
    """批量打标：返回 (label, score)；score = ±置信度（neutral 为 0）。"""
    pipe = _load()
    if pipe is None:
        return [(None, None)] * len(texts)
    out = []
    for r in pipe(texts):
        label = str(r["label"]).lower()
        sign = _LABEL_SCORE.get(label)
        if sign is None:
            out.append((None, None))
        else:
            out.append((label, round(sign * float(r["score"]), 4)))
    return out


async def classify(texts: list[str]) -> list[tuple[str | None, float | None]]:
    if not texts:
        return []
    return await asyncio.to_thread(_classify_sync, texts)


# 测试注入点：替换 classify 实现（async def fake(texts) -> [(label, score)])
ClassifyFn = Callable[[list[str]], "asyncio.Future[list[tuple[str | None, float | None]]]"]
