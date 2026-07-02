"""批次对账 + 老库幂等补列测试（weight-multi-model-training §2.6）。

**回归 R1/P0**：老库（``weight_train_runs`` 无 ``batch_id``/``variant_label`` 列）经 ``_ensure_weight_run_columns``
幂等补列后 INSERT/SELECT 正常，既有单次训练不回归——``create_all`` 绝不给既有表补列，故补列补丁是硬门禁。
对账：重启（模拟）后残留 running/queued 候选统一改判 failed(``WEIGHT_TRAIN_INTERRUPTED``) 并 repoint。
"""
from __future__ import annotations

from sqlalchemy import delete, text

from app.db import repositories as repo
from app.db.models import WeightModel, WeightTrainBatch, WeightTrainRun
from app.db.session import (
    _ensure_weight_run_columns,
    dispose_engine,
    get_engine,
    get_sessionmaker,
    init_db,
)

_OLD_SCHEMA = """
CREATE TABLE weight_train_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id INTEGER,
    status VARCHAR(16),
    method VARCHAR(24),
    inst_id VARCHAR(32),
    bar VARCHAR(8),
    start_ts INTEGER,
    end_ts INTEGER,
    horizon INTEGER,
    config_json TEXT,
    curve_json TEXT,
    weights_json TEXT,
    norm_stats_json TEXT,
    metrics_json TEXT,
    provenance_json TEXT,
    trainer_version VARCHAR(64),
    note VARCHAR(256),
    error_code VARCHAR(64),
    error_message VARCHAR(256),
    started_ts INTEGER,
    finished_ts INTEGER
)
"""


async def test_ensure_weight_run_columns_patches_old_db_idempotently():
    """老库缺 batch_id/variant_label → 补列补丁幂等补齐；老行存活、可插入带 batch_id 的新行。"""
    await init_db()
    engine = get_engine()
    try:
        # 重建为**老库形态**（无 batch_id / variant_label），插一条老行。
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS weight_train_runs"))
            await conn.execute(text(_OLD_SCHEMA))
            await conn.execute(text(
                "INSERT INTO weight_train_runs (id, model_id, status, method, inst_id, bar) "
                "VALUES (1, 7, 'succeeded', 'linear', 'BTC-USDT-SWAP', '1H')"))
        # 补列补丁（幂等：连跑两次不报错）。
        async with engine.begin() as conn:
            await _ensure_weight_run_columns(conn)
            await _ensure_weight_run_columns(conn)
            cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(weight_train_runs)"))).all()}
            assert "batch_id" in cols and "variant_label" in cols
            # 老行存活、SELECT 正常（既有单次训练不回归）。
            row = (await conn.execute(text("SELECT status, batch_id FROM weight_train_runs WHERE id=1"))).first()
            assert row[0] == "succeeded" and row[1] is None
            # 可插入带 batch_id / variant_label 的新行（批次候选路径）。
            await conn.execute(text(
                "INSERT INTO weight_train_runs (model_id, batch_id, status, method, inst_id, bar, variant_label) "
                "VALUES (7, 5, 'queued', 'equal', 'BTC-USDT-SWAP', '1H', 'L2=0.1')"))
            new = (await conn.execute(text(
                "SELECT batch_id, variant_label FROM weight_train_runs WHERE batch_id=5"))).first()
            assert new[0] == 5 and new[1] == "L2=0.1"
    finally:
        await dispose_engine()


async def test_reconcile_running_and_queued_candidates_to_failed():
    """重启对账：批次残留 running + queued 候选统一改判 failed(WEIGHT_TRAIN_INTERRUPTED) 并 repoint 父模型。"""
    await init_db()
    sm = get_sessionmaker()
    try:
        async with sm() as s:
            await s.execute(delete(WeightTrainRun))
            await s.execute(delete(WeightTrainBatch))
            await s.execute(delete(WeightModel))
            await s.commit()
        async with sm() as s:
            parent = await repo.create_weight_model(s, spec=repo.WeightModelSpec(
                name="批次父", inst_id="BTC-USDT-SWAP", bar="1H",
                members=[{"factorKey": "mom_roc", "params": {}, "direction": "long"}],
                method="linear", config={}, horizon=6, source="batch"))
            batch = await repo.create_weight_batch(s, spec=repo.WeightBatchSpec(
                label="批次", inst_id="BTC-USDT-SWAP", bar="1H", start_ts=0, end_ts=1, horizon=6,
                members=[], config={}, parent_model_id=parent.id))
            running = await repo.add_weight_run(s, status="running", start=repo.WeightRunStart(
                model_id=parent.id, method="linear", inst_id="BTC-USDT-SWAP", bar="1H",
                start_ts=0, end_ts=1, horizon=6, config={}, started_ts=0, batch_id=batch.id))
            queued = await repo.add_queued_weight_run(s, start=repo.WeightRunStart(
                model_id=parent.id, method="equal", inst_id="BTC-USDT-SWAP", bar="1H",
                start_ts=0, end_ts=1, horizon=6, config={}, started_ts=0, batch_id=batch.id))
            running_id, queued_id = running.id, queued.id

        async with sm() as s:
            reconciled = await repo.reconcile_dangling_weight_runs(s)
        assert reconciled == 2

        async with sm() as s:
            runs = {r.id: r for r in await repo.list_batch_runs(s, batch.id)}
        assert runs[running_id].status == "failed"
        assert runs[running_id].error_code == "WEIGHT_TRAIN_INTERRUPTED"
        assert runs[queued_id].status == "failed"
        assert runs[queued_id].error_code == "WEIGHT_TRAIN_INTERRUPTED"
    finally:
        await dispose_engine()
