"""接管 / 离线平仓的补采（W-A，A-11…A-16 / A-42 / G-16）。

这一整条工作流存在的理由是一条**幸存者偏差**：会被跨进程接管、或在暂停期被交易所平掉的仓位，
恰恰是那些**没能在会话内平掉的**（超时、被套、等反弹），在净额分布上系统性偏左。把它们整类
剔除在事件表之外，等于给整张经验表做了一次「只留活下来的」筛选。

**两条口径纪律各有一条用例守着**：
· A-12 —— 补采的 bps 必须与 `trip_ledger.settle` 独立算出的**逐位相等**（绝不另抄一份换算）；
· G-16 —— `origin` 是**双向**口径闸，只加写侧 = 写进去的一条也读不出来，而 UI 上还会显示
  「已补采 N 笔」——比不做更坏。
"""
from __future__ import annotations

import ast
import pathlib

from sqlalchemy import delete

from app.ai_auto.experience import taxonomy as tax
from app.ai_auto.experience.adopted import (
    AdoptedOpen,
    degraded_context,
    synth_from_contract,
    synth_open_snapshot,
)
from app.ai_auto.experience.events import EventIdentity
from app.ai_auto.experience.rules import ExperienceRule
from app.ai_auto.experience.runtime import HARVEST_REASONS, ExperienceRuntime
from app.ai_auto.trip_ledger import SettleContext, settle
from app.config import get_settings
from app.db import repositories as repo
from app.db.models import AiExperienceEvent, AppSetting
from app.db.session import dispose_engine, get_sessionmaker, init_db

_START = 1_700_000_000
_CT_VAL = 0.01

_CONTRACT = {"position": {"posId": "p-1", "posSide": "long", "avgPx": 60000.0,
                          "size": 3.0, "openCTime": _START}}
_CLOSE = {"ts": _START + 3600, "side": "close", "price": 61200.0, "qty": 3.0,
          "fee": -1.8, "pnl": 36.0, "positionAfter": 0.0, "reason": "offline_exit",
          "explain": {"kind": "offline_exit",
                      "meta": {"source": "ai", "exitSource": "offline",
                               "wallTs": _START + 3600}}}


def _runtime(*, write: bool = True, adopt: bool = True) -> ExperienceRuntime:
    settings = get_settings().model_copy(update={
        "ai_trade_net_pnl_enabled": True,
        "ai_trade_experience_adopt_harvest_enabled": adopt})
    return ExperienceRuntime(
        None, ExperienceRule(enabled=True, scope="symbol", write=write), settings,
        EventIdentity(group_id="grp-1", run_id=7, inst_id="BTC-USDT-SWAP", bar="15m"))


async def _fresh() -> None:
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(delete(AiExperienceEvent))
        await session.execute(delete(AppSetting))
        await session.commit()


async def _events() -> list[dict]:
    sm = get_sessionmaker()
    async with sm() as session:
        return await repo.load_experience_events(session, repo.ExperienceEventQuery())


# ---------------------------------------------------------------- A-11 / A-16
async def test_a11_a16_offline_close_lands_as_an_adopted_event_with_only_known_axes() -> None:
    """A-11 / A-16 · 离线平仓 ⇒ 事件入表、`origin="adopted"`、语境**只含**确定为真的轴。

    `hold_seconds` 必须在合理范围：合成断面的 `ts` 与平仓 trade 的 `ts` **同一时钟**（都是
    交易所墙钟秒）。一侧 bar 钟一侧墙钟会让它变成一个天文数字，且不会报错。
    """
    await _fresh()
    try:
        runtime = _runtime()
        assert runtime.adopt_open(synth_from_contract(
            _CONTRACT, seq=4, ct_val=_CT_VAL, open_fee_usd=1.8))
        await runtime.note_offline_close(_CLOSE, has_row=True)

        rows = await _events()
        assert len(rows) == 1
        row = rows[0]
        assert row["origin"] == "adopted"
        assert set(row["context"]) == {tax.AXIS_SYMBOL, tax.AXIS_BAR, tax.AXIS_SIDE,
                                       tax.AXIS_EXIT}
        assert tax.UNKNOWN not in {v for k, v in row["context"].items()
                                   if k != tax.AXIS_EXIT}
        assert 0 < row["holdSeconds"] <= 86400, "双时钟混用了（hold_seconds 是个天文数字）"
        assert runtime.adopted_harvested == 1 and runtime.harvest_gap == 0
    finally:
        await dispose_engine()


# ---------------------------------------------------------------- A-12
async def test_a12_net_bps_matches_an_independently_settled_trip_digit_for_digit() -> None:
    """**A-12** · 补采的 bps 与 `trip_ledger.settle` 独立算出的值**逐位相等**。

    这条用例的全部意义是证明本轮**没有另抄一份 bps 换算**：口径（`net_mode` / `ct_val` /
    双腿费 / `fee_known` 传染）只有一处实现，补采只是给它一个合成的开仓断面。
    """
    await _fresh()
    try:
        runtime = _runtime()
        runtime.adopt_open(synth_from_contract(
            _CONTRACT, seq=4, ct_val=_CT_VAL, open_fee_usd=1.8))
        await runtime.note_offline_close(_CLOSE, has_row=True)
        row = (await _events())[0]

        snapshot = synth_open_snapshot(
            seq=4, side="long",
            entry={"px": 60000.0, "qty": 3.0, "ts": _START}, open_fee_usd=1.8)
        oracle = settle(snapshot, _CLOSE,
                        SettleContext(net_mode=True, ct_val=_CT_VAL, exit_source="offline"))
        assert oracle is not None
        assert row["netBps"] == oracle.realized_net_bps
        assert row["grossBps"] == oracle.realized_gross_bps
        assert row["costBps"] == oracle.realized_cost_bps
        assert row["holdSeconds"] == oracle.hold_seconds
    finally:
        await dispose_engine()


# ---------------------------------------------------------------- A-13
async def test_a13_unknown_entry_fee_is_a_visible_gap_not_an_invented_zero() -> None:
    """A-13 · 入场费不可得 ⇒ `fee_known=False` ⇒ 该事件**仍然入表**但不进 bps 统计。

    被看见的缺失，而不是不存在的缺失：把它整条丢掉会让幸存者偏差原封不动，
    把它当成「零手续费」则会让整张表偏移一个开仓腿的成本。
    """
    await _fresh()
    try:
        runtime = _runtime()
        runtime.adopt_open(synth_from_contract(
            _CONTRACT, seq=4, ct_val=_CT_VAL, open_fee_usd=None))
        await runtime.note_offline_close(_CLOSE, has_row=True)
        row = (await _events())[0]
        assert row["feeKnown"] is False
        assert row["netBps"] is not None, "事件必须入表——它只是不进 bps 统计"
    finally:
        await dispose_engine()


# ---------------------------------------------------------------- A-14（F-6 的正解）
async def test_a14_both_offline_exits_report_a_reason_when_they_cannot_be_harvested() -> None:
    """**A-14（★ F-6 / E-5）** · 离线平仓采不到时，`harvest_gap` **加一**且原因**分得清**。

    `ExperienceCard` 上那句「另有 N 笔往返因续跑接管 / 暂停期离线平仓未能采集」在 v1 的后一半
    **恒为 0**——产品指名道姓承诺会计的一个数，从来没有被计过。`has_row` 把「采不到」分成两种
    真实存在的原因，而不是一句「其他」。
    """
    await _fresh()
    try:
        runtime = _runtime()
        runtime.adopt_open(synth_from_contract(
            _CONTRACT, seq=4, ct_val=_CT_VAL, open_fee_usd=1.8))
        await runtime.note_offline_close(None, has_row=False)
        assert runtime.harvest_gap == 1
        assert runtime.harvest_reasons["offlineNoReadback"] == 1

        await runtime.note_offline_close(None, has_row=True)
        assert runtime.harvest_gap == 2
        assert runtime.harvest_reasons["offlineInconsistent"] == 1

        wire = runtime.stats()
        assert wire["harvest"]["gap"] == 2
        assert set(wire["harvest"]["reasons"]) == set(HARVEST_REASONS), (
            "`reasons` 的键集必须闭合：多一个空键就是一个永远显示 0 的假分类")
        assert wire["harvestGap"] == 2, "旧标量键保留一个版本（桌面端旧包与新后端并存）"
    finally:
        await dispose_engine()


async def test_adopt_harvest_switch_falls_back_to_counting_only() -> None:
    """总闸关 ⇒ 逐字回退上一轮行为：不补采、也不建合成断面（一键回滚可用）。"""
    await _fresh()
    try:
        runtime = _runtime(adopt=False)
        assert runtime.adopt_open(synth_from_contract(
            _CONTRACT, seq=4, ct_val=_CT_VAL, open_fee_usd=1.8)) is False
        await runtime.note_offline_close(_CLOSE, has_row=True)
        assert await _events() == []
    finally:
        await dispose_engine()


async def test_read_only_mode_writes_nothing_at_all() -> None:
    """只读模式（`write=False`）⇒ 一个字都不写（沿用 `note_open` 的既有纪律）。"""
    await _fresh()
    try:
        runtime = _runtime(write=False)
        assert runtime.adopt_open(synth_from_contract(
            _CONTRACT, seq=4, ct_val=_CT_VAL, open_fee_usd=1.8)) is False
        await runtime.note_offline_close(_CLOSE, has_row=True)
        assert await _events() == []
    finally:
        await dispose_engine()


# ---------------------------------------------------------------- A-42 / G-16
async def test_a42_g16_origin_is_a_single_two_way_gate() -> None:
    """**A-42 / G-16（★ E-2 / J-7）** · `origin` 的口径闸是**单点**且**双向**的。

    只改写侧的后果不是抽象的：`adopted` 事件会写得进、读不出——一条也进不了桶、一条也进不了
    证据抽屉，而 `count_events` / `overview` 又不过滤 origin ⇒ L0 页眉那句「来自 X 组…的 Y 笔
    已结算往返」把这些**从未贡献过任何一条经验**的往返算了进去（`v1:I-9` 当场破）。
    """
    await _fresh()
    try:
        source = pathlib.Path("app/db/ai_experience_repositories.py").read_text(encoding="utf-8")
        assert "AiExperienceEvent.origin ==" not in source, (
            "还有一处按等值比 origin 的读点——四个读点必须共用 `STAT_ORIGINS` 这一个常量")
        assert repo.EXPERIENCE_STAT_ORIGINS == ("live", "adopted")
        # 默认口径必须**就是**那个常量（而不是某处又抄了一份字面量）。
        assert repo.ExperienceEventQuery().origins == repo.EXPERIENCE_STAT_ORIGINS

        runtime = _runtime()
        runtime.adopt_open(synth_from_contract(
            _CONTRACT, seq=4, ct_val=_CT_VAL, open_fee_usd=1.8))
        await runtime.note_offline_close(_CLOSE, has_row=True)

        sm = get_sessionmaker()
        async with sm() as session:
            rows = await repo.load_experience_events(session, repo.ExperienceEventQuery())
            counted = await repo.count_experience_events(session)
            overview = await repo.experience_overview(session)
        assert len(rows) == 1, "adopted 事件必须能被桶枚举的取数点读出来"
        assert counted == 1 and overview["events"] == 1, (
            "L0 页眉那个整数的口径必须**等于**真正参与桶枚举的事件数")
        assert overview["sessions"] == 1
    finally:
        await dispose_engine()


# ---------------------------------------------------------------- A-15（结构性隔离）
def test_a15_adopted_module_never_touches_the_attribution_ledger() -> None:
    """**A-15** · 合成断面**绝不**溢出到真实资金的归因链。

    那条链跑在真实资金上：喂给它一个合成断面会改变 `aiStats` / 已实现 edge 校准 / 验收卡上
    用户已经在看的数字。判据按 AST 而不是按原文——`adopted.py` 的 docstring 恰恰要逐字解释
    「为什么这里不能有它」，按原文扫会把解释本身判成违规（`v1:I-2` 的成文教训）。
    """
    tree = ast.parse(pathlib.Path("app/ai_auto/experience/adopted.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            node.body = [stmt for stmt in node.body
                         if not (isinstance(stmt, ast.Expr)
                                 and isinstance(stmt.value, ast.Constant)
                                 and isinstance(stmt.value.value, str))]
    code = ast.unparse(tree)
    for forbidden in ("AttributionLedger", "note_open", "cost_runtime", "record_close"):
        assert forbidden not in code, f"`adopted.py` 碰了归因链：{forbidden}"


def test_degraded_context_never_emits_an_unknown_axis() -> None:
    """降级语境只写确定为真的轴；折不进闭合词表的一律**不写**（绝不留一个 `unknown`）。"""
    full = degraded_context(inst_id="BTC-USDT-SWAP", bar="15m", side="long",
                            horizon_profile_id="intraday_swing")
    assert full == {tax.AXIS_SYMBOL: "BTC-USDT-SWAP", tax.AXIS_BAR: "15m",
                    tax.AXIS_SIDE: "long", tax.AXIS_HORIZON: "intraday_swing"}
    # 「没挂档」与「不知道有没有挂档」是两件事：后者连 `horizon` 都不写。
    assert tax.AXIS_HORIZON not in degraded_context(
        inst_id="BTC-USDT-SWAP", bar="15m", side="long", horizon_profile_id=None)
    thin = degraded_context(inst_id="BTC-USDT-SWAP", bar="15m", side="",
                            horizon_profile_id="nope")
    assert tax.UNKNOWN not in thin.values()


def test_synth_from_contract_reads_the_column_name_that_is_really_in_members_json() -> None:
    """**回归**（终审）· 持仓断面的入场价列名是 `avgEntry`，**不是** `avgPx`。

    `members_json` 里的那一行由 `session._state_position_snapshot()` 与 `_exchange_position()`
    写出，两处写的都是 `avgEntry`（`avgPx` 只是交易所归一行的原始列名，落库那一步就换掉了）。
    读错列名不会抛异常，只会让本函数**恒回 `None`** ⇒ 建不出合成断面 ⇒ 离线平仓那条往返照旧
    只记一个采集缺口，而 `tsc` / `ruff` / 全量用例全绿——W-A 的这一半在生产上一条事件都产不出来。
    """
    real = {"side": "long", "size": 3.0, "avgEntry": 60000.0, "posId": "p-1",
            "openCTime": _START, "openWallTs": _START, "positionSeq": 4}
    out = synth_from_contract({"position": real}, seq=4, ct_val=_CT_VAL, open_fee_usd=1.8)
    assert isinstance(out, AdoptedOpen), "读了一个 members_json 里根本不存在的列名"
    assert out.entry["px"] == 60000.0 and out.entry["qty"] == 3.0
    assert out.side == "long" and out.entry["ts"] == _START


async def test_offline_close_harvests_from_the_real_members_json_shape() -> None:
    """同一条回归的**端到端**形态：真实断面形状下，离线平仓必须真的产出一条 `adopted` 事件。

    上面那条守列名，这条守「列名对了之后整条链真的通」——两者缺一，都能让这一半静默回到
    「只计缺口、不补采」，而那正是本轮要消灭的幸存者偏差。
    """
    await _fresh()
    try:
        runtime = _runtime()
        real = {"position": {"side": "long", "size": 3.0, "avgEntry": 60000.0,
                             "posId": "p-1", "openCTime": _START, "openWallTs": _START,
                             "positionSeq": 4}}
        assert runtime.adopt_open(synth_from_contract(
            real, seq=4, ct_val=_CT_VAL, open_fee_usd=1.8))
        await runtime.note_offline_close(_CLOSE, has_row=True)

        rows = await _events()
        assert len(rows) == 1 and rows[0]["origin"] == "adopted"
        assert runtime.adopted_harvested == 1 and runtime.harvest_gap == 0
    finally:
        await dispose_engine()


def test_synth_open_snapshot_refuses_to_invent_a_price_or_a_size() -> None:
    """价 / 量不可用 ⇒ `None`（调用方据此计缺口）；**绝不编一个 0**。"""
    assert synth_open_snapshot(seq=1, side="long", entry={}, open_fee_usd=None) is None
    assert synth_open_snapshot(seq=1, side="long",
                               entry={"px": 0.0, "qty": 3.0}, open_fee_usd=None) is None
    assert synth_from_contract({"position": {}}, seq=1, ct_val=0.01, open_fee_usd=None) is None
    ok = synth_from_contract(_CONTRACT, seq=1, ct_val=0.01, open_fee_usd=None)
    assert isinstance(ok, AdoptedOpen) and ok.side == "long"
