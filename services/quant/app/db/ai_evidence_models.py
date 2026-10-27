"""实证准入的影子意图表（ai-trade-evidence-gated-capital §6.1）。

这是仓库里第一个独立的模型文件。不放进 `models.py` 的理由：那个文件在工作树里带着另一个尚未合入
特性（交易日志）的改动，是混合文件；新表放新文件，提交时就不需要按行拆分它。

注册方式：`db/session.py` **模块顶部**导入本模块，使 `Base.metadata` 在任何人调 `create_all` 之前
就认识这张表——包括直接 `Base.metadata.create_all` 的测试夹具（评审 P2-1）。`create_all` 会建这张
缺失的表 ⇒ 零迁移代码，老库首次启动自动获得。

一条开仓意愿（去重后）至多一行。推进列（`bars_seen` / `last_bar_ts` / `mfe_bps` / `mae_bps`）只是
缓存，不是真源：接管时由 `shadow_ledger.replay_shadow` 从冻结的入场参数重算（评审 P0-3）。
"""
from __future__ import annotations

from sqlalchemy import Boolean, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


class AiTradeShadowIntent(Base):
    """一笔影子意愿：真单与影子同一口径（固定参考离场规则下的 bps 净额）。"""

    __tablename__ = "ai_trade_shadow_intents"
    __table_args__ = (
        UniqueConstraint("source", "source_ref", name="uq_shadow_source_ref"),
        Index("ix_shadow_bucket", "inst_id", "bar", "side", "policy_version", "status",
              "matured_wall"),
        Index("ix_shadow_pooled", "bar", "side", "policy_version", "status", "matured_wall"),
        Index("ix_shadow_open", "inst_id", "bar", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shadow_key: Mapped[str] = mapped_column(String(32), unique=True)
    source: Mapped[str] = mapped_column(String(12), index=True, default="live")
    # 回填：`<run_id>:<trade_id>`；实时：恒为 `shadow_key`（评审 P1-2：空串会让第二条实时行撞唯一约束）。
    source_ref: Mapped[str] = mapped_column(String(64), default="")
    inst_id: Mapped[str] = mapped_column(String(32), default="")
    bar: Mapped[str] = mapped_column(String(8), default="")
    side: Mapped[str] = mapped_column(String(8), default="")
    policy_version: Mapped[str] = mapped_column(String(96), default="")
    run_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    group_id: Mapped[str] = mapped_column(String(64), default="")
    round_no: Mapped[int] = mapped_column(Integer, default=0)
    opened_wall: Mapped[float] = mapped_column(Float, default=0.0)
    opened_bar_ts: Mapped[int] = mapped_column(Integer, default=0)
    entry_px: Mapped[float] = mapped_column(Float, default=0.0)
    cost_bps: Mapped[float] = mapped_column(Float, default=0.0)
    atr_bps: Mapped[float] = mapped_column(Float, default=0.0)
    sl_bps: Mapped[float] = mapped_column(Float, default=0.0)
    tp_bps: Mapped[float] = mapped_column(Float, default=0.0)
    traded: Mapped[bool] = mapped_column(Boolean, default=False)
    repeats: Mapped[int] = mapped_column(Integer, default=0)
    bars_seen: Mapped[int] = mapped_column(Integer, default=0)
    last_bar_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mfe_bps: Mapped[float] = mapped_column(Float, default=0.0)
    mae_bps: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(10), index=True, default="open")
    exit_kind: Mapped[str] = mapped_column(String(8), default="")
    exit_px: Mapped[float | None] = mapped_column(Float, nullable=True)
    matured_wall: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    # **可空**：void 行为 NULL，绝不用 0 冒充「没结算」。
    gross_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    void_reason: Mapped[str] = mapped_column(String(24), default="")
    created_ts: Mapped[int] = mapped_column(Integer, default=0)
    updated_ts: Mapped[int] = mapped_column(Integer, default=0)
