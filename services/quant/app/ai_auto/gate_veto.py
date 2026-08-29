"""门禁否决注记的**纯核心**（ai-trade-directional-symmetry 轴 B）。

## 这一层解决什么

`commit_round` 收的是**门禁之后**的最终 decision（`session.py` 的 R-P1-3 注释明写如此）。于是
模型下一轮读到的自己的历史，是「上轮我决定观望」——而事实是「上轮我提议做空、被同向共识门槛
拦下」。合入本模块之前，八道确定性门禁里**只有持仓周期闸**会把这件事写回上下文
（`horizon_runtime.build_gate_veto_note` 的第一行就把 `vetoedBy` 硬编码成 `"horizon"`）。

这条缺陷本身是方向中性的，但它与共识门禁的单向阀相乘就产生**方向性的记忆偏斜**：做多成功了会
进 journal 与滚动摘要，做空被拦的十次从模型的历史里彻底消失。跑上十几轮，模型自己的记忆就变成
了「我一直在做多，而且没什么想做空的时候」。

## 三条不可动摇的纪律（改本文件前先读）

1. **层名只有一份真源**。`vetoedBy` 一律取 `open_funnel.classify_block(degrade)`，**绝不**在本
   模块另写一张层名表。`session._emit_tool_call_trace` 里那份曾经硬编码的 `"horizon"` 也必须
   改用同一个函数——两处各写一份必然漂移，而漂移的表现（模型与界面读到一个不存在的层名）
   **不报任何错**。
2. **`reflection` 层显式返回 `None`**。反思否决另有 `build_reflection_note` 承载，两条注记同时
   出现会让模型读到重复事实。这是硬约束，不是优化。
3. **第一阶段模型侧的降级码绝不产生注记**。`low_confidence` / `no_tool_call` / `missing_size` /
   `reasoning_truncated` 这些轮次，`_map_decision` 已经把 action 洗成 hold
   （`decider.py` 的 `_hold(...)`），此刻 `stage1_decision.action == "hold"`，写出去的
   `proposedAction` 会**撒谎**——而 `proposedAction` 带着方向正是本轴宣称的全部价值。
   判据是 `gate_degrade != llm_degrade`，**不是**任何形式的对象身份比较：`_apply_sizing` 本就会
   `replace` 出新对象，`decision is not stage1_decision` 在任何开仓轮都恒为真。
"""
from __future__ import annotations

from .open_funnel import LAYER_LABELS, classify_block

# 提议理由的截断长度（与 `_ANALYSIS_EXCERPT_CHARS` 同款「有界摘录」纪律）。
# `horizon_runtime.build_gate_veto_note` 已委托到本模块，故这里是该常量的**唯一**落点：
# 两处各写一个，horizon 轮的注记就不再逐字节等于合入前。
PROPOSED_REASON_CHARS = 200

# 反思层：本层的否决另有 `build_reflection_note` 承载（纪律 2）。
_REFLECTION_LAYER = "reflection"


def build_gate_veto_note(proposed, degrade: str | None, *, llm_degrade: str | None = None,
                         priority=None, consensus_wire: dict | None = None,
                         layer_label: bool = False) -> dict | None:
    """把本轮**任一确定性门禁**的否决压成记忆注记；未被否决 ⇒ `None`。

    Args:
        proposed: 门禁**之前**的决策引用（实盘取 `stage1_decision`）。`None` ⇒ `None`。
        degrade: 执行**之前**的降级码（`gate_degrade`）。`None` ⇒ 本轮没有任何门禁改写过决策。
            ⚠️ **绝不能传 `degrade or executed["closeBlocked"]` 那个合成值**：
            `exit_order_slot_busy` 不在 `_DEGRADE_PREFIXES` 任何前缀里，会经 `classify_block`
            落进 `"other"`，模型会读到一条**凭空捏造**的「我被『其它』层拦下了」。
        llm_degrade: 第一阶段模型侧的降级码（`decide()` 返回的那一个）。与 `degrade` 相等
            ⇒ 本轮没有任何**确定性门禁**参与 ⇒ `None`（纪律 3）。
        priority: 本轮 `ExitPriority`（可为 `None`）——仅 `horizon` 层附加，使该层的注记
            **逐字节等于**本模块合入前。
        consensus_wire: 本轮共识门禁的 `out.wire`（可为 `None`）——仅 `consensus` 层附加，
            让模型知道「还差几票 / 还能不能走逆势通路」。
        layer_label: 方向对称性总闸。`False`（默认）⇒ 注记恒为四键（+ 各层的既有附加键）
            ⇒ 逐字节回退；`True` ⇒ 追加中文 `layerLabel`（有意的行为变更）。

    Returns:
        注记 dict 或 `None`。
    """
    if proposed is None:
        return None
    # 纪律 1：层归属**只经 `veto_layer` 一个判定**——注记与 `tool_call` 相位共用它，两处各写一份
    # 必漂移（纪律 2 的反思层、纪律 3 的模型侧码都在那里一并收口）。
    key = veto_layer(degrade, llm_degrade=llm_degrade)
    if key is None:
        return None
    note = {"vetoedBy": key, "reason": degrade,
            "proposedAction": proposed.action,
            "proposedReason": str(proposed.reason or "")[:PROPOSED_REASON_CHARS]}
    if layer_label:
        note["layerLabel"] = LAYER_LABELS.get(key, key)
    if key == "horizon" and priority is not None:
        note.update({"exitConviction": priority.declared, "exitLevel": priority.level,
                     "downgradeReason": priority.downgrade_reason,
                     "intentStreak": int(priority.wire.get("intentStreak") or 0),
                     "intentLapses": int(priority.wire.get("intentLapses") or 0)})
    if key == "consensus" and consensus_wire:
        note.update({"longVotes": consensus_wire.get("longVotes"),
                     "shortVotes": consensus_wire.get("shortVotes"),
                     "minConsensus": consensus_wire.get("minConsensus"),
                     "contrarianAvailable": consensus_wire.get("contrarianAvailable"),
                     "contrarianRemaining": consensus_wire.get("contrarianRemaining")})
    return note


def veto_layer(degrade: str | None, *, llm_degrade: str | None = None) -> str | None:
    """本轮否决归属的层 key，**`session._emit_tool_call_trace` 与本模块共用的唯一判定**。

    `reflection` 显式返回 `None`（纪律 2：反思否决另有相位承载，混进来会让卡片说「被交易周期层
    降级」而其实不是）。

    ⚠️ `llm_degrade` **两个调用点都必须传**（纪律 3）。少传一处，那一处就会把
    `low_confidence` 这类第一阶段模型侧的码当成一次门禁否决：`classify_block` 会把它归到
    `confidence`（其余 LLM 故障码归 `other`），于是卡片上会出现「模型提议的是『观望』，被
    『置信度门槛』降级」——一条**凭空捏造**的门禁事实，而此刻 `proposedAction` 已是 hold、
    方向早就丢了。生产实测这条路径占开仓意愿的 21%，不是边角料。

    Args:
        degrade: 执行**之前**的降级码（`gate_degrade`）。
        llm_degrade: 第一阶段模型侧的降级码；与 `degrade` 相等 ⇒ 本轮没有任何**确定性门禁**
            参与 ⇒ `None`。
    """
    if degrade is None or (llm_degrade is not None and degrade == llm_degrade):
        return None
    key = classify_block(degrade)
    return None if key == _REFLECTION_LAYER else key


def veto_layer_label(key: str | None) -> str | None:
    """层 key → 中文标签；`None` ⇒ `None`。

    **中文标签必须由后端下发**（`discipline._THRESHOLD_LABELS` 的成文纪律：「数值与中文标签
    全部由后端下发，前端不复制任何一个」）。前端抄一份，下次加层就会显示英文 key。
    """
    return None if key is None else LAYER_LABELS.get(key, key)
