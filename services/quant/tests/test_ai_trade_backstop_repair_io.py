"""交易所侧兜底自愈 · IO 层（backstop-repair §6.1 B 组）。

守的是本仓库**第一条新增下单写路径**：请求体的每一个字段、H2 越界锁的每一条判据、以及下单时序
里那一次「按实时 `|pos|` 重算」。交易客户端与公共 rest 全程打桩，不依赖网络。

**铁律**：一律用占位假凭证，严禁写入真实凭证（沿用 `test_trading_api` 规约）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.db import repositories as repo
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.okx.accounts import AccountCredentials
from app.okx.error_text import trade_code_message
from app.okx.order_builder import (
    AlgoStopInput,
    InstrumentMeta,
    build_algo_stop_payload,
    build_attach_algo_orders,
    ensure_close_only,
    validate_algo_stop,
)
from app.okx.private_client import OKXPrivateError
from app.okx.trade_client import OKXTradeClient
from app.okx.trading import _BACKSTOP_LOCAL_REJECT_CODES, _map_trade_code, place_backstop_stop

_META = InstrumentMeta(ct_val=0.01, ct_val_ccy="BTC", lot_sz=0.1, min_sz=0.1, tick_sz=0.1,
                       max_lever=125.0, ct_type="linear", settle_ccy="USDT")
_RAW_META = {"ctVal": 0.01, "ctValCcy": "BTC", "lotSz": 0.1, "minSz": 0.1, "tickSz": 0.1,
             "maxLever": 125.0, "ctType": "linear", "settleCcy": "USDT"}
_CREDS = AccountCredentials(api_key="test-ak-demo-only", secret_key="test-sk-demo-secret",
                            passphrase="test-pp-123456")


def _stop(**over) -> AlgoStopInput:
    base = {"inst_id": "BTC-USDT-SWAP", "pos_side": "long", "pos": 1.0, "td_mode": "cross",
            "sz": 1.0, "stop_px": 61000.0, "algo_cl_ord_id": "arhabc123000300",
            "self_position_sz": 1.0, "covered_sz": 0.0}
    return AlgoStopInput(**{**base, **over})


# ==================== B-1~6 · 请求体组装 ====================
def test_net_mode_locks_the_leg_with_reduce_only():
    """B-1：net 模式带 `reduceOnly`、**不**带 `posSide`（与 `build_close_order_payload` 同款）。"""
    payload = build_algo_stop_payload(_stop(pos_side="net"), _META, "net_mode")
    assert payload["reduceOnly"] is True
    assert "posSide" not in payload


def test_hedge_mode_locks_the_leg_with_pos_side():
    """B-2：对冲模式带 `posSide`、**不**带 `reduceOnly`（部分账户模式对 reduceOnly 兼容性不同）。"""
    payload = build_algo_stop_payload(_stop(), _META, "long_short_mode")
    assert payload["posSide"] == "long"
    assert "reduceOnly" not in payload


def test_the_three_stop_fields_match_the_attached_oco_verbatim():
    """B-3：三个止损字段与随单 OCO 的 `build_attach_algo_orders` **逐字一致**，不发明第二套写法。"""
    payload = build_algo_stop_payload(_stop(), _META, "net_mode")
    attach = build_attach_algo_orders(None, 61000.0, _META.tick_sz)[0]
    assert payload["ordType"] == "conditional"
    assert payload["slTriggerPx"] == attach["slTriggerPx"]
    assert payload["slOrdPx"] == attach["slOrdPx"] == "-1"
    assert payload["slTriggerPxType"] == attach["slTriggerPxType"] == "last"


def test_size_is_floored_to_the_lot_step():
    """B-4：`sz` 按 `lotSz` **向下**取整（宁可少下，杜绝超额）。"""
    payload = build_algo_stop_payload(_stop(sz=0.37), _META, "net_mode")
    assert payload["sz"] == "0.3"


@pytest.mark.parametrize("pos_side,pos,side", [("long", 1.0, "sell"), ("short", -1.0, "buy"),
                                               ("net", 1.0, "sell"), ("net", -1.0, "buy")])
def test_the_closing_leg_direction_reuses_close_side(pos_side, pos, side):
    """B-5~6：平多 ⇒ `sell`、平空 ⇒ `buy`（复用 `_close_side`，一行都不新写）。"""
    payload = build_algo_stop_payload(_stop(pos_side=pos_side, pos=pos), _META,
                                      "net_mode" if pos_side == "net" else "long_short_mode")
    assert payload["side"] == side


def test_a_payload_without_a_lock_field_is_refused():
    """H2 第 4 条：`reduceOnly` 与 `posSide` **二者必居其一**——「永不建立敞口」的代码化断言。

    两个都缺时那张单在触发时可能开出**反向敞口**，那正是「把安全层变成亏钱机器」的形态。
    """
    with pytest.raises(OKXPrivateError) as caught:
        ensure_close_only({"instId": "BTC-USDT-SWAP", "ordType": "conditional"})
    assert caught.value.code == "LIVE_BACKSTOP_REJECTED_LOCAL"
    ensure_close_only({"reduceOnly": True})       # 任一在场即通过
    ensure_close_only({"posSide": "long"})


# ==================== B-7~12 / B-19b · H2 越界锁 ====================
@pytest.mark.parametrize("px", [0.0, -1.0, float("nan")])
def test_an_invalid_trigger_price_is_refused_locally(px):
    """B-7：触发价非法即本地拒，**绝不发往 OKX**。"""
    with pytest.raises(OKXPrivateError) as caught:
        validate_algo_stop(_stop(stop_px=px), _META, abs_pos=1.0)
    assert caught.value.code == "LIVE_BACKSTOP_REJECTED_LOCAL"


def test_a_tail_position_below_min_size_is_refused_honestly():
    """B-8：收敛取整后 `< minSz` ⇒ 本地拒并说清「未覆盖差额只有这么多」（尾仓补不出来是事实）。"""
    with pytest.raises(OKXPrivateError) as caught:
        validate_algo_stop(_stop(sz=0.05, self_position_sz=0.05), _META, abs_pos=0.05)
    assert caught.value.code == "LIVE_BACKSTOP_REJECTED_LOCAL"
    assert "最小下单量" in caught.value.message


def test_only_the_uncovered_difference_survives_validation():
    """B-9：已覆盖部分被扣掉——只补差额。"""
    out = validate_algo_stop(_stop(sz=1.0, covered_sz=0.4), _META, abs_pos=1.0)
    assert out["sz"] == pytest.approx(0.6)


def test_a_large_accumulated_position_is_not_rejected_by_the_notional_cap():
    """B-10 改判 [P1-1]：5000 USDT 的累积仓**必须通过**——补挂腿是减仓腿，豁免名义上限。

    `live_max_order_notional_usd` 默认仅 1000 USDT，它守的是「单笔**新增**敞口不得过大」；而持仓
    可以由多次加仓合法累积而成。照单笔开仓的口径拒单，会让**仓位越大、越裸露、越需要兜底时补挂
    越是被本地拒死**——一条只在无关紧要的小仓上生效的安全层。仓库既有纪律（`okx/trading.py`
    :224-225）早已写明平仓路径豁免它。
    """
    big = _stop(sz=800.0, self_position_sz=800.0)      # 800 张 × 0.01 BTC × 61000 ≈ 488k USDT
    assert validate_algo_stop(big, _META, abs_pos=800.0)["sz"] == pytest.approx(800.0)


def test_an_oversized_local_reading_converges_instead_of_throwing():
    """B-19b [P0-4]：本地读数 1.0、实时 `abs_pos` 0.4 ⇒ 收敛到 0.4，**不抛**。

    本地读数暂时偏大是正常的时序差（部分成交后尚未对齐），不是故障。v1 在这一格抛
    `LIVE_BACKSTOP_SIZE_EXCEEDS`、烧掉一次频率配额、并向用户推一条吓人的失败——把一次读数偏差
    渲染成一次真实故障。
    """
    out = validate_algo_stop(_stop(sz=1.0, self_position_sz=1.0), _META, abs_pos=0.4)
    assert out["sz"] == pytest.approx(0.4)


def test_the_account_wide_position_never_widens_the_repair():
    """B-11 [P0-4]：账户上另有他仓（`abs_pos=3.0`）时，补挂仍只覆盖本层自管的 1.0 张。

    §2.1 第 2 条不变式有两半：「永不建立敞口」与「**永不处置别人的敞口**」。按全账户 `abs_pos`
    补出来的 `reduceOnly` 止损一旦触发，就替用户平掉了他手工建的仓。
    """
    out = validate_algo_stop(_stop(sz=3.0, self_position_sz=1.0), _META, abs_pos=3.0)
    assert out["sz"] == pytest.approx(1.0)


def test_local_reject_codes_are_passed_through_not_flattened():
    """B-12：两个本地码必须原样透传——否则 `_map_okx_code` 会把它们拍平成 `OKX_ERROR`，
    端点就再也分不清「本地拒」与「交易所拒」。"""
    for code in _BACKSTOP_LOCAL_REJECT_CODES:
        assert _map_trade_code(code) == code


def test_every_normalised_code_has_a_chinese_label():
    """**AST 门禁**：`_map_trade_code` 能产出的每一个归一码，`okx/error_text.py` 里都要有中文。

    这条替代了「把标签表挨着映射表放」那种靠肉眼的 adjacency：标签表因 `trading.py` 已越过 800 行
    红线而独立成文件（spec §11.10 的欠账），而失败原因的呈现（卡片第五行 / prompt 兜底行 / WS
    `error` / 端点 502）**全部只读那张表**。漏一个码，用户就会在最需要读懂的那一格看到一串大写英文
    ——上一轮已经为「后端加了新码、前端显示原始英文」栽过一次，这次把它变成一条会红的用例。
    """
    source = (Path(__file__).resolve().parents[1] / "app/okx/trading.py").read_text("utf-8")
    produced: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_map_trade_code"):
            continue
        for literal in ast.walk(node):
            if isinstance(literal, ast.Dict):
                produced.update(v.value for v in literal.values
                                if isinstance(v, ast.Constant) and isinstance(v.value, str))
    assert produced, "AST 没读到任何归一码——`_map_trade_code` 的形状变了，门禁已失效"
    produced |= set(_BACKSTOP_LOCAL_REJECT_CODES)
    for code in sorted(produced):
        assert trade_code_message(code) != code, f"{code} 在 okx/error_text.py 里没有中文标签"


# ==================== B-13~16 · `place_algo_order` 业务码归一 ====================
class _FakePost:
    """`_post` 的可注入替身：只回一个响应体，不发任何请求。"""

    def __init__(self, body: dict):
        self.body = body
        self.paths: list[str] = []
        self.payloads: list[dict] = []

    async def __call__(self, path, payload):
        self.paths.append(path)
        self.payloads.append(payload)
        return self.body


async def test_place_algo_order_hits_the_documented_endpoint(monkeypatch):
    """B-13：路径恒为 `/api/v5/trade/order-algo`，且 payload 原样透传（含幂等键）。"""
    fake = _FakePost({"code": "0", "data": [{"algoId": "6xxxx", "algoClOrdId": "arh1"}]})
    client = OKXTradeClient("ak", "sk", "pp")
    monkeypatch.setattr(client, "_post", fake)
    row = await client.place_algo_order({"instId": "BTC-USDT-SWAP", "algoClOrdId": "arh1"})
    assert fake.paths == ["/api/v5/trade/order-algo"]
    assert fake.payloads[0]["algoClOrdId"] == "arh1"
    assert row["algoId"] == "6xxxx"


async def test_place_algo_order_raises_on_a_top_level_code(monkeypatch):
    """B-14：顶层 `code != "0"` 即抛（与 `place_order` 逐字同构）。"""
    client = OKXTradeClient("ak", "sk", "pp")
    monkeypatch.setattr(client, "_post", _FakePost({"code": "1", "msg": "boom", "data": []}))
    with pytest.raises(OKXPrivateError) as caught:
        await client.place_algo_order({})
    assert caught.value.code == "1"


async def test_place_algo_order_prefers_the_specific_scode(monkeypatch):
    """B-15：逐项位的 `sCode` 比顶层 `code` **更具体**，抛的必须是它（原始码，不归一）。"""
    client = OKXTradeClient("ak", "sk", "pp")
    monkeypatch.setattr(client, "_post", _FakePost(
        {"code": "1", "data": [{"sCode": "51008", "sMsg": "余额不足"}]}))
    with pytest.raises(OKXPrivateError) as caught:
        await client.place_algo_order({})
    assert caught.value.code == "51008"
    assert caught.value.message == "余额不足"


async def test_place_algo_order_accepts_a_zero_scode(monkeypatch):
    """B-16：`sCode == "0"` 是成功（不得被「非空即错」的写法误判）。"""
    client = OKXTradeClient("ak", "sk", "pp")
    monkeypatch.setattr(client, "_post", _FakePost(
        {"code": "0", "data": [{"sCode": "0", "algoId": "6y"}]}))
    assert (await client.place_algo_order({}))["algoId"] == "6y"


# ==================== B-17~22 · `place_backstop_stop` 时序与审计 ====================
class _FakeTradeClient:
    """OKXTradeClient 桩：记录调用过哪些方法（`set_leverage` 被调到即用例红）。"""

    pos_mode = "long_short_mode"
    positions = [{"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "1", "mgnMode": "cross"}]
    raise_on_place: OKXPrivateError | None = None
    calls: list[str] = []
    payloads: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def get_account_config(self):
        type(self).calls.append("get_account_config")
        return {"posMode": self.pos_mode}

    async def get_positions(self):
        type(self).calls.append("get_positions")
        return list(self.positions)

    async def place_algo_order(self, payload):
        type(self).calls.append("place_algo_order")
        type(self).payloads.append(payload)
        if self.raise_on_place:
            raise self.raise_on_place
        return {"algoId": "6xxxx", "algoClOrdId": payload.get("algoClOrdId")}

    async def set_leverage(self, *a, **k):
        type(self).calls.append("set_leverage")
        return {}

    async def aclose(self):
        pass


class _FakeRest:
    async def get_instrument_meta(self, inst_type, inst_id):
        return dict(_RAW_META)


def _patch(monkeypatch, **attrs):
    cls = type("_Fake", (_FakeTradeClient,), {**attrs, "calls": [], "payloads": []})
    monkeypatch.setattr("app.okx.trading.OKXTradeClient", cls)
    monkeypatch.setattr("app.okx.trading.get_rest_client", lambda: _FakeRest())
    return cls


async def _fresh():
    await init_db()
    async with get_sessionmaker()() as session:
        for row in await repo.list_live_order_audit(session, limit=1000):
            await session.delete(row)
        await session.commit()


async def _place(stop: AlgoStopInput) -> dict:
    async with get_sessionmaker()() as session:
        return await place_backstop_stop(session, _CREDS, 7, stop)


async def _audits() -> list:
    async with get_sessionmaker()() as session:
        return await repo.list_live_order_audit(session, limit=50)


async def test_a_successful_repair_is_audited_as_a_conditional_order(monkeypatch):
    """B-17 / [P2-3]：成功 ⇒ `status="accepted"`、`ord_type="conditional"`、
    `ord_id=<algoId>`、`cl_ord_id=<algoClOrdId>`、`sl_trigger_px=<触发价>`（**零迁移**）。"""
    await _fresh()
    _patch(monkeypatch)
    try:
        result = await _place(_stop())
        assert result["ok"] is True and result["algoId"] == "6xxxx"
        row = (await _audits())[0]
        assert (row.status, row.ord_type) == ("accepted", "conditional")
        assert row.ord_id == "6xxxx"
        assert row.cl_ord_id == "arhabc123000300"
        assert row.sl_trigger_px == 61000.0
        assert row.px is None and row.lever is None
    finally:
        await dispose_engine()


async def test_the_repair_never_touches_leverage(monkeypatch):
    """B-18：**不设杠杆**——补挂不改变仓位参数（与 `place_live_order` 的刻意差异之一）。"""
    await _fresh()
    fake = _patch(monkeypatch)
    try:
        await _place(_stop())
        assert "set_leverage" not in fake.calls
        assert fake.calls.index("get_positions") < fake.calls.index("place_algo_order")
    finally:
        await dispose_engine()


async def test_the_size_is_recomputed_against_the_live_position(monkeypatch):
    """B-19b [P0-4]：入参 `sz=1.0`、实时 `abs_pos=0.4` ⇒ **实际下单 0.4**，且不落 rejected 审计。"""
    await _fresh()
    fake = _patch(monkeypatch, positions=[
        {"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "0.4", "mgnMode": "cross"}])
    try:
        result = await _place(_stop(sz=1.0))
        assert result["sz"] == pytest.approx(0.4)
        assert fake.payloads[0]["sz"] == "0.4"
        assert [row.status for row in await _audits()] == ["accepted"]
    finally:
        await dispose_engine()


async def test_a_flat_position_is_refused_before_any_write(monkeypatch):
    """B-20：实时无仓 ⇒ `LIVE_NO_POSITION`，**一个写请求都不发**（无仓即无需兜底）。"""
    await _fresh()
    fake = _patch(monkeypatch, positions=[])
    try:
        with pytest.raises(OKXPrivateError) as caught:
            await _place(_stop())
        assert caught.value.code == "LIVE_NO_POSITION"
        assert "place_algo_order" not in fake.calls
        assert [row.status for row in await _audits()] == ["rejected"]
    finally:
        await dispose_engine()


async def test_an_exchange_rejection_is_audited_as_error(monkeypatch):
    """B-21：交易所拒单 ⇒ `status="error"` + 归一码落审计，异常上抛（绝不假成功）。"""
    await _fresh()
    _patch(monkeypatch, raise_on_place=OKXPrivateError("51008", "insufficient"))
    try:
        with pytest.raises(OKXPrivateError) as caught:
            await _place(_stop())
        assert caught.value.code == "INSUFFICIENT_BALANCE"
        row = (await _audits())[0]
        assert (row.status, row.error_code) == ("error", "INSUFFICIENT_BALANCE")
        assert row.ord_type == "conditional"
    finally:
        await dispose_engine()


async def test_a_local_reject_is_audited_as_rejected(monkeypatch):
    """B-22：本地拦截（尾仓不足 minSz）⇒ `status="rejected"`，且**没有**任何写请求。"""
    await _fresh()
    fake = _patch(monkeypatch, positions=[
        {"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "0.05", "mgnMode": "cross"}])
    try:
        with pytest.raises(OKXPrivateError) as caught:
            await _place(_stop(sz=0.05, self_position_sz=0.05))
        assert caught.value.code == "LIVE_BACKSTOP_REJECTED_LOCAL"
        assert "place_algo_order" not in fake.calls
        assert [row.status for row in await _audits()] == ["rejected"]
    finally:
        await dispose_engine()


async def test_the_payload_carries_the_deterministic_idempotency_key(monkeypatch):
    """幂等键必须原样进 payload——`_post` 的重试与人工按钮落在同一个键上才恒等。"""
    await _fresh()
    fake = _patch(monkeypatch)
    try:
        await _place(_stop(algo_cl_ord_id="arhdeadbe000100"))
        assert fake.payloads[0]["algoClOrdId"] == "arhdeadbe000100"
    finally:
        await dispose_engine()
