"""持久化模型（一期最小集，对应设计文档 §5 ER 图）。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ExecutorModel(Base):
    """执行器：策略模板的运行时实例（设计决策 #9）。

    持有策略参数配置、绑定品种/周期、风险预算与运行时状态（持仓快照）。
    """
    __tablename__ = "executors"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    strategy_name: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(8))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    equity_budget: Mapped[float] = mapped_column(Float)
    risk_frac: Mapped[float] = mapped_column(Float, default=0.05)
    vol_target: Mapped[float] = mapped_column(Float, default=0.25)
    max_leverage: Mapped[float] = mapped_column(Float, default=3.0)
    status: Mapped[str] = mapped_column(String(16), default="paused")  # running/paused/stopped
    state: Mapped[dict] = mapped_column(JSON, default=dict)            # {position: {...}} 等
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_utcnow, onupdate=_utcnow)


class EquitySnapshotModel(Base):
    """执行器权益快照：每次 tick 收盘后落一条（账面权益 = 钱包现金 + 未实现盈亏）。

    记账口径（dry-run，账面隔离，见设计文档 §5）：
    - 成交近似：信号 bar 收盘价成交（live 实际在收盘后数秒下单，偏差可忽略）；
    - 手续费双边按 settings.default_fee 从现金扣减；
    - 未计资金费与滑点（与研究成本模型独立，属监控口径）。
    """
    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    executor_id: Mapped[str] = mapped_column(String(32), index=True)
    bar_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    equity: Mapped[float] = mapped_column(Float)          # cash + 未实现盈亏
    cash: Mapped[float] = mapped_column(Float)            # 钱包现金（含已实现盈亏与费用）
    unrealized: Mapped[float] = mapped_column(Float, default=0.0)
    close: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SignalLogModel(Base):
    """执行器信号/动作流水：每次 bar 收盘触发后落一条（含错误与熔断事件）。"""
    __tablename__ = "signal_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    executor_id: Mapped[str] = mapped_column(String(32), index=True)
    bar_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    action: Mapped[str] = mapped_column(String(16))     # open/close/trail_stop/hold/error/circuit_break
    entry_signal: Mapped[int] = mapped_column(Integer, default=0)
    exit_signal: Mapped[bool] = mapped_column(Boolean, default=False)
    side: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr: Mapped[float | None] = mapped_column(Float, nullable=True)
    slope: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
