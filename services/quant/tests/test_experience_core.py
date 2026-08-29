"""经验层纯核心单测（taxonomy / events / aggregate / promote / retrieval / disclosure）。

覆盖 §9.2 的 A-1 / A-3 / A-4 / A-6 / A-7 / A-8 / A-9 / A-10 / A-29 / A-32 / A-36 / A-39 / A-42。
全部离线、零 IO、零 LLM。

这批用例的存在理由写在 §九 的开头：**没有测试的 2680 行纯核心在评审面前等于零**——它们此前
一条用例都没有，而其中任何一条判据出错的失败形态都是「日志全绿、一个字都不喂」。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.ai_auto.experience import taxonomy as tax
from app.ai_auto.experience.aggregate import EventView, compute_stats, enumerate_buckets
from app.ai_auto.experience.disclosure import (
    FRAME_OVERHEAD,
    MIN_ITEM_BUDGET,
    SIDE_NOTE_OVERHEAD,
    TIER_REPEAT,
    budget_floor,
    experience_tier,
    format_l0,
    format_l1,
)
from app.ai_auto.experience.events import ContextFacts, EventIdentity, build_event, freeze_open_context
from app.ai_auto.experience.graph import Graph, GraphEdge, GraphNode, MaterializeOptions, materialize
from app.ai_auto.experience.promote import PromoteRule, resolve_tier, roll_sign
from app.ai_auto.experience.retrieval import (
    ContextSeed,
    RetrievalConfig,
    retrieve,
    side_degeneracy,
)
from app.ai_auto.experience.runtime import ExperienceRuntime
from app.ai_auto.experience.rules import ExperienceRule


# ---------------------------------------------------------------- 桩
@dataclass
class _Trip:
    """`SettledTrip` 的最小替身（`build_event` 只读这些字段）。"""

    seq: int = 1
    side: str = "long"
    entry_ts: int = 1_700_000_000
    exit_ts: int = 1_700_003_600
    hold_seconds: float = 3600.0
    fee_known: bool = True
    exit_source: str = "agent"
    realized_net_bps: float | None = -30.0
    realized_gross_bps: float | None = -10.0
    realized_cost_bps: float | None = 20.0
    edge_error_bps: float | None = 5.0
    gross_pnl_usd: float | None = -1.0
    net_pnl_usd: float | None = -3.0
    cost_error_bps: float | None = 1.0
    r_multiple: float | None = -0.5
    decision_confidence: float | None = 0.8
    wake_kind: str | None = "candle_close"


class _Settings:
    """`Settings` 的最小替身（只带经验层读到的字段）。"""

    ai_trade_net_pnl_enabled = True
    ai_trade_experience_seed_expand_alpha = 0.5
    ai_trade_experience_co_edge_min_weight = 0.15
    ai_trade_experience_max_expanded_contexts = 24
    ai_trade_experience_candidate_cap = 200
    ai_trade_experience_mmr_lambda = 0.5
    ai_trade_experience_half_life_days = 30.0
    ai_trade_experience_l0_max = 3
    ai_trade_experience_l1_max = 3
    ai_trade_experience_l0_max_chars = 480
    ai_trade_experience_l1_max_chars = 660
    ai_trade_experience_reflection_max_chars = 240
    ai_trade_experience_manual_seed_weight = 0.35
    ai_trade_experience_pinned_evidence_floor = 0.60
    ai_trade_experience_min_samples = 8
    ai_trade_experience_iron_samples = 20
    ai_trade_experience_t_threshold = 2.0
    ai_trade_experience_iron_t_threshold = 2.5
    ai_trade_experience_stale_days = 90.0
    ai_trade_experience_global_min_symbols = 3
    ai_trade_experience_cost_regime_min_samples = 30
    ai_trade_experience_graph_node_cap = 4000
    # directional-symmetry 轴 E-2：`side` 轴方向退化时整轴退出评分（`retrieve` 的 keyword-only
    # 入参由 runtime 从这里取）。替身默认**开**，与生产默认一致。
    ai_trade_directional_symmetry_enabled = True


def _facts(regime: str = "range") -> ContextFacts:
    return ContextFacts(inst_id="BTC-USDT-SWAP", bar="15m", side="long",
                        market_context={"primary": regime, "volatility": "high_volatility",
                                        "event": "breakout"},
                        wake_kind="candle_close", horizon_profile_id="intraday_swing",
                        confidence=0.8)


def _runtime(**kwargs) -> ExperienceRuntime:
    rule = ExperienceRule(enabled=True, scope="symbol", write=True)
    return ExperienceRuntime(None, rule, _Settings(),
                             EventIdentity(group_id="g1", run_id=1,
                                           inst_id="BTC-USDT-SWAP", bar="15m"), **kwargs)


# ---------------------------------------------------------------- A-1 / A-29
def test_a1_context_is_frozen_at_open_not_at_close():
    """A-1 · 语境取自**开仓**瞬刻——出场时体制已变也不改写。

    用出场语境标注一笔入场决策的教训是错的，而且**不会报错**：它只会让「高波动追突破亏钱」
    这条经验挂到「震荡」那一格上，然后被当成事实喂给下一次决策。
    """
    opened = freeze_open_context(_facts("range"), seq=1, now_wall=1_700_000_000)
    event = build_event(opened, _Trip(), {"exitSource": "agent", "wallTs": 1_700_003_600,
                                          "_costBasis": "net"},
                        EventIdentity(group_id="g", run_id=1, inst_id="BTC-USDT-SWAP", bar="15m"))
    assert event is not None
    assert event.context[tax.AXIS_REGIME] == "range"          # 出场时是 trend_up 也不改
    assert event.context[tax.AXIS_EXIT] == "agent"            # 退出来源在平仓时才补齐


def test_a29_note_close_reads_the_trip_body_not_just_meta():
    """A-29 · 三个 `trip_meta_wire` **不发**的字段必须拿得到（评审 P0-3）。

    `fee_known` 是 I-7 口径闸的唯一依据、`side` 与 `entry_ts` 是事件表的列、`edge_error_bps` 是
    「估计偏差型」模板的输入。只读 `explain.meta` 的实现会让这三处**静默**失效且全程零报错。
    """
    opened = freeze_open_context(_facts(), seq=3, now_wall=1_700_000_000)
    trip = _Trip(seq=3, fee_known=False)
    event = build_event(opened, trip, {"exitSource": "profitGuard", "wallTs": 1,
                                       "_costBasis": "gross"},
                        EventIdentity(group_id="g", run_id=1, inst_id="BTC-USDT-SWAP", bar="15m"))
    assert event is not None
    assert event.fee_known is False
    assert event.side == "long"
    assert event.entry_ts == trip.entry_ts
    assert event.edge_error_bps == 5.0
    assert event.cost_basis == "gross"          # 缺 net 声明即按 gross，绝不把毛额当净额


# ---------------------------------------------------------------- A-36 / A-42
def test_a36_ambiguous_seq_match_is_refused_not_guessed():
    """A-36（★ D-4 / 评审 F-4）· 两个同 `seq` 不同 `side` 的槽 ⇒ **不落事件**，改计 `harvestGap`。

    退化匹配「取第一个」会默默用另一个 epoch 的语境标注这一笔往返——事件表里多一条语境错误的
    样本，比少一条更坏。宁可缺一条并说明。
    """
    runtime = _runtime()
    runtime.note_open({"side": "long"}, seq=7, facts=_facts())
    runtime.note_open({"side": "short"}, seq=7, facts=_facts())
    runtime.note_close({"side": "close"}, {"exitSource": "agent"}, _Trip(seq=7, side="buy"))
    assert runtime.events_harvested == 0
    assert runtime.harvest_gap == 1


def test_a42_open_slots_are_bounded_and_the_eviction_is_counted():
    """A-42（★ E-13 / 评审 P1-7）· `_open_slots` 有界，且**每一次淘汰都被计数**。

    本仓库对每个会话内缓冲都有成文的有界纪律（`_CAPACITY = 200` / capped trade buffers）。
    被看见的丢弃，而不是一个无声增长的字典。
    """
    runtime = _runtime()
    for seq in range(100):
        runtime.note_open({"side": "long"}, seq=seq, facts=_facts())
    assert len(runtime._open_slots) == 64
    assert runtime.harvest_gap == 36


def test_trip_none_never_invents_an_event():
    """A-30 的纯核心一半：无开仓断面 ⇒ 不落事件、计 `harvestGap`（绝不编一条读数是猜的事件）。"""
    runtime = _runtime()
    runtime.note_close({"side": "close"}, {}, None)
    assert runtime.events_harvested == 0
    assert runtime.harvest_gap == 1


async def test_persist_failure_is_counted_as_a_harvest_gap_not_swallowed():
    """§十二 P7① · 「落盘失败」这处 `except` 的**可见性落点**是 `harvestGap`。

    经验沉淀是 best-effort，失败绝不允许阻断真实资金的成交落库——这一点不变。但事件已经从
    `_pending` 里取走、`events_harvested` 也早在 `note_close` 就加过了：不补这一笔，运行中的
    会话卡会说「已沉淀 7 条」而库里只有 5 条，**差额没有任何地方看得见**。

    记进 `harvest_gap` 的语义恰好对：这张表因此偏乐观了 k 笔——与续跑接管 / 暂停期离线平仓
    那两条采集缺口是同一个口径，也共用同一处渲染落点。
    """
    import app.ai_auto.experience.runtime as runtime_mod

    runtime = _runtime()
    runtime.note_open({"side": "long"}, seq=1, facts=_facts())
    runtime.note_close({"side": "close"}, {"exitSource": "agent", "wallTs": 1},
                       _Trip(seq=1))
    assert runtime.events_harvested == 1

    def _explode():
        raise RuntimeError("DB is gone")

    original = runtime_mod.get_sessionmaker
    runtime_mod.get_sessionmaker = _explode
    try:
        await runtime.persist_pending()          # best-effort：**绝不抛**
    finally:
        runtime_mod.get_sessionmaker = original
    assert runtime.harvest_gap == 1, "落盘失败没有被计进采集缺口 ⇒ 差额无人可见"
    assert runtime.take_dirty() is False, "落盘失败不该把脏标志置真（没有新事件真的进库）"


# ---------------------------------------------------------------- A-3 / A-33
def _views(count: int, *, net: float, fee_known: bool = True, basis: str = "net",
           regime: str = "range", symbol: str = "BTC-USDT-SWAP") -> list[EventView]:
    return [EventView(
        inst_id=symbol, bar="15m",
        context={"symbol": symbol, "bar": "15m", "regime": regime,
                 "volatility": "high_volatility", "event": "breakout", "side": "long",
                 "wake": "candle_close", "exit": "agent", "horizon": "intraday_swing",
                 "confidence": "high"},
        cost_basis=basis, fee_known=fee_known,
        net_bps=net + (i % 3) * 0.5, gross_bps=net + 10, cost_bps=10.0,
        edge_error_bps=1.0, hold_seconds=3600.0,
        exit_wall_ts=1_700_000_000 + i * 3600, seq=i, group_id="g1") for i in range(count)]


def test_a3_gross_or_unknown_fee_events_are_excluded_and_counted():
    """A-3 · `cost_basis != net` 或 `fee_known == False` 的事件不进 bps 统计，`excluded` 如实计数。

    口径一旦混用，整张表会系统性偏移一个往返成本——而它**不会报错**。
    """
    events = _views(10, net=-30.0) + _views(4, net=-30.0, fee_known=False)
    buckets = enumerate_buckets(events, {})
    stats = [compute_stats(bucket, events, {}) for bucket in buckets.values()]
    assert stats, "枚举不出任何桶——聚合层坏了"
    # ★ 实施发现 I-3：同一条事件绝不能在同一个桶里被数两遍。`n` 翻倍会让 `t = mean/(std/√n)`
    # 随 √n 增大，于是一条只有 10 个样本的经验拿着「20 笔」晋升成铁律，并把那个假数字逐字
    # 写进 prompt（违反 I-9）。这条断言就是它的机械判据。
    assert all(s.n <= 10 for s in stats), "有桶把同一条事件数了两遍（n 翻倍 ⇒ 假铁律）"
    assert any(s.excluded == 4 for s in stats)


# ---------------------------------------------------------------- A-4 / A-5 / A-28
def test_a4_and_a28_tier_ladder_and_sign_streak():
    """A-4 / A-28 · 样本不足 ⇒ `candidate`；`iron` 需要样本 + |t| + **连续两次同号**。

    第三条门槛（`sign_streak`）没有一个活得过物化的落点时，`iron` 在结构上永远判不成立 ⇒
    L0 恒空 ⇒ 整个特性「装配成功、日志全绿、一个字都不喂」。
    """
    rule = PromoteRule()
    assert resolve_tier({"n": 3, "tStat": -9.0}, rule) == "candidate"
    assert resolve_tier({"n": 25, "tStat": -3.0}, rule, sign_streak=1) == "normal"
    assert resolve_tier({"n": 25, "tStat": -3.0}, rule, sign_streak=2) == "iron"
    # 叙事 / 手写经验**永远**不能自动进 iron（不变式 I-4）；置顶是唯一路径。
    assert resolve_tier({"n": 99, "tStat": -9.0}, rule, source="narrative", sign_streak=9) == "normal"
    assert resolve_tier({"n": 0, "tStat": 0.0}, rule, source="manual", pinned=True) == "iron"


def test_sign_roll_flip_is_detected_and_streak_restarts():
    """A-5 的纯核心一半：符号翻转被识别（降档 + 版本链由物化层据它执行）。"""
    def _tuple(roll):
        return (roll.sign, roll.streak, roll.flipped)

    assert _tuple(roll_sign(-1, 3, -5.0)) == (-1, 4, False)
    assert _tuple(roll_sign(-1, 3, +5.0)) == (1, 1, True)
    assert roll_sign(0, 0, +5.0).flipped is False           # 首次物化不算翻转
    assert _tuple(roll_sign(-1, 3, None)) == (-1, 3, False)  # 样本不足不清零历史


# ---------------------------------------------------------------- A-6 / A-7 / A-8
def _graph_with(nodes: list[GraphNode], edges: list[GraphEdge]) -> Graph:
    return Graph(nodes, edges, as_of_ts=1_700_000_000)


def _lesson(key: str, *, signature: dict, weight: float = 0.8, tier: str = "iron",
            source: str = "stat", pinned: bool = False, mean: float = -30.0) -> GraphNode:
    return GraphNode(key=key, kind="lesson", label=f"教训 {key}", source=source, tier=tier,
                     pinned=pinned, support=25, weight=weight,
                     stats={"tStat": -3.0, "meanNetBps": mean, "n": 25},
                     signature=signature, last_seen_ts=1_700_000_000)


def _applies(lesson: GraphNode, keys: list[str]) -> list[GraphEdge]:
    return [GraphEdge(lesson.key, key, "applies_in", weight=1.0, support=25) for key in keys]


def test_a6_retrieval_is_deterministic():
    """A-6 · 同图同种子跑 100 次，结果序列完全一致。

    一个每次给出不同经验的 Agent，用户没有任何办法判断它到底在依据什么下单。
    """
    lessons = [_lesson(f"lesson:{i}", signature={"symbol": "BTC-USDT-SWAP", "regime": "range"},
                       weight=0.8) for i in range(6)]
    contexts = [GraphNode(key="symbol:BTC-USDT-SWAP", kind="context", axis="symbol",
                          value="BTC-USDT-SWAP", weight=1.0),
                GraphNode(key="regime:range", kind="context", axis="regime", value="range",
                          weight=1.0)]
    edges = [e for lesson in lessons
             for e in _applies(lesson, ["symbol:BTC-USDT-SWAP", "regime:range"])]
    graph = _graph_with(lessons + contexts, edges)
    seed = ContextSeed(axes={"symbol": "BTC-USDT-SWAP", "regime": "range"},
                       now_wall=1_700_000_000)
    cfg = RetrievalConfig.from_settings(_Settings())
    first = retrieve(graph, seed, cfg).ids()
    assert first, "检索不出任何东西——用例本身坏了"
    assert all(retrieve(graph, seed, cfg).ids() == first for _ in range(100))


def test_a8_cross_symbol_contamination_is_hard_gated():
    """A-8 · 别的标的的 symbol 域经验在本标的种子下 `score == 0`（R-4 的硬闸）。

    「PEPE 1m 的教训用在 BTC 1H」不会报错，只会用别的标的的历史去开真实仓位。
    """
    foreign = _lesson("lesson:pepe", signature={"symbol": "PEPE-USDT-SWAP", "regime": "range"})
    contexts = [GraphNode(key="regime:range", kind="context", axis="regime", value="range",
                          weight=1.0),
                GraphNode(key="symbol:PEPE-USDT-SWAP", kind="context", axis="symbol",
                          value="PEPE-USDT-SWAP", weight=1.0)]
    graph = _graph_with([foreign] + contexts,
                        _applies(foreign, ["regime:range", "symbol:PEPE-USDT-SWAP"]))
    result = retrieve(graph, ContextSeed(axes={"symbol": "BTC-USDT-SWAP", "regime": "range"},
                                         now_wall=1_700_000_000),
                      RetrievalConfig.from_settings(_Settings()))
    assert result.l0 == [] and result.l1 == []


def test_a7_co_occurrence_expansion_reaches_neighbouring_contexts():
    """A-7 · 共现扩散生效：邻接语境下的经验以**衰减权重**浮现，而不是一无所获。"""
    lesson = _lesson("lesson:hv", signature={"symbol": "BTC-USDT-SWAP",
                                             "volatility": "high_volatility"})
    contexts = [
        GraphNode(key="symbol:BTC-USDT-SWAP", kind="context", axis="symbol",
                  value="BTC-USDT-SWAP", weight=1.0),
        GraphNode(key="volatility:high_volatility", kind="context", axis="volatility",
                  value="high_volatility", weight=1.0),
        GraphNode(key="volatility:normal", kind="context", axis="volatility", value="normal",
                  weight=1.0),
    ]
    edges = _applies(lesson, ["symbol:BTC-USDT-SWAP", "volatility:high_volatility"]) + [
        GraphEdge("volatility:normal", "volatility:high_volatility", "co_occurs", weight=0.8),
        GraphEdge("volatility:high_volatility", "volatility:normal", "co_occurs", weight=0.8),
    ]
    graph = _graph_with([lesson] + contexts, edges)
    cfg = RetrievalConfig.from_settings(_Settings())
    direct = retrieve(graph, ContextSeed(axes={"symbol": "BTC-USDT-SWAP",
                                               "volatility": "high_volatility"},
                                         now_wall=1_700_000_000), cfg)
    spread = retrieve(graph, ContextSeed(axes={"symbol": "BTC-USDT-SWAP",
                                               "volatility": "normal"},
                                         now_wall=1_700_000_000), cfg)
    assert direct.l0 and spread.l0
    assert spread.l0[0].score < direct.l0[0].score      # 衰减，不是等权


def test_a32_recency_decays_with_wall_clock():
    """A-32 · `recency` 真的会衰减（`last_seen_ts` 是**最近一条支撑事件**的墙钟，不是物化时刻）。

    两者若被合成一个字段，每次重建都会把时钟拨回 now ⇒ 衰减恒为 1 ⇒ 三年前的教训与昨天同权，
    **且不会报错**。
    """
    lesson = _lesson("lesson:old", signature={"symbol": "BTC-USDT-SWAP"})
    contexts = [GraphNode(key="symbol:BTC-USDT-SWAP", kind="context", axis="symbol",
                          value="BTC-USDT-SWAP", weight=1.0)]
    graph = _graph_with([lesson] + contexts, _applies(lesson, ["symbol:BTC-USDT-SWAP"]))
    cfg = RetrievalConfig.from_settings(_Settings())
    fresh = retrieve(graph, ContextSeed(axes={"symbol": "BTC-USDT-SWAP"},
                                        now_wall=1_700_000_000), cfg)
    year_later = retrieve(graph, ContextSeed(axes={"symbol": "BTC-USDT-SWAP"},
                                             now_wall=1_700_000_000 + 365 * 86400), cfg)
    assert fresh.l0[0].recency == 1.0
    assert year_later.l0[0].recency < 0.01


def test_e2_pinned_manual_lesson_is_retrievable():
    """★ E-2（评审 P0-2）· 一条 `weight == 0` 的手工经验被置顶后**必须**进 L0。

    这是 I-4 白纸黑字承诺的「升 `iron` 只有一条路径——用户显式置顶」在代码里的唯一落点。
    没有证据下限，它的 `score` 恒为 0，会在 `retrieve` 的 `score > 0` 那一行被剔除，
    而 §7.1 那句「置顶的经验不受证据门槛约束」就是假的。
    """
    manual = GraphNode(key="lesson:m1", kind="lesson", label="别在收线前 30 秒追突破",
                       source="manual", tier="candidate", support=0, weight=0.0,
                       signature={"symbol": "BTC-USDT-SWAP"}, last_seen_ts=1_700_000_000)
    contexts = [GraphNode(key="symbol:BTC-USDT-SWAP", kind="context", axis="symbol",
                          value="BTC-USDT-SWAP", weight=1.0)]
    edges = [GraphEdge("lesson:m1", "symbol:BTC-USDT-SWAP", "applies_in", weight=1.0)]
    cfg = RetrievalConfig.from_settings(_Settings())
    seed = ContextSeed(axes={"symbol": "BTC-USDT-SWAP"}, now_wall=1_700_000_000)

    unpinned = retrieve(_graph_with([manual] + contexts, edges), seed, cfg)
    assert [item.node.key for item in unpinned.l0] == []      # candidate 不进 prompt

    manual.pinned = True
    pinned = retrieve(_graph_with([manual] + contexts, edges), seed, cfg)
    assert [item.node.key for item in pinned.l0] == ["lesson:m1"]


def test_e2_materialize_attaches_non_stat_lessons_to_the_graph():
    """★ E-2 的另一半：物化必须为非 `stat` 经验**建出 `applies_in` 边**。

    `replace_edges` 会先全清再插入本计划里的边，任何在别处插的边都会被下一次物化抹掉——
    所以这件事只能在物化这一步做。
    """
    prior = {"lesson:m1": GraphNode(key="lesson:m1", kind="lesson", source="manual",
                                    label="手写", signature={"symbol": "BTC-USDT-SWAP"},
                                    weight=0.0)}
    plan = materialize(_views(12, net=-30.0), MaterializeOptions(rule=PromoteRule()), prior)
    seeded = [edge for edge in plan.edges
              if edge.src_key == "lesson:m1" and edge.rel == "applies_in"]
    assert seeded, "手工经验没有被接进图 ⇒ `_collect` 永远收不到它（评审 P0-2）"
    assert any(node.key == "lesson:m1" and node.weight > 0 for node in plan.nodes)


# ---------------------------------------------------------------- A-9 / A-10 / A-39
def test_a9_char_budget_is_a_hard_ceiling_including_the_frame():
    """A-9 · 整段（页眉 + 条目 + 页脚）≤ `max_chars`，条数 ≤ 上限，**绝不在句中截断**。"""
    items = [{"text": "长" * 120, "support": 20, "tStat": -3.0} for _ in range(500)]
    totals = {"lessons": 148, "events": 2043, "sessions": 37}
    text = format_l0(items, totals, 480)
    assert len(text) <= 480
    assert text.count("\n") <= 4                      # 页眉 + ≤3 条 + 页脚
    assert "长" * 120 in text                          # 整条渲染，没有被切一半


def test_a9_first_item_does_not_fit_means_whole_layer_is_silent():
    """A-9 后半 · 第一条装不下 ⇒ **整段一个字都不渲染**（不是只输出页眉页脚的空壳）。"""
    items = [{"text": "长" * 120, "support": 20, "tStat": -3.0}]
    assert format_l0(items, {"lessons": 1, "events": 1, "sessions": 1}, 200) == ""


def test_a39_frame_overhead_constant_matches_the_real_rendering():
    """A-39（★ E-7 / 评审 P1-1）· 常量必须 ≥ 真实框架长度，且下限值真的装得下一条满长经验。

    v3 声明的 `{"l0": 70}` 会让 `l0_max_chars ∈ [194, 201]` **通过**启动期校验却整层渲染不出
    任何东西——正是 I-6 规则② 声称「拒绝启动、不静默降级」要消灭的形态。这个常量随渲染函数
    演化，只能由一条会一起演化的用例钉住，不能靠人肉核对。
    """
    worst = {"lessons": 9_999_999, "events": 9_999_999, "sessions": 9_999_999}
    one = [{"text": "长" * 120, "support": 20, "tStat": -3.0}]
    # 下限值恰好装得下一条满长经验（这就是「常量 ≥ 真实框架」的可观测判据）。
    l0 = format_l0(one, worst, budget_floor("l0"))
    assert l0 and "长" * 120 in l0 and len(l0) <= budget_floor("l0")
    l1 = format_l1(one, budget_floor("l1"))
    assert l1 and "长" * 120 in l1 and len(l1) <= budget_floor("l1")
    assert budget_floor("l0") == FRAME_OVERHEAD["l0"] + MIN_ITEM_BUDGET


# ============ 轴 E-2 · 经验图谱的方向退化（ai-trade-directional-symmetry） ============
#
# 不做空 => 无做空样本 => `side:short` 语境节点在图里根本不存在 => 检索在结构上**只可能**召回
# 做多教训 => 模型读到的经验永远说「做多是有效的」。而「做空侧没有教训」与「做空侧的教训是
# 负面的」在模型看来不可区分——这是一个不会自己走出来的吸收态。本节把它钉成可判定的事实。

def _side_context(value: str, support: int) -> GraphNode:
    return GraphNode(key=tax.canonical_key(tax.AXIS_SIDE, value), kind="context",
                     axis=tax.AXIS_SIDE, value=value, weight=1.0, support=support)


def _side_graph(long_support: int, short_support: int) -> Graph:
    """一条经验 + symbol 语境 + 两个 side 语境（support 为 0 即「这一侧一条样本都没有」）。"""
    lesson = _lesson("lesson:side", signature={"symbol": "BTC-USDT-SWAP",
                                               tax.AXIS_SIDE: "long"})
    symbol = GraphNode(key="symbol:BTC-USDT-SWAP", kind="context", axis="symbol",
                       value="BTC-USDT-SWAP", weight=1.0)
    contexts = [symbol, _side_context("long", long_support), _side_context("short", short_support)]
    edges = _applies(lesson, ["symbol:BTC-USDT-SWAP",
                              tax.canonical_key(tax.AXIS_SIDE, "long")])
    return _graph_with([lesson] + contexts, edges)


def test_side_degeneracy_names_the_side_that_actually_has_samples():
    """恰有一侧有样本 => 返回那一侧；两侧都有 / 两侧都没有 => `None`（**绝不猜**）。"""
    assert side_degeneracy(_side_graph(28, 0)) == "long"
    assert side_degeneracy(_side_graph(0, 12)) == "short"
    assert side_degeneracy(_side_graph(28, 12)) is None
    assert side_degeneracy(_side_graph(0, 0)) is None


def test_side_axis_leaves_scoring_only_when_the_graph_is_degenerate():
    """退化 + 总闸开 => `side` 轴整轴退出评分；其余三种组合**逐字节回退**。

    把 `side` 留在分母里，会让「本轮是做空」这个语境对每一条（清一色做多的）经验都判为不匹配,
    于是覆盖率被一个**结构性缺样本**的轴系统性压低——那正是吸收态的第一条腿。
    """
    seed = ContextSeed(axes={"symbol": "BTC-USDT-SWAP", tax.AXIS_SIDE: "short"},
                       now_wall=1_700_000_000)
    cfg = RetrievalConfig.from_settings(_Settings())
    degenerate = _side_graph(28, 0)
    off = retrieve(degenerate, seed, cfg)
    on = retrieve(degenerate, seed, cfg, direction_debias=True)
    assert off.side_degeneracy is None and on.side_degeneracy == "long"
    assert on.l0 and off.l0
    assert on.l0[0].match > off.l0[0].match          # 缺样本的那一轴不再压低覆盖率
    # 两侧都有样本 => 判据不成立 => 打分与总闸关时逐字相同（只加不减）。
    healthy = _side_graph(28, 12)
    plain = retrieve(healthy, seed, cfg)
    debiased = retrieve(healthy, seed, cfg, direction_debias=True)
    assert debiased.side_degeneracy is None
    assert [item.match for item in debiased.l0] == [item.match for item in plain.l0]


def test_side_note_is_declared_and_fits_the_reserved_frame_overhead():
    """退化声明句进 **L0 页眉**，故它的长度必须真的被 `SIDE_NOTE_OVERHEAD` 预留出来。

    低估它的后果与 `v1:E-7` 逐字同形：`l0_max_chars` 落在旧下限附近的配置会**通过**启动期校验,
    却在经验库真的方向退化的那一天让整层一个字都渲染不出来——一个不报错的空转。
    """
    from app.ai_auto.experience.disclosure import _side_degeneracy_note

    for side in ("long", "short"):
        assert len(_side_degeneracy_note(side)) <= SIDE_NOTE_OVERHEAD
    assert _side_degeneracy_note(None) == ""          # 未退化 => 页眉逐字节回退
    # 三层下限：只有 L0 抬高，且抬高的量恰是预留量。
    assert budget_floor("l0", side_note=True) - budget_floor("l0") == SIDE_NOTE_OVERHEAD
    assert budget_floor("l1", side_note=True) == budget_floor("l1")
    assert budget_floor("l2", side_note=True) == budget_floor("l2")
    # 抬高后的下限恰好仍装得下一条满长经验 + 声明句（A-39 的同款可观测判据）。
    worst = {"lessons": 9_999_999, "events": 9_999_999, "sessions": 9_999_999}
    one = [{"text": "长" * 120, "support": 20, "tStat": -3.0}]
    out = format_l0(one, worst, budget_floor("l0", side_note=True), side_degeneracy="long")
    assert "长" * 120 in out and "不可" in out
    assert len(out) <= budget_floor("l0", side_note=True)


def test_a10_repeat_tier_compresses_the_whole_block_to_one_line():
    """A-10 · id 元组不变 + `contextDisclosure == "repeat"` ⇒ 整段压成一行且 ≤ 40 字。

    这一档位机制是本设计对「防止上下文爆炸」的正面回答：稳态下每轮只留约 22 字。
    """
    from app.ai_auto.experience.disclosure import format_experience

    wire = {"tier": TIER_REPEAT, "l0": [{"text": "x"}, {"text": "y"}, {"text": "z"}],
            "l1": [], "maxChars": {"l0": 480, "l1": 660}, "totals": {}}
    line = format_experience(wire)
    assert "\n" not in line and len(line) <= 40


def test_experience_tier_requires_context_disclosure_from_wake_layer():
    """A-31 的纯核心一半：`contextDisclosure` 缺席 ⇒ 档位恒 `full` ⇒ 整套压缩静默失效。

    这就是「经验层的 inject 必须排在 `self._wake.inject(...)` **之后**」那条顺序约束的判据。
    """
    from app.ai_auto.experience.retrieval import RetrievalResult

    result = RetrievalResult()
    assert experience_tier(result, None, "repeat") == "full"          # 首轮恒 full
    assert experience_tier(result, result.ids(), None) == "brief"     # 键缺席 ⇒ 退到 brief
    assert experience_tier(result, result.ids(), "repeat") == "repeat"


def test_taxonomy_folds_unknown_values_instead_of_dropping_events():
    """不变式 I-5 · 越界取值折成 `unknown`，**绝不丢事件**（丢事件 = 幸存者偏差）。"""
    assert tax.fold(tax.AXIS_REGIME, "心情好") == tax.UNKNOWN
    assert tax.fold(tax.AXIS_REGIME, None) == tax.UNKNOWN
    assert tax.fold(tax.AXIS_SYMBOL, "PEPE-USDT-SWAP") == "PEPE-USDT-SWAP"   # 开放轴原样
    assert tax.is_valid(tax.AXIS_WAKE, "candle_close") is True
    assert tax.is_valid(tax.AXIS_WAKE, "candle") is False    # trigger 的二值枚举不是 wake_kind
    assert not math.isnan(0.0)                               # 占位：确保本模块 import 的 math 被用到


def test_disputed_pairs_reach_the_reflection_officer_even_when_l0_is_empty():
    """§4.8 的 L2 是「最相冲突者**或**争议对」——两条势均力敌的矛盾结论必须真的送到反思轮。

    `_suppress` 把争议对**双双**踢出 L0/L1，所以「本轮候选全是争议对」恰恰是 L0/L1 皆空的一种；
    而 `most_conflicting` 只在 L0∪L1 里找。少了争议对这条回退，设置中心与执行轨迹上那两句
    「它们只会作为『争议』出现在下单前的反思环节」就是产品在说假话——而说假话的代价，
    在一个把「可核对」当卖点的界面上比少一段文字大得多。
    """
    left = _lesson("lesson:a", signature={"symbol": "BTC-USDT-SWAP", "regime": "range"},
                   mean=-30.0)
    right = _lesson("lesson:b", signature={"symbol": "BTC-USDT-SWAP", "regime": "range"},
                    mean=+30.0)
    left.label = "震荡里追突破亏钱"
    right.label = "震荡里追突破赚钱"
    contexts = [GraphNode(key="symbol:BTC-USDT-SWAP", kind="context", axis="symbol",
                          value="BTC-USDT-SWAP", weight=1.0),
                GraphNode(key="regime:range", kind="context", axis="regime", value="range",
                          weight=1.0)]
    edges = _applies(left, ["symbol:BTC-USDT-SWAP", "regime:range"]) \
        + _applies(right, ["symbol:BTC-USDT-SWAP", "regime:range"]) \
        + [GraphEdge(left.key, right.key, "contradicts", weight=1.0, support=25),
           GraphEdge(right.key, left.key, "contradicts", weight=1.0, support=25)]
    graph = _graph_with([left, right] + contexts, edges)

    rule = ExperienceRule(enabled=True, scope="symbol", write=True)
    settings = _Settings()

    class _Snapshot:
        def __init__(self) -> None:
            self.graph = graph
            self.totals = {"lessons": 2, "events": 50, "sessions": 3}
            self.as_of_ts = 1_700_000_000

    runtime = ExperienceRuntime(_Snapshot(), rule, settings,
                                EventIdentity(group_id="g1", run_id=1,
                                              inst_id="BTC-USDT-SWAP", bar="15m"))
    payload: dict = {}
    runtime.inject(payload, seed=ContextSeed(
        axes={"symbol": "BTC-USDT-SWAP", "regime": "range"}, now_wall=1_700_000_000))
    # 两条互相矛盾且 match 相等 ⇒ 双双出局 ⇒ 决策轮一个字都不喂（`experience` 整键缺席）。
    assert "experience" not in payload
    assert runtime.stats()["l0"] == 0 and runtime.stats()["chars"] == 0

    class _Decision:
        action = "open_long"

    reflection: dict = {}
    runtime.inject_reflection(reflection, _Decision())
    counter = reflection.get("experienceCounter")
    assert counter is not None, "争议对没有出现在反思 payload 里 ⇒ 界面上那两句话是假的"
    assert counter["kind"] == "disputed"
    assert left.label in counter["rendered"] and right.label in counter["rendered"]
    assert len(counter["rendered"]) <= settings.ai_trade_experience_reflection_max_chars
