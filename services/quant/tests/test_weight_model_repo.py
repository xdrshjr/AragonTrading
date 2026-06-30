"""权重模型 / 训练记录数据访问层测试：CRUD、状态机、剪枝、指针完整性（R-05）、启动对账（R-04）。

共享进程 DB，每例先清空两表隔离（呼应 aragon-quant-test-isolation）。
"""
from __future__ import annotations

from sqlalchemy import delete

from app.db import repositories as repo
from app.db.models import WeightModel, WeightTrainRun
from app.db.session import dispose_engine, get_sessionmaker, init_db

_SPEC = repo.WeightModelSpec(
    name="测试模型", inst_id="BTC-USDT-SWAP", bar="1H",
    members=[{"factorKey": "mom_roc", "params": {}, "weight": 0.0, "direction": "long"}],
    method="linear", config={"normWindow": 100}, horizon=6, source="builder", factor_set_id=7)


async def _fresh():
    await init_db()
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(delete(WeightTrainRun))
        await s.execute(delete(WeightModel))
        await s.commit()
    return sm


def _run_start(model_id: int, started: int = 1_700_000_000) -> repo.RunStart:
    return repo.WeightRunStart(model_id=model_id, method="linear", inst_id="BTC-USDT-SWAP", bar="1H",
                         start_ts=started, end_ts=started + 1000, horizon=6,
                         config={"normWindow": 100}, started_ts=started)


def _success(ic_out: float, finished: int = 1_700_001_000) -> repo.RunOutcome:
    return repo.WeightRunOutcome(status="succeeded", finished_ts=finished,
                           curve=[{"step": 0, "fraction": 1.0, "validIc": ic_out}],
                           weights={"mom_roc": 1.0}, norm_stats={"normWindow": 100, "clip": 4.0},
                           metrics={"icIn": 0.5, "icOut": ic_out, "nTrain": 100, "nValid": 40},
                           provenance={"instId": "BTC-USDT-SWAP"}, trainer_version=None)


async def test_model_crud_roundtrip():
    sm = await _fresh()
    try:
        async with sm() as s:
            model = await repo.create_weight_model(s, spec=_SPEC)
            assert model.id is not None and model.factor_set_id == 7
            fetched = await repo.get_weight_model(s, model.id)
            assert fetched is not None and fetched.name == "测试模型"
            updated = await repo.update_weight_model(
                s, model.id, patch=repo.WeightModelPatch(name="改名", enabled=False))
            assert updated.name == "改名" and updated.enabled is False
            rows = await repo.list_weight_models(s, enabled=False)
            assert any(r.id == model.id for r in rows)
            assert await repo.delete_weight_model(s, model.id) == "ok"
            assert await repo.get_weight_model(s, model.id) is None
    finally:
        await dispose_engine()


async def test_finalize_success_updates_pointer_and_best_ic():
    sm = await _fresh()
    try:
        async with sm() as s:
            model = await repo.create_weight_model(s, spec=_SPEC)
            run = await repo.add_weight_run(s, start=_run_start(model.id))
            assert run.status == "running"
            await repo.finalize_weight_run(s, run.id, outcome=_success(0.30), run_cap=50)
            refreshed = await repo.get_weight_model(s, model.id)
            assert refreshed.latest_run_id == run.id
            assert abs(refreshed.best_valid_ic - 0.30) < 1e-9
            stored = await repo.get_weight_run(s, model.id, run.id)
            assert stored.status == "succeeded" and stored.metrics_json
    finally:
        await dispose_engine()


async def test_failed_run_keeps_pointer_null():
    sm = await _fresh()
    try:
        async with sm() as s:
            model = await repo.create_weight_model(s, spec=_SPEC)
            run = await repo.add_weight_run(s, start=_run_start(model.id))
            await repo.finalize_weight_run(s, run.id, outcome=repo.WeightRunOutcome(
                status="failed", finished_ts=1, error_code="WEIGHT_TRAIN_INSUFFICIENT_SAMPLES"), run_cap=50)
            refreshed = await repo.get_weight_model(s, model.id)
            assert refreshed.latest_run_id is None and refreshed.best_valid_ic is None
            stored = await repo.get_weight_run(s, model.id, run.id)
            assert stored.status == "failed" and stored.error_code == "WEIGHT_TRAIN_INSUFFICIENT_SAMPLES"
    finally:
        await dispose_engine()


async def test_best_valid_ic_tracks_max_over_succeeded():
    sm = await _fresh()
    try:
        async with sm() as s:
            model = await repo.create_weight_model(s, spec=_SPEC)
            for ic in (0.10, 0.42, 0.25):
                run = await repo.add_weight_run(s, start=_run_start(model.id))
                await repo.finalize_weight_run(s, run.id, outcome=_success(ic), run_cap=50)
            refreshed = await repo.get_weight_model(s, model.id)
            assert abs(refreshed.best_valid_ic - 0.42) < 1e-9   # 历史最佳验证 IC
            # latest 指针指向最新一条（最大 id），而非最佳。
            runs = await repo.list_weight_runs(s, model.id)
            assert refreshed.latest_run_id == max(r.id for r in runs)
    finally:
        await dispose_engine()


async def test_prune_keeps_cap_and_never_pointer():
    sm = await _fresh()
    try:
        async with sm() as s:
            model = await repo.create_weight_model(s, spec=_SPEC)
            for _ in range(5):
                run = await repo.add_weight_run(s, start=_run_start(model.id))
                await repo.finalize_weight_run(s, run.id, outcome=_success(0.2), run_cap=3)
            runs = await repo.list_weight_runs(s, model.id)
            assert len(runs) == 3   # 超 cap=3 的最旧被剪枝
            refreshed = await repo.get_weight_model(s, model.id)
            assert refreshed.latest_run_id in {r.id for r in runs}   # 指针记录绝不被剪
    finally:
        await dispose_engine()


async def test_delete_pointer_run_repoints(monkeypatch):
    sm = await _fresh()
    try:
        async with sm() as s:
            model = await repo.create_weight_model(s, spec=_SPEC)
            first = await repo.add_weight_run(s, start=_run_start(model.id))
            await repo.finalize_weight_run(s, first.id, outcome=_success(0.4), run_cap=50)
            second = await repo.add_weight_run(s, start=_run_start(model.id))
            await repo.finalize_weight_run(s, second.id, outcome=_success(0.1), run_cap=50)
            # 删掉 latest 指向记录 → 指针回退到剩余最近 succeeded（first），best_ic 重算。
            assert await repo.delete_weight_run(s, model.id, second.id) == "ok"
            refreshed = await repo.get_weight_model(s, model.id)
            assert refreshed.latest_run_id == first.id
            assert abs(refreshed.best_valid_ic - 0.4) < 1e-9
            # 删尽 succeeded → 指针 / 冗余双双置 None（无悬挂）。
            assert await repo.delete_weight_run(s, model.id, first.id) == "ok"
            refreshed = await repo.get_weight_model(s, model.id)
            assert refreshed.latest_run_id is None and refreshed.best_valid_ic is None
    finally:
        await dispose_engine()


async def test_prune_preserves_best_ic_run():
    """H-B8：自动剪枝永不删当前持 best_valid_ic 的 succeeded run → 「历史最佳」绝不静默退化。"""
    sm = await _fresh()
    try:
        async with sm() as s:
            model = await repo.create_weight_model(s, spec=_SPEC)
            best = await repo.add_weight_run(s, start=_run_start(model.id))   # 最旧但 IC 最高
            await repo.finalize_weight_run(s, best.id, outcome=_success(0.9), run_cap=3)
            for _ in range(4):   # 后续 4 条低 IC，超 cap=3 触发剪枝
                run = await repo.add_weight_run(s, start=_run_start(model.id))
                await repo.finalize_weight_run(s, run.id, outcome=_success(0.1), run_cap=3)
            refreshed = await repo.get_weight_model(s, model.id)
            assert abs(refreshed.best_valid_ic - 0.9) < 1e-9   # 历史最佳不退化（best-IC run 受保护）
            assert await repo.get_weight_run(s, model.id, best.id) is not None   # best-IC run 未被自动剪
    finally:
        await dispose_engine()


async def test_prune_logs_dropped_count(caplog):
    """剪枝有删除即显式 log 丢弃数（无静默截断，呼应清洁度「绝不静默」）。"""
    import logging
    sm = await _fresh()
    try:
        caplog.set_level(logging.INFO, logger="aragon")
        async with sm() as s:
            model = await repo.create_weight_model(s, spec=_SPEC)
            for _ in range(5):   # 超 cap=2 必有剪枝
                run = await repo.add_weight_run(s, start=_run_start(model.id))
                await repo.finalize_weight_run(s, run.id, outcome=_success(0.2), run_cap=2)
        assert any("pruned" in rec.message for rec in caplog.records)
    finally:
        await dispose_engine()


async def test_delete_running_run_rejected():
    sm = await _fresh()
    try:
        async with sm() as s:
            model = await repo.create_weight_model(s, spec=_SPEC)
            run = await repo.add_weight_run(s, start=_run_start(model.id))
            assert await repo.delete_weight_run(s, model.id, run.id) == "running"   # 进行中拒删（→409）
    finally:
        await dispose_engine()


async def test_reconcile_dangling_runs():
    sm = await _fresh()
    try:
        async with sm() as s:
            model = await repo.create_weight_model(s, spec=_SPEC)
            dangling = await repo.add_weight_run(s, start=_run_start(model.id))   # 残留 running
            count = await repo.reconcile_dangling_weight_runs(s)
            assert count >= 1
            stored = await repo.get_weight_run(s, model.id, dangling.id)
            assert stored.status == "failed" and stored.error_code == "WEIGHT_TRAIN_INTERRUPTED"
    finally:
        await dispose_engine()


async def test_delete_model_cascades_runs():
    sm = await _fresh()
    try:
        async with sm() as s:
            model = await repo.create_weight_model(s, spec=_SPEC)
            run = await repo.add_weight_run(s, start=_run_start(model.id))
            await repo.finalize_weight_run(s, run.id, outcome=_success(0.2), run_cap=50)
            assert await repo.delete_weight_model(s, model.id) == "ok"
            assert await repo.get_weight_run(s, model.id, run.id) is None   # 连带删训练记录
    finally:
        await dispose_engine()


async def test_find_model_by_factor_set():
    sm = await _fresh()
    try:
        async with sm() as s:
            model = await repo.create_weight_model(s, spec=_SPEC)   # factor_set_id=7
            found = await repo.find_weight_model_by_factor_set(s, 7)
            assert found is not None and found.id == model.id
            assert await repo.find_weight_model_by_factor_set(s, 999) is None
    finally:
        await dispose_engine()
