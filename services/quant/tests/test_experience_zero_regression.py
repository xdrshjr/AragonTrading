"""门禁 G-2 · 零回归（ai-trade-experience-context-graph 不变式 I-2）。

**这条不绿，后面一行都不许接。**

经验层未装配（运维总闸关 / 启动请求显式关 / 图为空 / 检索零条目）⇒ payload **无** `experience`
键 ⇒ `build_decision_prompt` 与 `build_reflection_prompt` 的输出**逐字节**等于本特性合入前。

「逐字节」在这里不是修辞：本仓库的决策 prompt 有 15 个 `_format_*` 段，每一段都走「取键 → 渲染
→ 缺键即整段省略」范式。任何一处把「缺席」渲染成「无」的实现都会改变喂给模型的输入，而**没有
任何运行期信号**会告诉你它变了。
"""
from __future__ import annotations

from app.ai_auto.decider import TradeDecision
from app.ai_auto.experience.rules import resolve_experience
from app.ai_auto.prompts import build_decision_prompt, build_reflection_prompt


def _payload() -> dict:
    """一份不含 `experience` 键的典型决策 payload（= 本特性合入前的形状）。"""
    return {
        "round": 7, "barTs": 1_700_000_000, "lastPx": 30_000.0, "trigger": "candle",
        "position": None, "sessionPnlUsd": -12.5, "budgetUsd": 1000.0,
        "takeProfitUsd": None, "stopLossUsd": None,
        "marketContext": {"primary": "range", "volatility": "high_volatility"},
        "signalBoard": [{"strategyId": 1, "name": "S", "stance": "flat",
                         "latestSignal": "open_long", "strength": 1.0, "reason": "r"}],
    }


class _Settings:
    ai_trade_experience_enabled = True
    ai_trade_experience_default_on = True
    ai_trade_experience_write_enabled = True


def test_decision_prompt_is_byte_identical_without_the_experience_key():
    """无 `experience` 键 ⇒ 渲染结果与「有键但为 None / 空」完全一致，且不含任何经验字样。"""
    base = build_decision_prompt(_payload())
    for absent in (None, {}, {"tier": "full", "l0": [], "l1": [], "maxChars": {}, "totals": {}}):
        payload = _payload()
        if absent is not None:
            payload["experience"] = absent
        assert build_decision_prompt(payload) == base
    assert "交易经验" not in base


def test_reflection_prompt_is_byte_identical_without_the_counter_key():
    """反思 prompt 同款：无 `experienceCounter` ⇒ 逐字节回退。"""
    decision = TradeDecision(action="open_long", size_fraction=0.5, confidence=0.7, reason="r")
    base = build_reflection_prompt(_payload(), decision)
    for absent in (None, {}, {"rendered": ""}):
        payload = _payload()
        if absent is not None:
            payload["experienceCounter"] = absent
        assert build_reflection_prompt(payload, decision) == base


def test_injected_experience_actually_changes_the_prompt():
    """反向断言：装配之后**必须**真的改变输出。

    没有这一条，上面两条可以靠「渲染函数根本没接上」而全绿——那正是本特性最大的一类失明源。
    """
    payload = _payload()
    payload["experience"] = {
        "tier": "full",
        "l0": [{"key": "lesson:a", "text": "高波动追突破做多净额为负", "support": 23,
                "tStat": -2.8}],
        "l1": [], "disputed": [], "suppressed": [],
        "totals": {"lessons": 148, "events": 2043, "sessions": 37},
        "maxChars": {"l0": 480, "l1": 660}, "graphAsOfTs": 1_700_000_000,
    }
    rendered = build_decision_prompt(payload)
    assert "高波动追突破做多净额为负" in rendered
    assert "37 组有成交记录的自动交易的 2043 笔已结算往返" in rendered, (
        "页眉里的数字必须是库存真值（不变式 I-9）——占位的 0 比不写这一段更坏")
    assert rendered != build_decision_prompt(_payload())


def test_operator_switch_and_explicit_opt_out_both_disarm_the_layer():
    """三层优先级：运维总闸 > 用户显式 > 服务端默认（`rules.resolve_experience` 是唯一解析点）。

    「字段整键缺省」与「显式 `{enabled:false}`」是两件不同的事。把两者合成一个判据，用户就再也
    关不掉这个功能，而界面上那个复选框看起来是生效的——本仓库已在 `baseRules` / `profitGuard` /
    `tradingHorizon` 上踩过三次。
    """
    settings = _Settings()
    assert resolve_experience(None, settings) is not None           # 缺省 ⇒ 按服务端默认装配
    assert resolve_experience({"enabled": False}, settings) is None  # 用户显式关闭
    settings.ai_trade_experience_enabled = False
    assert resolve_experience({"enabled": True}, settings) is None   # 运维总闸优先级最高
