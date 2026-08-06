"""轴 A · 官方费率真源（ai-trade-cost-truth-and-net-pnl §3.1）。

覆盖 G-4 / G-5 / G-17 / G-18：归一的四条分支与两级回落、符号约定、返佣夹紧、TTL 去重、
`enabled=False` 零请求、`retune` 绝不回改已记账，以及记账链与决策链读**同一对**数字。
"""
from __future__ import annotations

import pytest

from app.ai_auto.cost_runtime import CostRuntime
from app.ai_auto.fee_rates import FeeRateCache, resolve_fee_rates, snapshot_from_wire
from app.config import Settings
from app.live_auto.fee_ledger import build_fee_ledger
from app.okx.trading import _normalize_trade_fee

_INST = "BTC-USDT-SWAP"
# 真实墙钟量级：TTL 判据是 `now - last_try`，用小数字会让首次刷新被 TTL 误判为「刚跑过」。
_WALL = 1_754_400_000.0


def _raw(**over) -> dict:
    base = {"level": "Lv1", "ruleType": "normal", "maker": "-0.0002", "taker": "-0.0005",
            "makerU": "-0.00015", "takerU": "-0.0004", "makerUSDC": "", "takerUSDC": "",
            "ts": "1754400000000"}
    base.update(over)
    return base


# ---------- 用例 1：分档字段的四条分支（顺序不可换）----------
def test_usdt_swap_prefers_the_u_tier_fields():
    out = _normalize_trade_fee(_raw(), _INST)
    assert (out["makerBps"], out["takerBps"]) == (1.5, 4.0)
    assert out["level"] == "Lv1" and out["source"] == "exchange" and out["rebate"] is False


def test_usdc_swap_uses_the_usdc_tier_fields():
    out = _normalize_trade_fee(_raw(makerUSDC="-0.0001", takerUSDC="-0.0003"), "BTC-USDC-SWAP")
    assert (out["makerBps"], out["takerBps"]) == (1.0, 3.0)


def test_coin_margined_swap_falls_back_to_the_generic_fields():
    out = _normalize_trade_fee(_raw(), "BTC-USD-SWAP")
    assert (out["makerBps"], out["takerBps"]) == (2.0, 5.0)


def test_empty_tier_field_falls_back_to_the_generic_pair():
    """空串 ⇒ 回落通用字段（§2.3 ① 标 ⚠️ 的那种历史形态，两级回落对两种形态都正确）。"""
    out = _normalize_trade_fee(_raw(makerU="", takerU=""), _INST)
    assert (out["makerBps"], out["takerBps"]) == (2.0, 5.0)


def test_both_levels_empty_yields_none_not_zero():
    """两级皆空 ⇒ 整体不可用（**绝不返回 0** —— 0 bps 会静默放松门禁并把净额扣减抹平）。"""
    assert _normalize_trade_fee(_raw(maker="", taker="", makerU="", takerU=""), _INST) is None
    assert _normalize_trade_fee({}, _INST) is None


# ---------- 用例 2：G-17 返佣一律夹紧到 ≥ 0（记账链与决策链一视同仁）----------
def test_rebate_tier_is_clamped_to_zero_on_both_chains():
    out = _normalize_trade_fee(_raw(makerU="0.00001", takerU="-0.0004"), _INST)
    assert out["makerBps"] == 0.0 and out["makerBpsRaw"] == pytest.approx(-0.1)
    assert out["rebate"] is True

    ledger = build_fee_ledger(Settings(), ct_val=0.01, fee_rates=out)
    runtime = CostRuntime(_INST, Settings(), ledger)
    assert ledger.params.maker_fee_bps == 0.0                 # 记账链
    assert runtime._cost_params().maker_fee_bps == 0.0        # 决策链
    # 负费用会让净额**高于**毛额，这是全 spec 唯一能凭空抬高盈亏的口子——必须堵死。
    assert ledger.leg_fee(sz=1.0, px=30_000.0, ord_type="limit") == 0.0


# ---------- 用例 3：G-4 单一真源（两条链同为交易所值 / 同为配置常数）----------
def test_official_rates_land_on_both_the_ledger_and_the_cost_params():
    wire = _normalize_trade_fee(_raw(), _INST)
    settings = Settings()
    ledger = build_fee_ledger(settings, ct_val=0.01, fee_rates=wire)
    runtime = CostRuntime(_INST, settings, ledger)
    params = runtime._cost_params()
    assert (ledger.params.maker_fee_bps, ledger.params.taker_fee_bps) == (1.5, 4.0)
    assert (params.maker_fee_bps, params.taker_fee_bps) == (1.5, 4.0)
    assert ledger.rate_source == "exchange"


def test_missing_official_rates_fall_back_to_config_on_both_chains():
    settings = Settings()
    ledger = build_fee_ledger(settings, ct_val=0.01, fee_rates=None)
    runtime = CostRuntime(_INST, settings, ledger)
    assert (ledger.params.maker_fee_bps, ledger.params.taker_fee_bps) == (2.0, 5.0)
    assert runtime._cost_params().taker_fee_bps == 5.0
    assert ledger.rate_source == "config"
    assert resolve_fee_rates(settings, None) == (2.0, 5.0, "config")


# ---------- 用例 4：G-5 `retune` 只改此后新腿，绝不回改已记账 ----------
def test_retune_never_rewrites_already_booked_fees():
    ledger = build_fee_ledger(Settings(), ct_val=0.01, fee_rates=None)
    booked = ledger.on_trade(cl_ord_id="c1", side="long", sz=10.0, px=30_000.0,
                             ord_type="market", expected_px=None)
    before_fee, before_session = booked["fee"], ledger.session_fee_usd
    ledger.retune(maker_bps=1.0, taker_bps=2.0, source="exchange")
    assert booked["fee"] == before_fee                    # 已落库那一笔一位不变
    assert ledger.session_fee_usd == before_session       # 累计值不被追溯改写
    assert ledger.params.taker_fee_bps == 2.0             # 此后新腿按新档位
    assert ledger.rate_source == "exchange"


def test_retune_clamps_negative_input_defensively():
    ledger = build_fee_ledger(Settings(), ct_val=0.01)
    ledger.retune(maker_bps=-3.0, taker_bps=-1.0, source="exchange")
    assert (ledger.params.maker_fee_bps, ledger.params.taker_fee_bps) == (0.0, 0.0)


# ---------- 用例 5：缓存的三条不变式 ----------
async def test_disabled_cache_sends_zero_requests():
    calls = {"n": 0}

    async def fetch(_inst_id):
        calls["n"] += 1
        return _normalize_trade_fee(_raw(), _INST)

    cache = FeeRateCache(_INST, ttl_s=3600.0, enabled=False)
    assert await cache.refresh(fetch, 1000.0) is False
    assert calls["n"] == 0 and cache.snapshot() is None


async def test_ttl_dedupes_and_stale_only_marks_never_withdraws():
    calls = {"n": 0}

    async def fetch(_inst_id):
        calls["n"] += 1
        return _normalize_trade_fee(_raw(), _INST)

    cache = FeeRateCache(_INST, ttl_s=100.0, stale_factor=2.0, enabled=True)
    assert await cache.refresh(fetch, 1_000.0) is True
    assert await cache.refresh(fetch, 1_050.0) is False       # 未到 TTL
    assert calls["n"] == 1
    # 陈旧只影响标记，**不撤回数值**：一份一小时前的真实档位仍比配置常数准（与 FundingCache 的差异）。
    assert cache.snapshot() is not None
    assert cache.wire(1_400.0)["stale"] is True
    assert cache.wire(1_050.0)["stale"] is False


async def test_fetch_failure_is_swallowed_and_keeps_the_previous_snapshot():
    async def boom(_inst_id):
        raise RuntimeError("429")

    cache = FeeRateCache(_INST, ttl_s=10.0, enabled=True)
    cache.seed(snapshot_from_wire(_normalize_trade_fee(_raw(), _INST), 0.0), 0.0)
    assert await cache.refresh(boom, 100.0) is False
    assert cache.snapshot().maker_bps == 1.5              # 上一份快照仍可读


# ---------- 用例 6：G-18 无凭证 ⇒ 私有取数一个请求都不发 ----------
async def test_runtime_without_credentials_never_touches_the_private_endpoints():
    runtime = CostRuntime(_INST, Settings(), build_fee_ledger(Settings(), ct_val=0.01))
    assert runtime.fee_rates.enabled is False
    assert await runtime.refresh_fee_rates(10_000.0) is False
    assert await runtime.reconcile_tick(10_000.0, window_start_ts=0, trades=[]) is None


# ---------- 用例 7：§5.3 「已回落配置常数」的一次性告知 ----------
async def test_fee_rate_fallback_is_announced_once_and_carries_the_actual_constants():
    """取不到官方档位时如实告知一次——**不带 code**（P2-5：回落不是错误，只是「这个数是猜的」）。"""
    pushed: list[dict] = []

    async def push(event):
        pushed.append(event)

    runtime = CostRuntime(_INST, Settings(), build_fee_ledger(Settings(), ct_val=0.01),
                          creds=object())

    async def boom(_inst_id):
        raise RuntimeError("permission denied")

    runtime._fetch_fee_rate = boom
    await runtime._maintenance_round(_WALL, 0, [], push)
    await runtime._maintenance_round(_WALL + 10_000.0, 0, [], push)

    notices = [e for e in pushed if "官方费率档位" in (e["data"].get("message") or "")]
    assert len(notices) == 1                       # 会话内至多一次，绝不刷屏
    assert notices[0]["type"] == "status" and "code" not in notices[0]["data"]
    assert "maker 2.0 / taker 5.0 bps" in notices[0]["data"]["message"]


async def test_no_fallback_notice_when_the_official_rates_are_available():
    pushed: list[dict] = []

    async def push(event):
        pushed.append(event)

    runtime = CostRuntime(_INST, Settings(), build_fee_ledger(Settings(), ct_val=0.01),
                          creds=object())

    async def ok(_inst_id):
        return _normalize_trade_fee(_raw(), _INST)

    runtime._fetch_fee_rate = ok
    runtime._fetch_position_history = _no_position_history
    await runtime._maintenance_round(_WALL, 0, [], push)
    assert not [e for e in pushed if "官方费率档位" in (e["data"].get("message") or "")]
    assert runtime.fee_rates.snapshot().maker_bps == 1.5


async def _no_position_history() -> list[dict]:
    return []
