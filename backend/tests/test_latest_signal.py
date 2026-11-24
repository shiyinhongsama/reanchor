"""最新信号查询测试：列表页未读高亮的数据源（临时库，无外网）。

运行：PYTHONPATH=. python tests/test_latest_signal.py
"""
import asyncio
import tempfile

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.models import Base, SignalLogModel
from app.schemas import ExecutorCreate
from app.services import executor_service as svc

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
engine = create_async_engine(f"sqlite+aiosqlite:///{_tmp.name}")
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as s:
        ex1 = await svc.create_executor(s, ExecutorCreate(symbol="BTCUSDT", timeframe="1d"))
        ex2 = await svc.create_executor(s, ExecutorCreate(symbol="ETHUSDT", timeframe="4h"))

        # 无信号时返回空
        assert await svc.latest_signal_map(s, [ex1.id, ex2.id]) == {}

        s.add(SignalLogModel(executor_id=ex1.id, action="hold"))
        await s.flush()
        s.add(SignalLogModel(executor_id=ex1.id, action="open", side=1))
        s.add(SignalLogModel(executor_id=ex2.id, action="trail_stop"))
        await s.flush()

        m = await svc.latest_signal_map(s, [ex1.id, ex2.id])
        assert m[ex1.id]["action"] == "open", "应取 id 最大的那条"
        assert m[ex2.id]["action"] == "trail_stop"
        assert m[ex1.id]["id"] > 0
        # 只查一个执行器、空列表
        assert list(await svc.latest_signal_map(s, [ex2.id])) == [ex2.id]
        assert await svc.latest_signal_map(s, []) == {}
    print("latest signal tests passed")


if __name__ == "__main__":
    asyncio.run(main())
