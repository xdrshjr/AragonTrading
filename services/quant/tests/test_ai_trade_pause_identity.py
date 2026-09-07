"""持仓身份三键的「停止丢弃」（ai-trade-pause-resume [P0-1] / G-26 ①）。

`_normalize_position` 此前把 OKX 原始行里的 `posId` / `cTime` / `uTime` **全丢了**，于是续跑
时拿回来的持仓**无法回答「这是不是暂停时那一个」**。本文件用一行真实形状的 OKX 响应做**黄金
对照**：三个新键必须出现，而**既有键逐字节不变**——这是唯一一处会被非 AI 链路（账户面板）读到
的改动，它必须是纯加法。
"""
from __future__ import annotations

from app.okx.accounts import _normalize_position

# 一行真实形状的 OKX `account/positions` 响应（数值为字符串、时间为毫秒串，与线上一致）。
_RAW = {
    "instId": "BTC-USDT-SWAP", "instType": "SWAP", "posSide": "long",
    "pos": "0.6", "avgPx": "61234.5", "last": "61500.1", "upl": "12.44",
    "uplRatio": "0.0203", "lever": "3", "mgnMode": "cross",
    "posId": "1899472349827", "cTime": "1723598880000", "uTime": "1723600000000",
}

# 本改动之前该函数的**全部**产出（黄金对照；任何一键变化即视为破坏非 AI 链路）。
_LEGACY_KEYS = {"instId", "instType", "posSide", "pos", "avgPx", "last", "upl",
                "uplRatio", "lever", "marginMode"}


def test_legacy_keys_are_byte_identical():
    """G-26 ①：既有键的集合与取值**逐字节不变**（账户面板与 live_auto 都读这一份）。"""
    out = _normalize_position(_RAW)
    assert _LEGACY_KEYS <= set(out)
    assert {k: out[k] for k in _LEGACY_KEYS} == {
        "instId": "BTC-USDT-SWAP", "instType": "SWAP", "posSide": "long",
        "pos": 0.6, "avgPx": 61234.5, "last": 61500.1, "upl": 12.44,
        "uplRatio": 0.0203, "lever": 3.0, "marginMode": "cross"}


def test_identity_keys_are_added_and_normalized_to_seconds():
    """三个新键：`posId` 原样、两个时间戳按仓库惯例归一为**秒级** UTC。"""
    out = _normalize_position(_RAW)
    assert out["posId"] == "1899472349827"
    assert out["cTimeS"] == 1_723_598_880
    assert out["uTimeS"] == 1_723_600_000


def test_missing_identity_keys_fall_back_without_raising():
    """老 OKX 响应 / 字段为空 ⇒ 容错为 `""` / `0`（缺席即判 `unverifiable_identity`，绝不猜）。"""
    out = _normalize_position({"instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "1"})
    assert out["posId"] == "" and out["cTimeS"] == 0 and out["uTimeS"] == 0


def test_identity_keys_include_exact_milliseconds():
    """只加三键，不多不少——新增键是有代价的（`response_model` 会静默吞掉，见函数 docstring）。"""
    out = _normalize_position(_RAW)
    assert set(out) - _LEGACY_KEYS == {
        "posId", "cTimeS", "uTimeS", "exchangeCTimeMs", "exchangeUTimeMs"}


def test_account_detail_endpoint_schema_still_drops_them_silently():
    """如实钉住那条**已知**的静默丢弃：`OkxAccountPosition` 没有这三键，故账户面板拿不到。

    这不是 bug，是本轮刻意不动的边界（续跑走 `fetch_live_positions` 直调、不经该端点）。
    写成用例是为了让后人别把它当 bug 修——也别在没有读点的情况下顺手给 schema 加三个字段。
    """
    from app.schemas import OkxAccountPosition

    assert {"posId", "cTimeS", "uTimeS"}.isdisjoint(OkxAccountPosition.model_fields)
