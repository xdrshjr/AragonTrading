"""裁剪函数的**预算属性测试**（G-14）+ W-D 的四条修法（A-1…A-6 / A-9 / A-46）。

一个负责裁剪的函数不守自己的预算、或者把一个统计结论切成半句，都不会报错——`tsc` / `ruff` /
全量用例全绿，只有把渲染结果逐字打出来才看得见。这批用例就是那个「打出来」。

三条被钉死的性质（不变式 J-6）：

1. `len(out) <= max_chars` **恒成立**；
2. 非空输出必含框架句的**首尾两端**——免责句（`v1:I-1`「这不是否决指令」在文本层的唯一落点）
   与正文**同生共死**：留不下它就整段不渲染；
3. 绝不在句子中间截断，也绝不产出「有标题没内容」的空壳。
"""
from __future__ import annotations

import itertools
import random

import pytest

from app.ai_auto.experience import aggregate as agg
from app.ai_auto.experience import taxonomy as tax
from app.ai_auto.experience.disclosure import (
    FRAME_OVERHEAD,
    MIN_ITEM_BUDGET_L2,
    _L2_HEAD,
    _L2_TAIL,
    budget_floor,
    format_counter,
    format_disputed,
    format_l0,
    format_l1,
)
from app.ai_auto.experience.graph import GraphNode
from app.ai_auto.experience.retrieval import RetrievalResult, ScoredLesson, most_conflicting
from app.config import Settings

# 一条满长的统计结论（120 字上限的写入期 schema 硬限）。
_LONG = "BTC-USDT-SWAP 15m · 波动·高波动+方向·做多：30 笔中亏 20 笔，净额均值 -25bps（t=-3.2），成本吃掉毛额 62%。"


def _lesson(text: str = _LONG, *, support: int = 30, t: float = -3.2) -> dict:
    return {"text": text, "support": support, "tStat": t}


def _scored(key: str, *, mean: float, t: float, side: str = "long") -> ScoredLesson:
    node = GraphNode(key=key, label=f"{key} 的陈述。", support=30,
                     signature={tax.AXIS_SIDE: side},
                     stats={"meanNetBps": mean, "tStat": t})
    return ScoredLesson(node=node, match=1.0, evidence=1.0, recency=1.0, scope=1.0, score=1.0)


# ---------------------------------------------------------------- A-6（★ E-6）
def test_a6_l2_budget_floor_is_208_and_the_startup_check_covers_it() -> None:
    """A-6 · `FRAME_OVERHEAD["l2"] == 88`、`budget_floor("l2") == 208`，且启动期校验覆盖它。

    这两个数**必须逐值钉死**：低估它的后果与 `v1:E-7` 逐字同形——`reflection_max_chars` 落在
    (0, 208) 会**通过**启动期校验，却让 L2 永远渲染不出一个字，一个不报错的空转。
    L2 没有 `"1. "` 序号前缀，故它的单条预算是 120 而不是含序号的 124。
    """
    assert FRAME_OVERHEAD["l2"] == 88
    assert MIN_ITEM_BUDGET_L2 == 120
    assert budget_floor("l2") == 208
    # 声明值必须 ≥ 真实渲染开销（宁可多留绝不少留）。
    worst_detail = len("（样本 9999999 笔，t=-1234.5）")
    assert len(_L2_HEAD) + len(_L2_TAIL) + worst_detail + 2 <= FRAME_OVERHEAD["l2"]

    assert Settings(ai_trade_experience_reflection_max_chars=0)      # 0 = 整段关闭，合法
    assert Settings(ai_trade_experience_reflection_max_chars=208)
    assert Settings(ai_trade_experience_reflection_max_chars=240)    # 默认值
    for bad in (1, 100, 207):
        with pytest.raises(ValueError, match="REFLECTION_MAX_CHARS"):
            Settings(ai_trade_experience_reflection_max_chars=bad)


# ---------------------------------------------------------------- A-3 / A-4 / A-5
def test_a3_a4_counter_is_all_or_nothing_and_never_exceeds_its_own_budget() -> None:
    """A-3 / A-4（★ F-3）· 装不下完整一条 ⇒ **空串**；绝不先丢免责句、绝不超自己的预算。"""
    full = format_counter(_lesson(), 400)
    assert full and _L2_HEAD in full and _L2_TAIL in full

    # 装得下就整段渲染、装不下就整段省略——判据是**完整那一条的实际长度**，不是一个拍脑袋的数。
    assert format_counter(_lesson(), len(full)) == full
    for budget in (0, 10, 20, 60, 120, len(full) - 1):
        out = format_counter(_lesson(), budget)
        assert len(out) <= budget, f"预算 {budget} 却返回了 {len(out)} 字——裁剪函数不守自己的预算"
        assert out == "", "装不下完整一条时必须整段省略（免责句与正文同生共死）"


def test_a5_disputed_renders_both_or_nothing() -> None:
    """A-5（★ F-4）· 标题逐字承诺「两条互相矛盾」，就绝不允许只渲染一条。"""
    nodes = {"a": {"text": "甲的陈述。"}, "b": {"text": "乙的陈述。"}}
    pairs = [{"a": "a", "b": "b"}]
    full = format_disputed(pairs, nodes, 400)
    assert "甲的陈述。" in full and "乙的陈述。" in full

    assert format_disputed(pairs, nodes, len(full)) == full
    # 只放得下标题 + 第一条（而放不下第二条）时，必须整段省略。
    tight = format_disputed(pairs, nodes, len(full) - len("\n· 乙的陈述。"))
    assert tight == "", "放不下第二条时不许保留那个写着「两条」的标题"
    assert format_disputed(pairs, nodes, 0) == ""


# ---------------------------------------------------------------- G-14（属性测试）
def test_g14_every_formatter_respects_its_budget_over_1000_random_draws() -> None:
    """**G-14** · 四个渲染函数 × 1000 组随机预算：长度恒不超预算、非空即完整、绝不出半句。"""
    rng = random.Random(20260825)
    items = [{"text": _LONG, "support": 30, "tStat": -3.2},
             {"text": "ETH-USDT-SWAP 1H · 方向·做空：12 笔净额均值 +18bps（t=2.1），胜率 58%。",
              "support": 12, "tStat": 2.1},
             {"text": "全标的 15m · 退出·到期离场：由「到期离场」了结的 41 笔：净额均值 -9bps，平均持仓 2.3 小时。",
              "support": 41, "tStat": -2.4}]
    totals = {"sessions": 38, "events": 2043}
    nodes = {"a": {"text": items[0]["text"]}, "b": {"text": items[1]["text"]}}
    for _ in range(1000):
        budget = rng.randint(0, 4000)
        picked = items[:rng.randint(0, len(items))]
        for out in (format_l0(picked, totals, budget),
                    format_l1(picked, budget),
                    format_counter(items[0] if picked else None, budget),
                    format_disputed([{"a": "a", "b": "b"}] if picked else [], nodes, budget)):
            assert len(out) <= budget, f"预算 {budget} 却返回了 {len(out)} 字"
            if not out:
                continue
            body = [line for line in out.split("\n") if line.strip()]
            assert len(body) >= 2, "非空输出必须同时带上框架句的首尾两端，绝不产出空壳"
            for line in body:
                text = line.split(". ", 1)[-1].lstrip("· ")
                if text in (_L2_HEAD, _L2_TAIL) or text.startswith("【") or "以上为" in text:
                    continue
                assert text.endswith(("。", "）")), f"半句截断：{text!r}"


# ---------------------------------------------------------------- A-1 / A-2（D-1）
def test_a1_a2_close_counter_never_picks_a_profitable_lesson() -> None:
    """A-1 / A-2（★ D-1 / F-1）· 「与本次提议最相冲突的一条」必须真的是反例。

    此前 `close` 分支取 `|meanNetBps|` 最大的那条、**不看符号**——实测会挑中一条 +88bps 的
    盈利经验放在那个标题下交给复核官。而复核官的裁决空间只有 confirm / abort / 缩仓，把一条
    **支持**平仓的证据标注成「最相冲突」，唯一可能的行为后果是否掉一次本该确认的平仓。
    """
    result = RetrievalResult(
        l0=[_scored("lesson:win", mean=88.0, t=3.2)],
        l1=[_scored("lesson:win2", mean=41.0, t=2.6)])
    assert most_conflicting(None, result, "close") is None, (
        "候选池全是盈利经验时唯一正确的产品行为是**什么都不说**，"
        "而不是找一条最响的话来填满那个位置")

    mixed = RetrievalResult(
        l0=[_scored("lesson:win", mean=88.0, t=9.9),
            _scored("lesson:loss", mean=-12.0, t=-3.1)],
        l1=[_scored("lesson:loss2", mean=-30.0, t=-2.0)])
    picked = most_conflicting(None, mixed, "close")
    assert picked is not None and picked.node.key == "lesson:loss", (
        "取的必须是 |t| 最大的**负收益**那条；正收益经验永不入选")


# ---------------------------------------------------------------- A-9（★ E-16）
def test_a9_worst_case_label_is_111_chars_and_buckets_have_at_most_two_free_axes() -> None:
    """**A-9（★ E-16）** · 穷举全部桶形 × 全部合法取值 × 六模板 × 25 字 instId 的实算最坏长度。

    数字必须**逐值钉死**（而不是「小于 120 即可」）：余量只有 9 个字，只有钉死具体值，下一次
    改模板或加一条轴时它才会红。spec v2 估的 110 是在旧 `describe` 上算的——本轮 J-3 给每条轴
    补了轴名前缀，最坏语境短语因此多了 1 个字（见「实施过程发现的方案缺陷」I-3）。
    """
    inst = "A" * 25
    signature = {axis: values[0] for axis, values in tax.AXIS_VALUES.items()}
    signature[tax.AXIS_SYMBOL] = inst
    shapes = sorted({tuple(sorted(combo)) for combo in agg._combos_for(signature)})

    widths: dict[int, int] = {}
    for shape in shapes:
        free = len([a for a in shape if a not in (tax.AXIS_SYMBOL, tax.AXIS_BAR)])
        widths[free] = widths.get(free, 0) + 1
    assert max(widths) == 2, (
        f"一个桶签名里非 symbol/bar 的轴最多 2 条，实测桶形分布 {widths}")

    worst = 0
    for shape in shapes:
        domains = [[inst] if axis == tax.AXIS_SYMBOL else list(tax.AXIS_VALUES.get(axis, ("",)))
                   for axis in shape]
        for values in itertools.product(*domains):
            bucket = agg.Bucket(signature=dict(zip(shape, values)),
                                scope="symbol" if tax.AXIS_SYMBOL in shape else "global")
            for template in agg._TEMPLATES:
                stats = agg.BucketStats(
                    n=9999999, wins=0, winRate=0.9999, meanNetBps=-12345.6789,
                    medianNetBps=-1.0, stdNetBps=1.0, tStat=-1234.5, meanHoldS=123456.0,
                    meanCostBps=-12345.6789, costShare=-1234.5678,
                    meanEdgeErrorBps=-12345.6789, firstTs=1, lastTs=2, symbols=3,
                    template=template)
                worst = max(worst, len(agg.render_label(bucket, stats)))
    assert worst == 111, f"实算最坏 label 长度变成了 {worst}（此前是 111）"
    assert worst <= agg._LABEL_MAX


# ---------------------------------------------------------------- A-46 / G-12c（J-3b）
def test_a46_g12c_axis_scoped_words_and_injective_labels() -> None:
    """**A-46 / G-12** · `label_of` 按 `(轴, 取值)` 取词；`describe` 与 `_prefix` 各自单射。

    `horizon="none"` 此前渲染成「无事件」——一句关于持仓周期的事实被写成一句关于行情事件的
    事实。它不会报错，只会逐字进 prompt（`v1:I-9`）。
    """
    # G-12c：凡在多条轴上都合法的取值，渲染出来的**整条短语**必须两两不同。
    by_value: dict[str, list[str]] = {}
    for axis, values in tax.AXIS_VALUES.items():
        for value in values:
            by_value.setdefault(value, []).append(axis)
    for value, axes in sorted(by_value.items()):
        if len(axes) < 2:
            continue
        rendered = [tax.describe({axis: value}) for axis in axes]
        assert len(set(rendered)) == len(rendered), f"{value} 在 {axes} 上渲染成了同一句话"

    assert tax.label_of(tax.AXIS_HORIZON, "none") != "无事件"
    assert "事件" not in tax.describe({tax.AXIS_HORIZON: "none"}), (
        "一条 horizon 桶的整条 label 里不该出现「事件」二字")

    # G-12a：`describe` 对签名的**非 symbol/bar 部分**单射（它刻意不含标的与周期）。
    rng = random.Random(4242)
    axes = [a for a in tax.AXES if a not in (tax.AXIS_SYMBOL, tax.AXIS_BAR)]
    seen: dict[str, tuple] = {}
    for _ in range(2000):
        picked = rng.sample(axes, rng.randint(1, 3))
        sig = {axis: rng.choice(tax.AXIS_VALUES[axis]) for axis in picked}
        identity = tuple(sorted(sig.items()))
        phrase = tax.describe(sig)
        assert seen.setdefault(phrase, identity) == identity, (
            f"两个不同的签名渲染出了同一句话：{phrase}")

    # G-12b：`_prefix` 对**整个签名**单射（它带上了 symbol 与 bar）。
    assert agg._prefix({tax.AXIS_SYMBOL: "BTC-USDT-SWAP", tax.AXIS_BAR: "15m",
                        tax.AXIS_SIDE: "long"}) != agg._prefix(
        {tax.AXIS_SYMBOL: "ETH-USDT-SWAP", tax.AXIS_BAR: "1H", tax.AXIS_SIDE: "long"})


# ---------------------------------------------------------------- A-43（★ E-9）
def test_a43_degraded_signature_never_raises_and_only_yields_known_axes() -> None:
    """**A-43（★ E-9）** · 只写四条轴的降级事件走完桶枚举**不抛**，且桶只由已知轴构成。

    `_combos_for` 此前在四处做 `signature[axis]` 硬取值，而 W-A 的降级签名「其余轴一个都不写」
    ⇒ `KeyError` ⇒ 被 `materialize_bounded` 的裸 `except Exception` 吞成一行 warning
    ⇒ **每一次物化都失败、图永久停更、日志与用例全绿**。
    """
    degraded = {tax.AXIS_SYMBOL: "BTC-USDT-SWAP", tax.AXIS_BAR: "15m",
                tax.AXIS_SIDE: "long", tax.AXIS_HORIZON: "intraday_swing"}
    combos = agg._combos_for(degraded)
    assert combos, "降级事件必须照常落进由已知轴构成的更粗的桶"
    for combo in combos:
        assert set(combo).issubset(set(degraded)), f"桶里出现了事件根本没有的轴：{combo}"
        assert tax.UNKNOWN not in combo.values()

    # J-4 的另一半：取值为 `unknown` 的轴同样不进桶身份（symbol / bar 除外）。
    noisy = {**degraded, tax.AXIS_REGIME: tax.UNKNOWN, tax.AXIS_COST_REGIME: tax.UNKNOWN}
    for combo in agg._combos_for(noisy):
        assert tax.AXIS_REGIME not in combo and tax.AXIS_COST_REGIME not in combo

    # 标的 / 周期缺失是**数据缺陷**（由 harvestGap 覆盖），不是「未知语境」⇒ 整条不落桶。
    assert agg._combos_for({tax.AXIS_SIDE: "long"}) == []
