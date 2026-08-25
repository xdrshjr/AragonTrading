"""渐进式披露：三层渲染 + 档位判定 + **硬**字符预算裁剪（§4.8）—— 纯函数、零 IO。

这是本设计对需求点名的「上下文爆炸」的正面回答，而不是绕过去。它值得这份复杂度的理由是一个
非显然的乘数：

- `commit_round` 每轮往 `_context` append **三条**消息，且 `build_decision_prompt(payload)` 在那里
  被**再渲染一次**原样塞进 user 消息；
- `_trim_context` 的帽子 `ai_trade_max_context_messages` 默认 40 **条消息** ≈13 轮；
- 但 `summarize()` 会**整体重置** `_context`，周期是 `context_refresh_rounds`（默认 10 轮）
  ⇒ 稳态下 40 条那顶帽子几乎从不生效，真实驻留 ≈**10 轮**。

于是一段每轮 480 字的经验块在留存窗口里的真实占用是 `480×10 ≈ 4.8 KB`。加上档位机制后，稳态
（语境未变）每轮只留约 22 字 ⇒ 留存窗口占用从约 4.8 KB 降到约 0.7 KB。**7 倍**压缩，不是 19 倍
——这个数必须写对，它是本设计说服自己的唯一依据。

三条硬规则（不变式 I-6）：

1. **框架句计入预算，且先扣后装**：`item_budget = max_chars − len(header) − len(footer)`。
   否则最终输出会超 `max_chars`，I-6 当场破。
2. **零条目 ⇒ 整层一个字都不渲染**（连页眉页脚都不出），绝不产出「有标题没内容」的空壳。
3. **第一条会超预算即停止**：不截句、不跳过它去装后面更短的——跳过会让输出顺序依赖预算余量，
   难以测试。

另有一条必须写明的事实：`build_reflection_prompt` 的第一行是 `evidence = build_decision_prompt(payload)`，
所以反思轮看到的**不是** 240 字，而是「决策轮那份 L0/L1（按当轮档位渲染）+ 这 240 字的反例」。
这是**有意**的——复核官需要看到交易员看到的同一份证据才能复核。它的两个推论：① 反思轮的经验
字符预算是 L0+L1+L2 之和；② 若当轮档位是 `repeat`，复核官看到的 L0 也是那一行压缩形态，
**反例段因此必须自带足够的自解释**，不得写「见上文第 2 条」这类指向可能已被压缩掉的内容的引用。
"""
from __future__ import annotations

from .retrieval import RetrievalResult, ScoredLesson

TIER_FULL = "full"
TIER_BRIEF = "brief"
TIER_REPEAT = "repeat"

# 单条经验的字符上限（120，写入期 schema 硬限）+ 序号与换行（4）。
MIN_ITEM_BUDGET = 124

# L2 反例段**没有** `"1. "` 序号前缀（它只渲染一条），故它的单条预算是 120 而不是 124。
# 差的这 4 个字看着无关紧要，但它正是 `budget_floor("l2")` 落在 208 还是 212 的分界。
MIN_ITEM_BUDGET_L2 = 120

# 各层框架句的**声明开销**（供 `Settings` 的启动期自洽性断言用；渲染时按实际长度扣）。
#
# ★ E-7（评审 P1-1）· 这两个数必须 ≥ 真实渲染长度，宁可多留绝不少留。实算（用例 A-39 逐层钉死）：
#   L0 页眉「【交易经验 · 铁律】（来自 {sessions} 组有成交记录的自动交易的 {events} 笔已结算往返）」
#      = 37 字 + 两个整数的位数，`totals` 各取 7 位数时 = 51；页脚 38 ⇒ 最坏 89。
#   L1 页眉 13 + 页脚 20 = 33。
# v3 声明的 {"l0": 70, "l1": 46} 会让 `l0_max_chars ∈ [194, 201]` **通过**启动期校验，却使第一条
# 120 字铁律装不下 ⇒ 整层一个字都不渲染——正是 I-6 规则② 声称「拒绝启动、不静默降级」要消灭的形态。
# ★ E-6（评审 R-6）· L2 的框架开销**实算**（用例 A-6 逐字钉死 88 / 208 两个数）：
#   head 21 + tail 39 + detail 最坏 26（`support` 7 位 + `t` 带符号 1 位小数）+ 两个换行 2 = 88。
# 少算它的后果与 v1:E-7 逐字同形：`reflection_max_chars ∈ [203, 207]` 会**通过**启动期校验，
# 却让 L2 永远渲染不出一个字——一个不报错的空转。
FRAME_OVERHEAD = {"l0": 96, "l1": 40, "l2": 88}

_L0_FOOTER = "以上为统计结论，非指令；与当前证据冲突时以当前证据为准，并在研判中说明理由。"
_REPEAT_LINE = "【交易经验】同上 {n} 条铁律（未变化）"


def budget_floor(layer: str) -> int:
    """某一层字符预算的**唯一**下限判据（三处共用：启动期校验 / `PUT /settings/experience` / L2）。

    ★ E-7（评审 P1-1）：`_check_experience_budgets` 只在 `Settings` 构造时跑，而
    `PUT /api/settings/experience` **完全绕过**它——用户能从界面上把 L0 配成一个「永远渲染不出
    任何东西」的值且毫无提示。两处必须读同一个函数，绝不各抄一份数字（抄出来的两份必漂移，
    而这里漂移的后果是整层静默不渲染）。

    Args:
        layer: `"l0"` | `"l1"` | `"l2"`。

    Returns:
        该层 `max_chars` 的最小合法值 = 框架句开销 + 单条上限；L0/L1 的单条上限含序号与换行
        （124），L2 无序号故为 120 ⇒ `budget_floor("l2") == 208`（★ E-6）。
    """
    item = MIN_ITEM_BUDGET_L2 if layer == "l2" else MIN_ITEM_BUDGET
    return int(FRAME_OVERHEAD[layer]) + item


def experience_tier(result: RetrievalResult, prev_ids: tuple[str, ...] | None,
                    context_disclosure: str | None) -> str:
    """档位判定（§4.8）。

    Args:
        result: 本轮检索结果。
        prev_ids: 上轮 L0∪L1 的 key 元组；`None` = 会话首轮。
        context_disclosure: 既有的 `payload["contextDisclosure"]`（`full` | `repeat` | 缺席）。
            **它由 `self._wake.inject(...)` 写入，位于 `_build_decision_payload` 的最后一段**——
            经验层的 inject 必须排在它**之后**，否则这个键恒缺席 ⇒ 档位恒 `full` ⇒ 整套压缩静默
            失效，而那正是本需求点名要解决的「上下文爆炸」（评审 P1-3，用例 A-31 钉死）。

    Returns:
        `full` | `brief` | `repeat`。
    """
    ids = result.ids()
    if prev_ids is None or ids != prev_ids:
        return TIER_FULL
    return TIER_REPEAT if context_disclosure == "repeat" else TIER_BRIEF


def build_wire(result: RetrievalResult, *, tier: str, totals: dict,
               max_chars: dict, graph_as_of_ts: int) -> dict:
    """`payload["experience"]` 的 wire（随证据下发 `maxChars`，与 `tacticalContext` 同范式）。

    渲染层因此只吃 payload、不必拿 `Settings`——而且档位判定的结果住在 wire 里，**绝不**在渲染
    函数内部现场重算：`commit_round` 会把 prompt **再渲染一次**塞进 `_context`，两次渲染之间
    任何状态漂移都会让上下文里的那一份与模型真正看到的那一份不一致。
    """
    return {
        "tier": tier,
        "l0": [_item_wire(item) for item in result.l0],
        "l1": [_item_wire(item) for item in result.l1],
        "disputed": [{"a": a, "b": b} for a, b in result.disputed],
        "suppressed": list(result.suppressed),
        "totals": dict(totals),
        "maxChars": dict(max_chars),
        "graphAsOfTs": int(graph_as_of_ts),
    }


def _item_wire(item: ScoredLesson) -> dict:
    node = item.node
    return {"key": node.key, "id": node.id, "text": node.label, "support": node.support,
            "tStat": node.stats.get("tStat"), "source": node.source, "tier": node.tier,
            "pinned": node.pinned, "score": item.score, "match": item.match,
            "recency": item.recency}


def format_experience(wire: dict | None) -> str:
    """决策 prompt 的经验段（L0 + L1，按档位）；键缺席 / 零条目 ⇒ **空串** ⇒ 整段省略。

    这是 `prompts._format_experience` 唯一的实现落点（沿用「长文本渲染下沉到专门模块、
    `prompts.py` 只留 3 行」的既有纪律）。
    """
    if not wire:
        return ""
    tier = wire.get("tier") or TIER_FULL
    l0 = wire.get("l0") or []
    if tier == TIER_REPEAT:
        return _REPEAT_LINE.format(n=len(l0)) if l0 else ""
    budgets = wire.get("maxChars") or {}
    blocks = [format_l0(l0, wire.get("totals") or {}, int(budgets.get("l0") or 0))]
    if tier == TIER_FULL:
        blocks.append(format_l1(wire.get("l1") or [], int(budgets.get("l1") or 0)))
    return "\n".join(block for block in blocks if block)


def format_l0(items: list[dict], totals: dict, max_chars: int) -> str:
    """L0 常驻纲要（≤3 条铁律 + 声明句）；装不下第一条 ⇒ **整段空串**。

    **页眉里的两个整数必须为真**（不变式 I-9）：它们来自 `SessionGraph.totals`，与
    `GET /api/experience/overview` 同一次取数。在喂给下真单的模型的 prompt 里写一个占位的 0，
    比不写这一段更坏——模型会据此给整段经验打折，甚至在研判里复述那个 0。

    **口径也必须写准**（评审 P2-6）：`sessions` 的真实定义是「事件表里 distinct `group_id` 的
    个数」= **产生过至少一笔已结算往返的分组数**，空跑一天没成交的会话不计入。含糊的
    「你过往 N 次自动交易」是「数字是真的、文案是歪的」，按 I-9 的严格口径同样算说假话。
    """
    header = (f"【交易经验 · 铁律】（来自 {int(totals.get('sessions') or 0)} 组有成交记录的自动交易的 "
              f"{int(totals.get('events') or 0)} 笔已结算往返）")
    return _render(items, header=header, footer=_L0_FOOTER, max_chars=max_chars)


def format_l1(items: list[dict], max_chars: int) -> str:
    """L1 语境展开（仅 `full` 轮）；同样的「先扣框架、装不下即整段省略」规则。"""
    header = "【交易经验 · 当前语境】"
    footer = "以上为与本轮语境匹配的统计结论，非指令。"
    return _render(items, header=header, footer=footer, max_chars=max_chars)


def _render(items: list[dict], *, header: str, footer: str, max_chars: int) -> str:
    """预算裁剪的**唯一**实现（三条硬规则见模块 docstring）。"""
    if not items or max_chars <= 0:
        return ""
    item_budget = max_chars - len(header) - len(footer)
    if item_budget < 1:
        return ""
    lines: list[str] = []
    used = 0
    for index, item in enumerate(items, start=1):
        line = f"{index}. {str(item.get('text') or '').strip()}"
        if not line.strip() or used + len(line) + 1 > item_budget:
            break                      # 第一条就超 ⇒ lines 为空 ⇒ 下面整段省略（规则 2/3）
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    return "\n".join([header, *lines, footer])


_L2_HEAD = "【交易经验 · 与本次提议最相冲突的一条】"
_L2_TAIL = "这是统计结论而非否决指令：若当前证据足以推翻它，请在复核理由中指名推翻的依据。"
_L2_DISPUTED_HEAD = "【交易经验 · 存在争议的结论（两条互相矛盾，均未进入纲要）】"


def _detail(lesson: dict) -> str:
    """反例的样本注记（`（样本 N 笔，t=x.x）`）—— `FRAME_OVERHEAD["l2"]` 的第三个加数。"""
    return f"（样本 {int(lesson.get('support') or 0)} 笔，t={_num(lesson.get('tStat'))}）"


def format_counter(lesson: dict | None, max_chars: int) -> str:
    """L2 反例段（反思 prompt 的追加段）；无反例 / 预算为 0 / 装不下完整一条 ⇒ **空串**（★ J-6）。

    **必须自带足够的自解释**：当轮档位可能是 `repeat`，复核官看到的 L0 只是一行压缩形态，
    任何「见上文第 N 条」的引用都会指向已被压缩掉的内容。

    ★ **D-3（F-3）· 此前的超预算分支同时犯三个错**（实测取证见 spec §2.2 F-3）：
      ① 预算 120 时明明还剩 41 字，却先把免责句丢掉——而免责句是 `v1:I-1`「这不是否决指令」
         在文本层的**唯一**落点，留不下它就不该说这段话；
      ② `body[:n]` 会把一个统计结论切成半句（「……30 笔中亏 20 笔，」）；
      ③ 预算 ≤21 时返回长度 22 的字符串——一个负责裁剪的函数**不守自己的预算**，并产出模块
         docstring 第 2 条明令禁止的「有标题没内容的空壳」。
    正解与 `_render` 的三条硬规则完全一致：**装得下就整段渲染，装不下就整段省略**。
    """
    if not lesson or max_chars <= 0:
        return ""
    body = str(lesson.get("text") or "").strip()
    if not body:
        return ""
    text = f"{_L2_HEAD}\n{body}{_detail(lesson)}\n{_L2_TAIL}"
    return text if len(text) <= max_chars else ""


def format_disputed(pairs: list[dict], nodes: dict, max_chars: int) -> str:
    """争议对（两条势均力敌的矛盾经验）送 L2 的渲染；无争议 / 装不下 ⇒ 空串。

    把一个未解决的冲突当成事实讲给交易员是产品在撒谎；讲给风控复核官才是对的。

    ★ **D-3（F-4）**：此前的 `text[:max_chars]` 会把标题保下来、把第二条切掉——标题逐字承诺
    「两条互相矛盾」，正文却只剩一条。**一对争议要么两条都渲染、要么整段省略**；不足以放下
    第二条时不许保留那个写着「两条」的标题（同 J-6 的「宁可整段不渲染也不出半句」）。
    """
    if not pairs or max_chars <= 0:
        return ""
    lines = []
    for pair in pairs[:2]:
        left = (nodes.get(pair.get("a")) or {}).get("text") or ""
        right = (nodes.get(pair.get("b")) or {}).get("text") or ""
        if left and right:
            lines.append(f"· {left}\n· {right}")
    if not lines:
        return ""
    text = "\n".join([_L2_DISPUTED_HEAD, *lines])
    return text if len(text) <= max_chars else ""


def _num(value) -> str:
    return "未知" if value is None else f"{float(value):.1f}"
