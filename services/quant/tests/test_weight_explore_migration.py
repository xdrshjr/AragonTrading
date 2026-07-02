"""老库幂等补列迁移测试（factor-combo-exploration §6.1 / C-2）：既有 weight_train_runs / weight_train_batches
表（无 members_json / kind / explore_plan_json）经 init_db 后被 ALTER 幂等补列，既有 INSERT/SELECT 不
``no such column``（``create_all`` 绝不给已存在的表补列，故必须走 _ensure_* ALTER 补列器）。
"""
from __future__ import annotations

from sqlalchemy import text

from app.db import repositories as repo
from app.db.session import dispose_engine, get_engine, get_sessionmaker, init_db

# 补列前的「老库」schema（batch / explore 特性之前的 weight_train_runs：无 batch_id/variant_label/members_json）。
_OLD_RUNS_DDL = """
CREATE TABLE weight_train_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id INTEGER NOT NULL,
    status VARCHAR(16) DEFAULT 'running',
    method VARCHAR(24),
    inst_id VARCHAR(32),
    bar VARCHAR(8),
    start_ts INTEGER DEFAULT 0,
    end_ts INTEGER DEFAULT 0,
    horizon INTEGER DEFAULT 6,
    config_json TEXT DEFAULT '{}',
    curve_json TEXT DEFAULT '[]',
    weights_json TEXT DEFAULT '{}',
    norm_stats_json TEXT DEFAULT '{}',
    metrics_json TEXT,
    provenance_json TEXT DEFAULT '{}',
    trainer_version VARCHAR(64),
    note VARCHAR(256) DEFAULT '',
    error_code VARCHAR(64) DEFAULT '',
    error_message VARCHAR(256) DEFAULT '',
    started_ts INTEGER DEFAULT 0,
    finished_ts INTEGER
)
"""
# 补列前的「老库」weight_train_batches：无 kind / explore_plan_json。
_OLD_BATCHES_DDL = """
CREATE TABLE weight_train_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label VARCHAR(128),
    inst_id VARCHAR(32),
    bar VARCHAR(8),
    start_ts INTEGER DEFAULT 0,
    end_ts INTEGER DEFAULT 0,
    horizon INTEGER DEFAULT 6,
    members_json TEXT DEFAULT '[]',
    config_json TEXT DEFAULT '{}',
    parent_model_id INTEGER,
    factor_set_id INTEGER,
    origin VARCHAR(16) DEFAULT 'model',
    selected_run_id INTEGER,
    created_at DATETIME,
    updated_at DATETIME
)
"""


async def test_old_db_alters_new_columns_and_inserts_ok():
    await init_db()                                      # 先建全套表 / 拿到引擎
    engine = get_engine()
    async with engine.begin() as conn:                  # 重建两表为「无新列」的老库 schema
        await conn.execute(text("DROP TABLE IF EXISTS weight_train_runs"))
        await conn.execute(text("DROP TABLE IF EXISTS weight_train_batches"))
        await conn.execute(text(_OLD_RUNS_DDL))
        await conn.execute(text(_OLD_BATCHES_DDL))
    try:
        await init_db()                                 # create_all 跳过既有表 → _ensure_* ALTER 补新列
        async with engine.begin() as conn:
            run_cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(weight_train_runs)"))).all()}
            batch_cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(weight_train_batches)"))).all()}
        assert {"batch_id", "variant_label", "members_json"} <= run_cols
        assert {"kind", "explore_plan_json"} <= batch_cols

        # 既有单次训练 INSERT（members=None → members_json NULL）+ 探索候选 INSERT（members=子集）均不 no such column。
        sm = get_sessionmaker()
        async with sm() as s:
            model = await repo.create_weight_model(s, spec=repo.WeightModelSpec(
                name="m", inst_id="BTC-USDT-SWAP", bar="1H",
                members=[{"factorKey": "mom_roc", "params": {}, "weight": 0, "direction": "long"}],
                method="linear", config={}, horizon=6, source="builder"))
            plain = await repo.add_weight_run(s, start=repo.WeightRunStart(
                model_id=model.id, method="linear", inst_id="BTC-USDT-SWAP", bar="1H",
                start_ts=0, end_ts=1, horizon=6, config={}, started_ts=0))
            explore = await repo.add_queued_weight_run(s, start=repo.WeightRunStart(
                model_id=model.id, method="linear", inst_id="BTC-USDT-SWAP", bar="1H",
                start_ts=0, end_ts=1, horizon=6, config={}, started_ts=0, batch_id=1,
                variant_label="C0", members=[{"factorKey": "rev_rsi", "params": {}, "direction": "short"}]))
            batch = await repo.create_weight_batch(s, spec=repo.WeightBatchSpec(
                label="b", inst_id="BTC-USDT-SWAP", bar="1H", start_ts=0, end_ts=1, horizon=6,
                members=[], config={}, parent_model_id=model.id, kind="explore",
                explore_plan={"strategy": "manual"}))
        async with sm() as s:
            plain_row = await repo.get_weight_run(s, model.id, plain.id)
            explore_row = await repo.get_weight_run(s, model.id, explore.id)
            batch_row = await repo.get_weight_batch(s, batch.id)
        assert plain_row.members_json is None            # 既有行为：单次训练成员为 NULL
        assert explore_row.members_json is not None       # 探索候选携各自组合
        assert batch_row.kind == "explore"
    finally:
        await dispose_engine()
