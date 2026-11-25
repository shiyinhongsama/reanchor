"""FastAPI 应用入口。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .core.auth import AuthMiddleware
from .core.config import get_settings
from .infrastructure.db import init_db
from .services.live_runner import runner as live_runner
from .services.news_service import news_service
from .api.v1 import auth, backtests, executors, live, market, news

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()              # 一期 SQLite 自动建表；迁移 Postgres 后改 Alembic
    await live_runner.start()    # 恢复 running 执行器的调度任务
    news_service.start()         # 新闻采集：15 分钟一拍 + 每日清理
    yield
    await news_service.shutdown()
    await live_runner.shutdown()


app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan,
              description="ReAnchor 合约量化交易平台：一期回测+实盘，二期 AI Agent 助手")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],   # Vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(AuthMiddleware)       # /api/v1 需 Bearer 令牌（登录接口除外）

app.include_router(auth.router, prefix="/api/v1")
app.include_router(backtests.router, prefix="/api/v1")
app.include_router(executors.router, prefix="/api/v1")
app.include_router(live.router, prefix="/api/v1")
app.include_router(market.router, prefix="/api/v1")
app.include_router(news.router, prefix="/api/v1")


@app.get("/api/health")
async def health():
    return {"status": "ok", "env": settings.env}
