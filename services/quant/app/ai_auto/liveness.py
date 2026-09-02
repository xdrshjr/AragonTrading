"""活性看门狗的**纯核心**（ai-trade-loss-and-liveness-hardening §4.4）。

生产实证：最近一条会话跑满 **456 轮决策、0 笔成交**，模型自己的滚动摘要里写着「合规限价区间持续
为空（feed 故障未修复，已跨约 260 轮）」。根因是【基础策略】MA 通道闸是全链路**唯一**一条
fail-closed 的铁律（通道算不出 ⇒ 所有开仓一律否决），而行情通道在观察窗口里断连数千次——通道
一旦长期算不出，系统就进入一个**不报警、不自愈、不停止、但永远不会下单**的吸收态，同时继续烧
LLM 预算。用户看到的是「运行中」，实际是「已死」。

fail-closed 的极性本身可以辩护（无法验证开单位置就不该开单），**但它必须是响亮的**。

三条不可动摇的纪律（改本文件前先读）：

1. **信号必须与模型说什么无关。** 上一版设计想数「本轮 `degrade` 码连续 N 轮相同」，而 `degrade`
   只在**模型提出了受闸动作**时才非空：实测事故现场 456 轮里只有 12 轮产生过开仓意愿、
   `degradedRounds` 只有 2——其余 444 轮模型看到 `maBand: null` 后自己就写了 `hold`，一道闸都没
   开火。任何数降级码的看门狗在这张数据上**永远数不到 2**。故本模块的四条码全部取自**结构性
   事实**（通道算不算得出来 / 判定价可不可信 / 行情降没降级 / 决策器可不可用），每一轮都成立，
   与 LLM 的输出无关。

2. **四条码必须有唯一产出点、无参数后缀、值域封闭。** 「同码连续」这个判据只有在码本身稳定时
   才成立：`base_rule_ma_band(pctB=0.42)` 这类带参数的码按等值比永不命中、按原样比则每轮换码
   ⇒ `streak` 恒 1。将来若要把带参数的码纳进来，**必须先前缀归一**（`code.split("(")[0]`），
   与前端 `describeDegradeReason` 的 `startsWith` 口径同源。

3. **「有机会但我不想开」在这四条里一条都不落。** 看门狗数的是「结构上不可能开仓」，不是「模型
   选择观望」。`blocking_code is None` ⇒ `streak` 立刻归零。这是它不会误停一段合法的「无机会」
   行情的全部依据。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# ---- 四条结构性阻塞码（**顺序即优先级**，`probe` 自上而下取第一条）----
# 每一条都在后端有唯一产出点，且**不含 `(` 参数后缀**（G-D10 源码扫描门禁守这一条）。
BLOCK_MA_BAND = "ma_band_unavailable"    # payload.baseRules.maBand is None 且铁律已装配
BLOCK_NO_PX = "mark_px_unavailable"      # 判定价不可信（与 hold_horizon._is_bad_px 同一谓词）
BLOCK_FEED = "feed_degraded"             # 会话级 `_stale_degraded` 为真
BLOCK_LLM = "llm_unavailable"            # 本轮 decide 重试耗尽（终态失败）
BLOCKING_CODES: tuple[str, ...] = (BLOCK_MA_BAND, BLOCK_NO_PX, BLOCK_FEED, BLOCK_LLM)

LEVEL_OK = "ok"
LEVEL_DEGRADED = "degraded"
LEVEL_HALT = "halt"


def is_bad_px(value) -> bool:
    """判定价是否不可信（非有限、非正）。

    与 `hold_horizon._is_bad_px` 是**同一条谓词**。之所以在这里再写一遍而不是 import：
    `hold_horizon` 是持仓周期层的纯核心，让活性看门狗依赖它会在两个本该正交的层之间连出一条边，
    而这条边的方向将来只会更难拆。判据只有两行且是数学定义（有限且为正），不存在漂移空间——
    这与「阈值 / 分类表不得抄第二遍」的纪律不冲突（那些会调，这个不会）。
    """
    try:
        px = float(value)
    except (TypeError, ValueError):
        return True
    return not math.isfinite(px) or px <= 0


def probe(*, ma_band_armed: bool, ma_band: dict | None, mark_px,
          stale_feed: bool, llm_failed: bool) -> str | None:
    """本轮**结构上**是否无法开仓；能开 ⇒ `None`。顺序即优先级，自上而下取第一条。

    Args:
        ma_band_armed: 【基础策略】MA 通道铁律是否装配（未装配 ⇒ 通道算不出来也不阻塞开仓）。
        ma_band: 本轮 payload 里的通道断面（`None` = 算不出来，K 线不足 / 行情断流）。
        mark_px: 本轮判定价。
        stale_feed: 会话级行情降级标志。
        llm_failed: 本轮 `decide` 是否重试耗尽后终态失败（≠ 单次尝试失败）。

    Returns:
        `BLOCKING_CODES` 之一，或 `None`。
    """
    if ma_band_armed and ma_band is None:
        return BLOCK_MA_BAND
    if is_bad_px(mark_px):
        return BLOCK_NO_PX
    if stale_feed:
        return BLOCK_FEED
    if llm_failed:
        return BLOCK_LLM
    return None


@dataclass(frozen=True)
class LivenessRule:
    """两档阈值；任一为 `0` ⇒ 该档**永不触发**（沿用本仓库既有的「0 = 关」惯例）。

    Attributes:
        warn_rounds: 连续多少轮同码阻塞后置 `degraded`（面板橙色横幅）。
        halt_rounds: 连续多少轮后**请求整组暂停**（绝不平仓）。
    """

    warn_rounds: int = 20
    halt_rounds: int = 60


@dataclass(frozen=True)
class LivenessState:
    """看门狗状态：**同一个**阻塞码连续了多少轮，从什么时候开始。

    Attributes:
        code: 当前连续的阻塞码；`None` = 本轮能开仓（或刚被成交清零）。
        streak: 连续轮数（含本轮）。
        first_wall: 这一段连续阻塞开始的墙钟秒（横幅上的「起始时间」）。
    """

    code: str | None = None
    streak: int = 0
    first_wall: float = 0.0


def advance(state: LivenessState, *, blocking_code: str | None,
            now_wall: float) -> LivenessState:
    """推进一轮。判据逐条冻结（改任何一条都会改变自动停手的时机）：

    - `blocking_code is None` ⇒ 归零（`LivenessState()`）——**「有机会但我不想开」不算阻塞**；
    - 与上一轮**不是同一个码** ⇒ 重新起算（在两个码之间来回跳动不算长期阻塞）；
    - 同码 ⇒ `streak += 1`，`first_wall` 保持第一次的那个值。

    成交清零由调用方在成交事件上调 `LivenessRuntime.note_fill()` 完成——那是事件级的、比轮级更
    及时，且**任何一次成交都算数**（系统证明了自己还活着）。
    """
    if blocking_code is None:
        return LivenessState()
    if state.code != blocking_code:
        return LivenessState(code=blocking_code, streak=1, first_wall=float(now_wall))
    return LivenessState(code=blocking_code, streak=state.streak + 1,
                         first_wall=state.first_wall or float(now_wall))


def verdict(state: LivenessState, rule: LivenessRule | None) -> tuple[str, str | None]:
    """`(level, code)`——`ok` / `degraded` / `halt`；未装配或未阻塞 ⇒ `("ok", None)`。

    `halt` 先判：两档阈值配反（`halt < warn`）时也必须给出更严重的那一档，而不是卡在 `degraded`。
    """
    if rule is None or state.code is None:
        return LEVEL_OK, None
    if rule.halt_rounds > 0 and state.streak >= rule.halt_rounds:
        return LEVEL_HALT, state.code
    if rule.warn_rounds > 0 and state.streak >= rule.warn_rounds:
        return LEVEL_DEGRADED, state.code
    return LEVEL_OK, None


def rule_from_settings(settings) -> LivenessRule | None:
    """运维 settings → 契约；总闸关 ⇒ `None` ⇒ 整层不装配、`liveness` 键整键缺席。"""
    if not getattr(settings, "ai_trade_liveness_enabled", False):
        return None
    return LivenessRule(
        warn_rounds=max(0, int(getattr(settings, "ai_trade_liveness_warn_rounds", 0) or 0)),
        halt_rounds=max(0, int(getattr(settings, "ai_trade_liveness_halt_rounds", 0) or 0)))


class LivenessRuntime:
    """一个会话的活性看门狗**运行时载体**（纯同步、零 IO、不碰 executor）。

    它同时是 **`_band_unavailable_streak` 的唯一持有者**（评审 P1-1 / 放行条件 3）：合入前那个计数
    住在 `session._emit_base_rule_trace` 里，本轮**接管**它而不是并排再造一份——两份同源不同口径
    的计数正是本仓库反复付过学费的失效模式。既有的第 3 轮 `AI_TRADE_BASE_RULE_UNAVAILABLE` 事件
    因此改读 `band_streak`，判据与文案逐字保留（它是「第一声」，比新横幅早 17 轮，有独立价值）。

    Attributes:
        rule: 契约；`None` = 总闸关 ⇒ `verdict()` 恒 `ok`、wire 整键缺席、绝不请求停手。
        state: 当前连续阻塞断面。
        last_level: 最近一次上报过的档位（**只在跃迁时发事件**，防刷屏）。

    停手请求的幂等标志**刻意不在这里**：它是会话侧的编排状态（`session._liveness_halt_requested`
    与轮末投递槽 `_liveness_halt_pending` 成对，续跑时一并清），本类只回答「现在是哪一档」。
    在这里再放一份既没有写点，又会在 `reset()` 之后与会话侧那份分叉。
    """

    def __init__(self, rule: LivenessRule | None) -> None:
        self.rule = rule
        self.state = LivenessState()
        self.last_level = LEVEL_OK

    @property
    def enabled(self) -> bool:
        return self.rule is not None

    @property
    def band_streak(self) -> int:
        """【基础策略】通道连续不可用轮数——**唯一真源**（`session._band_unavailable_streak` 读它）。

        只有当前连续的那个码正是 `ma_band_unavailable` 时才非零：换了码或能开仓即归零，与合入前
        `_emit_base_rule_trace` 里那个 int 的语义逐字相同（`probe` 把 `ma_band` 排在最前，故
        「band 为 None」这一事实恒映射到这个码上）。
        """
        return self.state.streak if self.state.code == BLOCK_MA_BAND else 0

    def advance_round(self, *, ma_band_armed: bool, ma_band: dict | None, mark_px,
                      stale_feed: bool, llm_failed: bool, now_wall: float) -> str | None:
        """一轮一次的推进；返回本轮的阻塞码（`None` = 能开仓）。

        **必须排在 `_emit_base_rule_trace` 之前**：那条第 3 轮告警读的是本方法推进后的
        `band_streak`。落点在 `decide()` 返回之后、任何门禁之前——四个输入在那一刻全部已知，
        且没有一个取自模型的**动作**（`llm_failed` 取的是「这次调用成没成」，不是「模型说了什么」）。
        """
        code = probe(ma_band_armed=ma_band_armed, ma_band=ma_band, mark_px=mark_px,
                     stale_feed=stale_feed, llm_failed=llm_failed)
        self.state = advance(self.state, blocking_code=code, now_wall=now_wall)
        return code

    def note_fill(self) -> None:
        """任何一次成交 ⇒ 计数归零（系统证明了自己还活着）。

        **总闸关时不清零**：那时本运行时的唯一职责是承载 `_band_unavailable_streak` 的既有语义
        （只有「band 不为 None」才清零）。多一条清零判据会让 `AI_TRADE_BASE_RULE_UNAVAILABLE`
        的发火轮次与合入前不同——而 `G-D5` 要求它在总闸关时逐字节照发。
        """
        if self.rule is None:
            return
        self.state = LivenessState()

    def reset(self) -> None:
        """续跑时给看门狗重新上弦（用户点【继续】= 「我处理过了，再试一次」）。

        **与 `note_fill` 的区别**：这一条不看总闸——续跑本来就该把 band 计数也归零（合入前
        `_band_unavailable_streak` 是会话级 int，续跑不重建会话故它同样带着旧值，本轮顺带修正）。
        """
        self.state = LivenessState()
        self.last_level = LEVEL_OK

    def verdict(self) -> tuple[str, str | None]:
        """当前档位；总闸关 ⇒ 恒 `("ok", None)`。"""
        return verdict(self.state, self.rule)

    def take_transition(self) -> str | None:
        """档位**跃迁**了吗——是则返回新档位并记住它，否则 `None`（调用方据此决定发不发事件）。"""
        level, _ = self.verdict()
        if level == self.last_level:
            return None
        self.last_level = level
        return level

    def wire(self) -> dict | None:
        """`status.members[].liveness`；总闸关 ⇒ `None` ⇒ **整键缺席**（不是 `null`）。

        `level == "ok"` 时同样下发——「现在一切正常」与「后端没上线这个键」是两个结论，而前端的
        横幅渲染门控读的正是 `level`。
        """
        if self.rule is None:
            return None
        # 函数内导入是**为了破环**（不是随手写的）：`liveness_text` 在模块顶层 import 本模块的
        # 码表与档位常量，两边在模块层互引会当场 ImportError。文案层依赖判定层是对的方向，
        # 故反向的这一条留在调用点。
        from .liveness_text import wire as liveness_wire

        level, code = self.verdict()
        return liveness_wire(level, code, self.state.streak,
                             since_wall=self.state.first_wall)
