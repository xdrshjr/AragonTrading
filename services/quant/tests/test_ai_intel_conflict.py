"""立场矛盾检测与注入端的两遍预算（ai-trade-intel-actionability G-3 / G-18 / G-19 / G-20）。

**离线、零 IO**。轴 C 的两条最重要的性质在这里冻结：

1. **`conflicts=()` ⇒ `build_digest` 逐字节回退**（G-3 的第一件产物，用 `sha256` 比对）；
2. **两遍预算永不超限**（G-19）——预留是上界，故第二遍的总长度恒 ≤ 第一遍的预算上界；
   且**不存在指向未收进 `lines` 的编号的标注**。
"""
from __future__ import annotations

import hashlib
import json

import pytest

from app.ai_auto.intel import conflict as conflict_mod
from app.ai_auto.intel.disclosure import build_digest, format_intel
from app.ai_auto.intel.gate import IntelItem
from app.ai_auto.intel.taxonomy import IntelLimits

NOW = 1_788_110_000.0
LIMITS = IntelLimits(prompt_max_chars=6000)


def _item(item_id: int, *, stance: str = "bullish", category: str = "market",
          tier: str = "ultra", headline: str = "", summary: str = "", **over) -> IntelItem:
    base = {
        "item_id": item_id, "inst_id": "BTC-USDT-SWAP", "tier": tier, "category": category,
        "headline": headline or f"标题 {item_id}",
        "summary": summary or f"这是第 {item_id} 条情报的摘要内容，足够长以通过长度判据。",
        "stance": stance, "impact": "high", "confidence": 0.8,
        "event_ts": int(NOW - 3600), "captured_ts": int(NOW - 3600),
        "expires_ts": int(NOW + 86400), "domain_count": 3,
    }
    return IntelItem(**{**base, **over})


def _pair_items(**over):
    """一对必然成对的条目：同类别、反立场、主题几乎相同、事件时刻同窗。"""
    text = "ETF 资金流向出现明显变化，机构仓位随之调整"
    a = _item(1, stance="bullish", headline=text, summary=f"{text}，连续第 5 日净流入。",
              **over)
    b = _item(2, stance="bearish", headline=text, summary=f"{text}，单日净流出创年内新高。",
              **over)
    return a, b


def _find(items, *, min_overlap: float = 0.35):
    return conflict_mod.find_conflicts(items, min_overlap=min_overlap,
                                       max_pairs=conflict_mod.MAX_CONFLICT_PAIRS, now=NOW)


# ---------- G-18：四条判据，每条各造一个只差它的反例 ----------
def test_a_textbook_pair_is_detected():
    a, b = _pair_items()
    pairs = _find([a, b])
    assert len(pairs) == 1
    assert (pairs[0].a_id, pairs[0].b_id) == (1, 2)
    assert pairs[0].category == "market"
    assert pairs[0].overlap >= 0.35


@pytest.mark.parametrize("break_it", [
    pytest.param(lambda a, b: (a, b.__class__(**{**b.__dict__, "category": "macro"})),
                 id="cross_category"),
    pytest.param(lambda a, b: (a, b.__class__(**{**b.__dict__, "stance": "neutral"})),
                 id="neutral_stance"),
    pytest.param(lambda a, b: (a, b.__class__(**{**b.__dict__,
                                                 "headline": "完全无关的另一件事情",
                                                 "summary": "这条讲的是矿工手续费的季节性波动。"})),
                 id="low_overlap"),
    pytest.param(lambda a, b: (a, b.__class__(**{**b.__dict__,
                                                 "event_ts": int(NOW - 400 * 3600),
                                                 "captured_ts": int(NOW - 400 * 3600)})),
                 id="out_of_window"),
    pytest.param(lambda a, b: (a, b.__class__(**{**b.__dict__, "verdict": "refuted"})),
                 id="refuted_side_auto_resolves"),
    pytest.param(lambda a, b: (a, b.__class__(**{**b.__dict__, "status": "archived"})),
                 id="not_active"),
])
def test_each_single_broken_criterion_removes_the_pair(break_it):
    """四条判据是**合取**：任一不满足即不成对。`refuted` 的一方**自动解除矛盾**。"""
    assert _find(list(break_it(*_pair_items()))) == []


def test_undated_items_use_captured_ts_so_they_neither_always_nor_never_pair():
    """`event_ts == 0` 的条目取 `captured_ts` 作参照（与 `score_item` 同一条口径）。

    直接用 `event_ts` 会让无日期条目与任何东西相距半个世纪从而**永不成对**，而两条无日期
    条目又会被判成「时刻完全相同」——一个方向错、另一个方向也错。
    """
    a, b = _pair_items(event_ts=0)
    assert len(_find([a, b])) == 1
    far = b.__class__(**{**b.__dict__, "event_ts": 0,
                         "captured_ts": int(NOW - 400 * 3600)})
    assert _find([a, far]) == []


def test_pairs_are_capped_and_deterministic():
    items = []
    for index in range(0, 20, 2):
        text = f"话题 {index} 的资金流向出现明显变化引发讨论"
        items.append(_item(index + 1, stance="bullish", headline=text, summary=f"{text}，偏多。"))
        items.append(_item(index + 2, stance="bearish", headline=text, summary=f"{text}，偏空。"))
    pairs = _find(items)
    assert len(pairs) <= conflict_mod.MAX_CONFLICT_PAIRS
    assert pairs == _find(list(items))          # 同一批输入 ⇒ 同一个结果（确定性）


def test_topic_tokens_reuse_the_single_normalizer():
    """`topic_tokens` 复用 `quotes.normalize_for_match`——**不写第二个归一器**。"""
    assert conflict_mod.topic_tokens("ＡＢＣ 升级", "") == conflict_mod.topic_tokens(
        "abc升级", "")
    assert conflict_mod.overlap(frozenset(), frozenset({"ab"})) == 0.0


# ---------- G-3 ①：`conflicts=()` 的逐字节回退 ----------
def _digest_hash(**over) -> str:
    items = [_item(i) for i in range(1, 6)]
    wire = build_digest(items, limits=LIMITS, now=NOW, last_collect_ts=int(NOW - 600),
                        cite=True, **over)
    return hashlib.sha256(
        json.dumps(wire, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def test_build_digest_is_bit_identical_without_conflicts():
    """总闸关掉时调用方传 `()`（纯核心不读 `Settings`）⇒ 产物必须一个字节不差。"""
    assert _digest_hash() == _digest_hash(conflicts=())
    assert _digest_hash() == _digest_hash(eff=None, conflicts=())


def test_a_conflict_free_inventory_never_grows_the_note():
    items = [_item(i) for i in range(1, 4)]
    wire = build_digest(items, limits=LIMITS, now=NOW, last_collect_ts=int(NOW - 600))
    assert "conflictNote" not in wire
    assert "相互矛盾" not in format_intel(wire)


# ---------- G-19 / G-20：两遍预算 + 同层重排 ----------
def test_two_pass_budget_never_exceeds_the_cap_and_marks_only_included_pairs():
    """40 条含 8 对矛盾 + 极小预算：总长度不超限，且没有指向未收进的编号的标注。"""
    items: list[IntelItem] = []
    for index in range(8):
        text = f"议题 {index} 的资金流向出现明显变化引发市场讨论"
        items.append(_item(index * 2 + 1, stance="bullish", headline=text,
                           summary=f"{text}，多头解读。"))
        items.append(_item(index * 2 + 2, stance="bearish", headline=text,
                           summary=f"{text}，空头解读。"))
    items.extend(_item(100 + i) for i in range(24))
    pairs = tuple(_find(items))
    assert pairs
    tight = IntelLimits(prompt_max_chars=520)
    wire = build_digest(items, limits=tight, now=NOW, last_collect_ts=int(NOW - 600),
                        cite=True, conflicts=pairs)
    lines = wire["lines"]
    assert sum(len(line) + 1 for line in lines) <= tight.prompt_max_chars
    assert wire["hidden"] == len([it for it in items if it.status == "active"]) - len(lines)
    tokens = set(wire["refs"])
    for line in lines:
        if "相互矛盾" in line and "[I" in line.split("相互矛盾")[0].rsplit("（与 ", 1)[-1]:
            marked = line.rsplit("（与 [", 1)[1].split("]", 1)[0]
            assert marked in tokens, "标注指向了一个未被收进 lines 的编号"


def test_same_tier_pairs_become_adjacent():
    a, b = _pair_items()
    items = [a, _item(3), _item(4), b]
    pairs = tuple(_find(items))
    wire = build_digest(items, limits=LIMITS, now=NOW, last_collect_ts=int(NOW - 600),
                        cite=True, conflicts=pairs)
    order = [wire["refs"][token] for token in sorted(wire["refs"])]
    assert abs(order.index(1) - order.index(2)) == 1


def test_cross_tier_pairs_are_never_reordered_into_another_tier_block():
    """跨层的矛盾对**不重排**（评审 P1-8）：排序首键是层级，而预算循环是 `break` 不是
    `continue`——把一条长期条目拖进超短块会打乱层分组，还可能让整个循环提前截断。"""
    text = "主网升级进度出现变化影响供给预期"
    long_side = _item(1, tier="long", stance="bullish", headline=text,
                      summary=f"{text}，进度提前。")
    ultra_side = _item(2, tier="ultra", stance="bearish", headline=text,
                       summary=f"{text}，进度推迟。")
    filler = [_item(10 + i, tier="long") for i in range(3)]
    items = [long_side, *filler, ultra_side]
    pairs = tuple(_find(items))
    assert len(pairs) == 1
    wire = build_digest(items, limits=LIMITS, now=NOW, last_collect_ts=int(NOW - 600),
                        cite=True, conflicts=pairs)
    order = [wire["refs"][token] for token in sorted(wire["refs"])]
    # 长期条目仍全部排在超短条目之前——一条 long 不会因为与某条 ultra 矛盾而被移进 ultra 块。
    assert order.index(2) == len(order) - 1
    assert abs(order.index(1) - order.index(2)) > 1
    # 但两端都被收进时**照样各自带标注**（不重排 ≠ 不标注）。
    assert sum("相互矛盾" in line for line in wire["lines"]) == 2


def test_the_footer_note_only_appears_when_something_was_actually_marked():
    a, b = _pair_items()
    pairs = tuple(_find([a, b]))
    wire = build_digest([a, b], limits=LIMITS, now=NOW, last_collect_ts=int(NOW - 600),
                        cite=True, conflicts=pairs)
    text = format_intel(wire)
    assert "相互矛盾" in text
    assert "请在推理中明确说明你采信哪一条" in text


def test_marker_falls_back_to_a_fixed_phrase_without_numbering():
    """`cite=False`（无编号）⇒ 标注退化为固定文案，绝不指向一个不存在的编号。"""
    a, b = _pair_items()
    pairs = tuple(_find([a, b]))
    wire = build_digest([a, b], limits=LIMITS, now=NOW, last_collect_ts=int(NOW - 600),
                        cite=False, conflicts=pairs)
    assert all("（与库中另一条同题反向情报冲突）" in line for line in wire["lines"])
    assert "refs" not in wire
