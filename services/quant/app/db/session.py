"""异步 DB 引擎 / 会话、建表、seed 内置 5 策略 (spec §8.3 / §5.1)。"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..strategies import BUILTIN_ORDER, STRATEGY_REGISTRY
from .models import Base, Strategy

logger = logging.getLogger(__name__)

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _db_url() -> str:
    settings = get_settings()
    db_path = Path(settings.db_path)
    if not db_path.is_absolute():
        # 相对路径：以 services/quant 为根（本文件位于 app/db/ 下，上溯两级）
        db_path = Path(__file__).resolve().parents[2] / settings.db_path
    return f"sqlite+aiosqlite:///{db_path.as_posix()}"


def get_engine():
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(
            _db_url(),
            echo=False,
            future=True,
            connect_args={"timeout": 5},
        )
        event.listen(_engine.sync_engine, "connect", _configure_sqlite)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：产出一个 async session。"""
    sm = get_sessionmaker()
    async with sm() as session:
        yield session


async def init_db() -> None:
    """建表 + seed 内置策略 + seed AI 模型服务商（均幂等）。"""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_trade_risk_columns(conn)
        await _ensure_weight_run_columns(conn)
        await _ensure_weight_batch_columns(conn)
        await _ensure_live_auto_group_columns(conn)
    await _seed_builtin_strategies()
    await _seed_builtin_factors()
    await _seed_agent_providers()
    await _reconcile_orphan_ai_trade_runs()


async def _ensure_trade_risk_columns(conn) -> None:
    rows = (await conn.execute(text("PRAGMA table_info(trades)"))).all()
    columns = {row[1] for row in rows}
    risk_columns = {
        "stop_loss_pct": "FLOAT",
        "take_profit_pct": "FLOAT",
        "max_hold_bars": "INTEGER",
        "stop_loss_price": "FLOAT",
        "take_profit_price": "FLOAT",
        # auto-trade-position-percentage：旧库自然补 NULL，不做不可靠历史回填。
        "position_pct": "FLOAT",
        # auto-trade-explainability §5.1：实盘自动交易触发解释 JSON，老库幂等补列（与风控列同机制）。
        "explain_json": "TEXT",
    }
    for name, sql_type in risk_columns.items():
        if name not in columns:
            await conn.execute(
                text(f"ALTER TABLE trades ADD COLUMN {name} {sql_type}")
            )


async def _ensure_weight_run_columns(conn) -> None:
    """老库幂等补列（weight-multi-model-training R1/P0）：``weight_train_runs`` 是既有表，
    ``create_all`` 不会为其补 ``batch_id``/``variant_label`` 两列——须此处 ``ALTER TABLE ADD COLUMN``
    幂等补齐（仿 ``_ensure_trade_risk_columns``），否则老库 INSERT/SELECT 立即 ``no such column``、
    连既有单次训练一并崩。补列后 ``CREATE INDEX IF NOT EXISTS`` 建批次索引（幂等、老库安全）。
    """
    rows = (await conn.execute(text("PRAGMA table_info(weight_train_runs)"))).all()
    columns = {row[1] for row in rows}
    batch_columns = {
        "batch_id": "INTEGER",
        "variant_label": "VARCHAR(64) DEFAULT ''",
        # factor-combo-exploration §2.5/§2.6：探索候选自有组合成员快照（NULL=沿用父模型成员，既有行零影响）。
        "members_json": "TEXT",
    }
    for name, sql_type in batch_columns.items():
        if name not in columns:
            await conn.execute(
                text(f"ALTER TABLE weight_train_runs ADD COLUMN {name} {sql_type}")
            )
    await conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_weight_run_batch ON weight_train_runs(batch_id)"))


async def _ensure_weight_batch_columns(conn) -> None:
    """老库幂等补列（factor-combo-exploration §2.6/C-2）：``weight_train_batches`` 是既有表，
    ``create_all`` 不会为其补 ``kind``/``explore_plan_json`` 两列——须此处 ``ALTER TABLE ADD COLUMN``
    幂等补齐（仿 ``_ensure_weight_run_columns``），否则既有多模型库 INSERT/SELECT 立即 ``no such column``、
    连既有多方法批次一并崩（P0）。绝不指望 ``create_all`` 给既有表补列。
    """
    rows = (await conn.execute(text("PRAGMA table_info(weight_train_batches)"))).all()
    columns = {row[1] for row in rows}
    explore_columns = {
        "kind": "VARCHAR(16) DEFAULT 'multi_method'",
        "explore_plan_json": "TEXT DEFAULT '{}'",
    }
    for name, sql_type in explore_columns.items():
        if name not in columns:
            await conn.execute(
                text(f"ALTER TABLE weight_train_batches ADD COLUMN {name} {sql_type}")
            )


async def _ensure_live_auto_group_columns(conn) -> None:
    """老库幂等补列（auto-trading-earnings-persistence §5.2）：``strategy_runs`` 是既有表，
    ``create_all`` 不会给它补 ``group_id``/``group_started_ts``/``group_total_budget_usd`` 三列——
    须此处 ``ALTER TABLE ADD COLUMN`` 幂等补齐（仿 ``_ensure_trade_risk_columns``），否则老库读到
    历史聚合查询立即 ``no such column``。补列后 ``CREATE INDEX IF NOT EXISTS`` 建 group 索引（幂等、
    老库安全），支撑按 group_id 检索历史会话。
    """
    rows = (await conn.execute(text("PRAGMA table_info(strategy_runs)"))).all()
    columns = {row[1] for row in rows}
    group_columns = {
        "group_id": "VARCHAR(48)",              # 一次自动交易分组；非 live-auto / 老行为 NULL
        "group_started_ts": "INTEGER",          # 分组启动秒级 UTC（会话区间起点）
        "group_total_budget_usd": "FLOAT",      # 分组总投入预算（收益率分母，P1-4）
    }
    for name, sql_type in group_columns.items():
        if name not in columns:
            await conn.execute(
                text(f"ALTER TABLE strategy_runs ADD COLUMN {name} {sql_type}")
            )
    await conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_strategy_runs_group ON strategy_runs(group_id)"))


async def _reconcile_orphan_ai_trade_runs() -> None:
    """启动对账（ai-trade-stats-dashboard §3.5 / R-3）：把上轮进程残留的
    ``mode='ai-trade' AND status='running'`` 孤儿 run 回填 ``end_ts`` 并置 ``stopped``。

    进程启动时内存管理器为空，任何仍标 ``running`` 的 ai-trade run 必是上次崩溃 / 强杀遗留，
    否则历史列表会出现「幽灵进行中」。仿 ``_reconcile_orphan_live_auto_runs`` 用一次性 session，
    **整段 try/except 包裹、失败仅 warning 不阻断启动**（对账为非关键收敛，二次运行幂等）。
    """
    try:
        from . import repositories as repo
        async with get_sessionmaker()() as session:
            reconciled = await repo.reconcile_orphan_ai_trade_runs(session)
        if reconciled:
            logger.info("reconciled %d orphan ai-trade runs to stopped", reconciled)
    except Exception as exc:  # noqa: BLE001 —— 启动对账非关键，绝不因它拖垮服务启动
        logger.warning("ai-trade orphan reconcile skipped: %s", exc)


async def _seed_builtin_strategies() -> None:
    """为注册表中每个模板确保存在一行 builtin 策略实例（按注册顺序）。"""
    sm = get_sessionmaker()
    async with sm() as session:
        result = await session.execute(select(Strategy.template_key).where(Strategy.builtin == True))  # noqa: E712
        existing = {row[0] for row in result.all()}
        keys = BUILTIN_ORDER + [k for k in STRATEGY_REGISTRY if k not in BUILTIN_ORDER]
        for key in keys:
            cls = STRATEGY_REGISTRY.get(key)
            if cls is None or key in existing:
                continue
            session.add(Strategy(
                template_key=key,
                name=cls.meta.name,
                params_json=json.dumps(cls.meta.default_params, ensure_ascii=False),
                enabled=True,
                builtin=True,
            ))
        await session.commit()


async def _seed_builtin_factors() -> None:
    """空表时把因子库全部因子种成 source="builtin" 行，让「我的因子」首屏非空（幂等，按 source 判空）。

    内置因子只读（不可删，可激活 / 取消）；用户仍可基于库新增自定义因子或保存挖掘结果。
    """
    # 延迟导入避免 db 模块层在加载期硬依赖 engine.factors（→ signal_edge → strategies）。
    from ..engine.factors import FACTOR_LIBRARY
    from .models import Factor

    sm = get_sessionmaker()
    async with sm() as session:
        existing = (await session.execute(
            select(Factor.id).where(Factor.source == "builtin").limit(1))).first()
        if existing is not None:
            return
        for key, spec in FACTOR_LIBRARY.items():
            session.add(Factor(
                factor_key=key, name=spec.cn, params_json="{}", direction=spec.direction_hint,
                category=spec.category, source="builtin", enabled=True))
        await session.commit()


async def _seed_agent_providers() -> None:
    """仅空表时种入官方预置（openai/anthropic/gemini，空 key）+ `.env` KIMI 默认服务（幂等）。

    非空即跳过（保护用户在 UI 的增删改；桌面端每次启动注入 env 也不覆盖，R-Seed）。
    """
    # 延迟导入避免 db 模块层与 agent 层的循环依赖（db 不应在加载期依赖 agent）。
    from ..agent.providers import OFFICIAL_PRESETS
    from . import repositories as repo

    settings = get_settings()
    sm = get_sessionmaker()
    async with sm() as session:
        if await repo.count_agent_providers(session) > 0:
            return
        for preset in OFFICIAL_PRESETS:
            await repo.create_agent_provider(
                session, kind="official", slug=preset.slug,
                values=repo.ProviderValues(
                    label=preset.label, api_standard=preset.api_standard,
                    base_url=preset.base_url, api_key="", model=preset.model,
                    user_agent=preset.user_agent))
        # KIMI：.env 默认服务，自动种入并设为默认服务（任务要求；缺 key 时仍为默认，UI 引导补 key）。
        await repo.create_agent_provider(
            session, kind="official", slug="kimi",
            values=repo.ProviderValues(
                label="Kimi（内置默认）",
                api_standard=(settings.agent_provider or "anthropic").strip().lower(),
                base_url=settings.agent_base_url, api_key=settings.agent_api_key,
                model=settings.agent_model, user_agent=settings.agent_user_agent),
            is_default=True)


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
