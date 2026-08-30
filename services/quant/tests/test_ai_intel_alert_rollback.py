"""三把总闸的逐字节回退（ai-trade-intel-actionability G-1 / G-3 的另外三件产物）。

§3.0 第 2 条的自我约束：**三把总闸任一关闭时，对应链路的产物必须与 `4b9c352` 逐字节相同**。
这里用 `sha256` 比对而不是眼看——一条「大概一样」的断言在下一次改版里什么都拦不住。
"""
from __future__ import annotations

import hashlib
import json
import time

import pytest
from sqlalchemy import delete

from app.ai_auto.intel import prompts as intel_prompts
from app.ai_auto.intel import service, service_curate
from app.ai_auto.intel import taxonomy as tax
from app.ai_auto.wake_reason import WakeFacts, classify_wake
from app.config import Settings
from app.db import repositories as repo
from app.db.models import AiIntelAlert, AiIntelItem, AiIntelRound, AiIntelWatch
from app.db.session import get_sessionmaker, init_db

NOW = int(time.time()) + 3600
INST = "BTC-USDT-SWAP"


def _sha(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


async def _fresh() -> None:
    await init_db()
    async with get_sessionmaker()() as session:
        for table in (AiIntelAlert, AiIntelItem, AiIntelRound, AiIntelWatch):
            await session.execute(delete(table))
        await session.commit()


def _facts(**over) -> WakeFacts:
    base = {"trigger": "interval", "position_side": "flat", "has_resting": False,
            "bar_index": 7, "last_round_bar_index": 7, "px": 30000.0,
            "last_round_px": 30000.0, "atr_px": 100.0, "since_last_round_s": 182.0}
    return WakeFacts(**{**base, **over})


# ---------- G-1：轴 A 关掉 ⇒ 唤醒链路一个字节不多 ----------
def test_twenty_classifications_without_an_alert_carry_no_intel_key():
    """`intel` 缺席即整键不出现（不是 `None`）⇒ payload 与合入前逐字节相同。"""
    baseline = _sha(classify_wake(_facts(), move_atr_mult=0.5, move_pct=0.0005))
    for _ in range(20):
        wire = classify_wake(_facts(), move_atr_mult=0.5, move_pct=0.0005)
        assert "intel" not in wire
        assert "intel_alert" not in wire["alsoTrue"]
        assert _sha(wire) == baseline


@pytest.mark.asyncio
async def test_a_disabled_alert_switch_hands_off_nothing_and_still_writes_the_ledger():
    """G-15（服务端一侧）：总闸关 ⇒ **不发 handoff**，但台账仍写一行 `disabled`。

    「不发」与「没发生」是两件事：后者会让用户在动态页签上什么都看不到，从而无法回答
    「我到底有没有把它关掉」。
    """
    await _fresh()
    deps = service.IntelDeps(settings=Settings(ai_trade_intel_alert_enabled=False),
                             sessionmaker=get_sessionmaker, group_id="grp-1",
                             now=(lambda: float(NOW)))
    async with get_sessionmaker()() as session:
        handoff = await service._persist_alert(deps, session, INST, round_id=1,
                                               candidates=[])
        await session.commit()
    assert handoff is None
    async with get_sessionmaker()() as session:
        rows, _ = await repo.list_intel_alerts(session, inst_id=INST)
    assert rows[0]["fired"] is False
    assert rows[0]["suppressedCode"] == "disabled"
    assert rows[0]["suppressedText"] == "情报告警已在设置中关闭"


# ---------- G-3 ②③④：轴 C 关掉 ⇒ 整理链路的另外三件产物逐字节回退 ----------
def test_the_three_curation_products_are_bit_identical_with_conflicts_off():
    off = tax.IntelLimits(conflict_enabled=False)
    schema = _sha(intel_prompts.build_curate_tool_schema(off))
    system = _sha(intel_prompts.build_curate_system_prompt(INST, off))
    user = _sha(intel_prompts.build_curate_user_prompt("清单"))
    # 再跑一遍：同一批入参 ⇒ 同一个字节序列（纯函数，没有任何隐藏状态）。
    assert schema == _sha(intel_prompts.build_curate_tool_schema(off))
    assert system == _sha(intel_prompts.build_curate_system_prompt(INST, off))
    assert user == _sha(intel_prompts.build_curate_user_prompt("清单", conflicts=""))
    # 打开之后**三者同时**变（缺一即说明有一件产物没跟上条件化）。
    on = tax.IntelLimits(conflict_enabled=True)
    assert _sha(intel_prompts.build_curate_tool_schema(on)) != schema
    assert _sha(intel_prompts.build_curate_system_prompt(INST, on)) != system
    assert _sha(intel_prompts.build_curate_user_prompt("清单", conflicts="【矛盾】")) != user


def test_the_conflict_segment_renderer_returns_an_empty_string_without_pairs():
    assert service_curate.render_conflicts([], []) == ""


# ---------- 轴 B 关掉 ⇒ 注入排序逐字节回退 ----------
def test_limits_from_settings_carries_every_master_switch_into_the_pure_core():
    """三把总闸经 `IntelLimits` 到达纯核心——**纯核心里一个 `getattr(settings, …)` 都没有**。"""
    limits = tax.limits_from_settings(Settings(
        ai_trade_intel_alert_enabled=False, ai_trade_intel_efficacy_enabled=False,
        ai_trade_intel_conflict_enabled=False))
    assert (limits.alert_enabled, limits.efficacy_enabled, limits.conflict_enabled) == (
        False, False, False)
    on = tax.limits_from_settings(Settings())
    assert (on.alert_enabled, on.efficacy_enabled, on.conflict_enabled) == (True, True, True)


def test_no_pure_core_module_ever_reads_settings_directly():
    """§3.0 第 2 条的物理防线：三个新纯核心里不得出现 `getattr(settings` 的字样。"""
    from pathlib import Path

    base = Path(__file__).resolve().parents[1] / "app" / "ai_auto" / "intel"
    for name in ("alert.py", "efficacy.py", "conflict.py"):
        text = (base / name).read_text(encoding="utf-8")
        assert "getattr(settings" not in text, name
        assert "from ...config" not in text and "import Settings" not in text, name


def test_the_three_new_pure_cores_never_import_gate():
    """C-6：`efficacy` / `conflict` / `alert` 都不 import `gate`（否则与它成环）。"""
    from pathlib import Path

    base = Path(__file__).resolve().parents[1] / "app" / "ai_auto" / "intel"
    for name in ("alert.py", "efficacy.py", "conflict.py"):
        text = (base / name).read_text(encoding="utf-8")
        assert "from .gate import" not in text, name
        assert "import gate" not in text, name


def test_the_alert_categories_setting_falls_back_to_every_category_when_blank():
    limits = tax.limits_from_settings(Settings(ai_trade_intel_alert_categories=""))
    assert limits.alert_categories == frozenset(tax.CATEGORIES)


def test_require_quote_degrades_at_the_single_effective_value_read_point():
    """G-6：`quote_mode="off"` ⇒ 生效值降为 False，而**存储的偏好一个字节不改**。"""
    stored = Settings(ai_trade_intel_alert_require_quote=True,
                      ai_trade_intel_quote_mode="off")
    assert stored.ai_trade_intel_alert_require_quote is True      # 存储不被改写
    assert tax.resolve_require_quote(stored) is False
    assert tax.limits_from_settings(stored).alert_require_quote is False
    # 抓取关掉时同样降级：没有正文，引文只能靠 snippet 命中，命中率低到让这条判据把告警
    # **系统性归零**——而它在界面上与「今天真的没有够格的情报」不可区分。
    reader_off = Settings(ai_trade_intel_alert_require_quote=True,
                          ai_trade_intel_reader_enabled=False,
                          ai_trade_intel_quote_mode="soft")
    assert reader_off.ai_trade_intel_alert_require_quote is True   # 存储仍不被改写
    assert tax.resolve_require_quote(reader_off) is False
    back_on = Settings(ai_trade_intel_alert_require_quote=True,
                       ai_trade_intel_quote_mode="soft")
    assert tax.resolve_require_quote(back_on) is True
