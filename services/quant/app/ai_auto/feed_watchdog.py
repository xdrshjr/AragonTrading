"""AI 自动交易「行情断连自愈阶梯」的纯核心（ai-trade-feed-self-healing 轴 B）。

本模块**只做判定与措辞，不做任何 IO**，且**零 import 会话对象**：它只吃 `FeedReadings` 这一个
读数结构体，因此可以完全离线单测。范式镜像 `ai_auto/lifecycle.py`（纯判定 + 判定性文案单点生成）；
动作与状态住在 `ai_auto/feed_runtime.py`，会话侧只留一行装配与一处调用。

**本模块存在的理由**：今天的 `_check_staleness` 只做两件事——置 `_stale_degraded = True`、推一条
文案。而 `_stale_degraded` 是一个**吸收态**：三条唤醒源（K 线 / 墙钟定时 / 信号翻转）全被它挡住，
而唯一能清除它的 `_last_candle_wall` 前移，其唯一写点是那条已经死掉的 WebSocket 自己。于是
**feed 死 ⇒ 会话降级 ⇒ 所有唤醒停摆 ⇒ 只有 feed 能解锁 ⇒ 永不恢复**。

**判定顺序中最容易被「顺手整理」掉的一条（判定表第 2 行，见 `plan()` 的 docstring）**：
「WS 已死」必须排在「行情看起来新鲜」**之前**。L2 的 REST 补取成功会把 `_last_candle_wall` 推到
`now`，此后新鲜度判据从此永真；第 2 行若排在它后面，`ws_alive` 就再也不会被求值——一条死掉的 WS
会被 60 秒一次的 REST 轮询**永久掩盖**，`classify_member` 回到 `armed`、横幅消失、stage 回到
`fresh` 导致恢复卡也消失。**那不是自愈，那是把一个看得见的停摆变成了一个看不见的停摆**，比现状
严重。
"""
from __future__ import annotations

from dataclasses import dataclass

from .lifecycle import describe_age

# ---------- 阶段码（稳定语义码，进 wire，前端按它选颜色，绝不自己拼中文）----------
STAGE_FRESH = "fresh"            # 行情正常
STAGE_DEGRADED = "degraded"      # 刚超过 stale_after，先说话，还没动手
STAGE_BACKFILL = "backfill"      # 正在用 REST 取最新一根
STAGE_RELINK = "relink"          # 正在强制换一条 WS
STAGE_ESCALATED = "escalated"    # 换了 N 次仍不新鲜，交回用户

# ---------- 动作码（会话侧只按它分派，不做任何判定）----------
ACTION_NONE = "none"
ACTION_CLEAR = "clear"
ACTION_DEGRADE = "degrade"
ACTION_BACKFILL = "backfill"
ACTION_RELINK = "relink"
ACTION_ESCALATE = "escalate"

# 呈现层三态（`feedRecovery.state`）：`healthy` ⇒ 前端整卡不渲染。
VIEW_HEALTHY = "healthy"
VIEW_RECOVERING = "recovering"
VIEW_STUCK = "stuck"

# 链路登记簿里「已经算好的不可达」——比 `feed_age` 更硬的证据。`degraded`（"不稳"）刻意不在内：
# 数据静默重连的每一轮都夹着一次 `note_ws(event="connected")`，它会把 `consecutive_failures` 清零，
# 故那一形态的通道**恒停在 `degraded`**（`link_state.py:derive_state`）。把它算成不可达会让每一次
# 重连的头几秒都触发一次换链。
_LINK_DOWN_STATES = frozenset({"down"})


@dataclass(frozen=True)
class FeedReadings:
    """自愈判定的唯一入参（全部是**已经算好的数**，本模块不查任何东西）。

    Attributes:
        now_wall: 判定时刻的墙钟秒。
        feed_age_s: `now - _last_candle_wall`（`<= 0` 视为 0）。
        stale_after_s: `BAR_SECONDS[bar] * live_auto_staleness_factor`；`<= 0` ⇒ 整层空转。
        ws_alive: `self._ws is not None and self._ws.is_alive`。
        link_state: `SessionLink.channel_state(owner)`（`up` | `idle` | `degraded` | `down`
            | `unknown`）。
        is_degraded: 会话当前的 `_stale_degraded`。
        last_backfill_wall: 最近一次 REST 活性补取的墙钟秒（从未 ⇒ 0）。
        last_relink_wall: 最近一次强制换链的墙钟秒（从未 ⇒ 0）。
        relink_attempts: 本降级期内已换链次数（WS 送来确认根即复位）。
    """

    now_wall: float = 0.0
    feed_age_s: float = 0.0
    stale_after_s: float = 0.0
    ws_alive: bool = True
    link_state: str = "unknown"
    is_degraded: bool = False
    last_backfill_wall: float = 0.0
    last_relink_wall: float = 0.0
    relink_attempts: int = 0


@dataclass(frozen=True)
class FeedConfig:
    """自愈阶梯的阈值集（由 `feed_runtime` 从 settings 装配；本模块不认识 Settings）。

    Attributes:
        enabled: 整轴总闸。关 ⇒ `plan()` 只返回 `clear` / `degrade` / `none` 三态（逐字回落到本
            特性合入前的 `_check_staleness` 行为）。
        backfill_interval_s: REST 活性补取冷却。
        relink_factor: `feed_age > stale_after × relink_factor` 才走会话级换链。
        relink_min_interval_s: 换链冷却。
        escalate_after_relinks: 换链几次仍不新鲜即升级告警；0 ⇒ 永不升级。
    """

    enabled: bool = True
    backfill_interval_s: float = 60.0
    relink_factor: float = 2.0
    relink_min_interval_s: float = 120.0
    escalate_after_relinks: int = 3


@dataclass(frozen=True)
class FeedPlan:
    """本 tick 该干的**那一件**事。

    Attributes:
        action: `ACTION_*`。
        stage: `STAGE_*`（进 wire，前端据它选颜色）。
        reason_text: 判定性文案的**唯一**生成点（会话侧绝不自己拼中文）。
        next_attempt_in_s: 距下一次尝试的秒数；`None` = 无待办倒计时（冷却外 / 已升级）。
    """

    action: str = ACTION_NONE
    stage: str = STAGE_FRESH
    reason_text: str = ""
    next_attempt_in_s: float | None = None


def plan(r: FeedReadings, cfg: FeedConfig) -> FeedPlan:
    """自愈阶梯的判定（纯函数）。**判定顺序固定：先致命后次要。**

    | 序 | 条件 | action |
    |---|---|---|
    | 1 | `stale_after_s <= 0`（或总闸关 ⇒ 三态回落） | `none` |
    | 2 | **`not ws_alive` 或 `link_state == "down"`** | 该升级 ⇒ `escalate`；冷却到期 ⇒ `relink`；冷却中 ⇒ **不返回，继续向下判定** |
    | 3 | `feed_age <= stale_after` | `clear`（仅当已降级）否则 `none` |
    | 4 | `not is_degraded` | `degrade` |
    | 5 | `relink_attempts >= escalate_after > 0` 且（换链门槛已过 **或 `not ws_alive`**） | `escalate` |
    | 6 | 换链门槛已过 且 换链冷却到期 | `relink` |
    | 7 | 补取冷却到期 | `backfill` |
    | 8 | 其余 | `none`（带 `next_attempt_in_s`） |

    **为什么第 2 行不能往下挪**：见模块 docstring。它的唯一护栏是
    `test_ai_trade_feed_watchdog.py` 里那条「`feed_age = 0` + `not ws_alive` ⇒ 仍 `relink`」。

    **`action` 与 `stage` 是两个量，绝不可合并**（【R:P1-4】的落点）：REST 吊命期间行情**确实**是
    新鲜的，故 `action` 该是 `clear`（解锁 Agent 决策）；但实时通道**并没有**恢复，故 `stage` 必须
    停在 `relink` / `escalated` 而不是 `fresh`——横幅可以消失，恢复卡不许消失。把两者合并成一个
    「反正都新鲜了」正是把一个看得见的停摆变成看不见的停摆那条路。

    **为什么第 5 行要带 `or not ws_alive`**：一条被 REST 吊着、换了 N 次仍换不活的 WS，它的
    `feed_age` 永远越不过 `stale_after × relink_factor` 的门槛；没有这一支就会无限 relink 而
    永不升级，用户永远等不到那个「主动权交回给你」的时刻。

    **为什么 L2 排在 L3 之后判定、却在阶梯上编号更小**：`plan` 的顺序是「本 tick 该干哪一件事」，
    relink 比 backfill 昂贵得多且有更长的冷却，所以在双方都到期时优先 relink（换链是根治，补取是
    维持）；但常态下 backfill 的冷却远短于 relink，所以实际执行序列是
    `degrade → backfill × N → relink → backfill × N → … → escalate`，与阶梯编号一致。

    Args:
        r: 本 tick 的读数。
        cfg: 阈值集。

    Returns:
        本 tick 的 `FeedPlan`。总闸关时 `action` 值域退化为 `clear` / `degrade` / `none` 三态。
    """
    age = max(0.0, float(r.feed_age_s))
    if r.stale_after_s <= 0:
        return FeedPlan(ACTION_NONE, STAGE_FRESH, "")
    if not cfg.enabled:
        return _legacy_plan(r, age)

    link_down = not r.ws_alive or r.link_state in _LINK_DOWN_STATES
    relink_wait = _cooldown_left(r.now_wall, r.last_relink_wall, cfg.relink_min_interval_s)
    relink_due = age > r.stale_after_s * cfg.relink_factor
    escalate_now = _should_escalate(r, cfg, relink_due=relink_due)

    # ---- 第 2 行：WS 已死 / 登记簿判不可达（比 feed_age 更硬的证据，必须先被求值）----
    hold_stage: str | None = None
    hold_wait: float | None = None
    if link_down:
        if escalate_now:
            return FeedPlan(ACTION_ESCALATE, STAGE_ESCALATED, _escalate_text(r), None)
        if relink_wait <= 0:
            return FeedPlan(ACTION_RELINK, STAGE_RELINK, _relink_text(r), None)
        # 冷却中：**继续向下判定**（「什么都不干」会让这条最硬的证据在冷却期内彻底失声），
        # 但把阶段锁在 `relink` 并带上倒计时——恢复卡据此在整个 REST 吊命期间保持可见。
        hold_stage, hold_wait = STAGE_RELINK, relink_wait

    # ---- 第 3 行：行情确实新鲜（含被 REST 补取压住的那一档）----
    if age <= r.stale_after_s:
        action = ACTION_CLEAR if r.is_degraded else ACTION_NONE
        text = _RECOVERED_TEXT if r.is_degraded else ""
        return FeedPlan(action, hold_stage or STAGE_FRESH, text, hold_wait)

    # ---- 第 4 行：首次超阈，先说话，还没动手 ----
    if not r.is_degraded:
        return FeedPlan(ACTION_DEGRADE, hold_stage or STAGE_DEGRADED, _DEGRADED_TEXT, hold_wait)

    # ---- 第 5 行：升级告警，主动权交回用户 ----
    if escalate_now:
        return FeedPlan(ACTION_ESCALATE, STAGE_ESCALATED, _escalate_text(r), None)

    # ---- 第 6 行：会话级换链（bar × relink_factor × staleness_factor 的兜底门槛）----
    if relink_due and relink_wait <= 0:
        return FeedPlan(ACTION_RELINK, STAGE_RELINK, _relink_text(r), None)

    # ---- 第 7 行：REST 活性补取 ----
    backfill_wait = _cooldown_left(r.now_wall, r.last_backfill_wall, cfg.backfill_interval_s)
    if backfill_wait <= 0:
        return FeedPlan(ACTION_BACKFILL, STAGE_BACKFILL, _BACKFILL_TEXT, None)

    # ---- 第 8 行：两条待办都在冷却里，倒计时取最近的那一个 ----
    waits = [w for w in ((relink_wait if (relink_due or link_down) else None), backfill_wait)
             if w is not None and w > 0]
    stage = hold_stage or (STAGE_RELINK if relink_due and relink_wait > 0 else STAGE_BACKFILL)
    return FeedPlan(ACTION_NONE, stage, "", min(waits) if waits else None)


def describe(*, stage: str, attempts_backfill: int, attempts_relink: int,
             feed_age_s: float, next_attempt_in_s: float | None,
             last_outcome: str | None, last_outcome_text: str | None,
             source: str | None) -> str:
    """恢复卡那一行的判定性文案（**唯一**生成点，镜像 `lifecycle.classify_member`）。

    前端绝不自己拼中文、也绝不自己算任何一个数（次数 / 倒计时 / 年龄全部来自 wire）。
    **文案里绝不写 markdown 星号**：这条串直接进面板状态行，`**粗体**` 会显示成字面星号
    （本仓库已复发两次，见 `session_link.py:_repair` 的注释）。

    Args:
        stage: `STAGE_*`。
        attempts_backfill: 本降级期内的 REST 补取次数。
        attempts_relink: 本降级期内的换链次数。
        feed_age_s: 行情年龄（秒）。
        next_attempt_in_s: 距下一次尝试的秒数；`None` ⇒ 整句不读秒。
        last_outcome: `ok` | `no_data` | `failed` | `None`。
        last_outcome_text: 上一次尝试的结果一句话。
        source: 最后一根确认根的来源（`ws` | `rest` | `None`）。

    Returns:
        一句人话；`stage == STAGE_FRESH` ⇒ 空串（调用方据此不渲染）。
    """
    if stage == STAGE_FRESH:
        return ""
    if stage == STAGE_ESCALATED:
        parts = [f"自动恢复已尝试 {attempts_relink} 次仍未拿到新 K 线"
                 f"（{describe_age(feed_age_s)}无数据）。",
                 "交易所侧止盈止损仍生效；该标的的 Agent 决策已停摆。"]
        return "".join(parts)
    head = "正在自动恢复行情连接"
    parts = [head]
    if attempts_relink > 0:
        parts.append(f"已重连 {attempts_relink} 次")
    if attempts_backfill > 0:
        parts.append(f"已补取 {attempts_backfill} 次")
    if next_attempt_in_s is not None:
        parts.append(f"下一次尝试 {max(0, int(round(next_attempt_in_s)))} 秒后")
    if last_outcome_text:
        parts.append(f"上次结果：{last_outcome_text}")
    text = " · ".join(parts)
    if source == "rest":
        # 【R:P1-4】REST 生命维持期间用户必须知道自己跑在什么上面：此时成员会回到 `armed`、
        # 横幅消失，这一句是唯一的知情通道。
        text = f"{text}。当前 K 线由 REST 补取维持，实时通道尚未恢复"
    return text


def view_state(stage: str) -> str:
    """`stage` → 呈现层三态（`healthy` ⇒ 前端整卡不渲染）。"""
    if stage == STAGE_FRESH:
        return VIEW_HEALTHY
    return VIEW_STUCK if stage == STAGE_ESCALATED else VIEW_RECOVERING


# ---------- 内部 ----------

# 三条判定性文案**逐字沿用**本特性合入前 `_check_staleness` / `_clear_stale_if_recovered` 的原文：
# 总闸关时的回落必须是逐字的，用户读到的那句话不许因为一个运维开关而变。
_DEGRADED_TEXT = "行情连接异常，AI 自动交易暂停响应 K 线（交易所侧止盈止损仍生效）"
_RECOVERED_TEXT = "行情已恢复，AI 自动交易盯市恢复正常"
_BACKFILL_TEXT = "正在通过 REST 补取最新 K 线"


def _legacy_plan(r: FeedReadings, age: float) -> FeedPlan:
    """总闸关时的**逐字**回落：只有「恢复出声」与「首次超阈出声」两个动作，绝不动手。"""
    if age <= r.stale_after_s:
        return (FeedPlan(ACTION_CLEAR, STAGE_FRESH, _RECOVERED_TEXT) if r.is_degraded
                else FeedPlan(ACTION_NONE, STAGE_FRESH, ""))
    if not r.is_degraded:
        return FeedPlan(ACTION_DEGRADE, STAGE_DEGRADED, _DEGRADED_TEXT)
    return FeedPlan(ACTION_NONE, STAGE_DEGRADED, "")


def _cooldown_left(now_wall: float, last_wall: float, interval_s: float) -> float:
    """距冷却结束还剩几秒（`<= 0` 即到期）。从未尝试过（`last_wall <= 0`）⇒ 立刻到期。"""
    if last_wall <= 0:
        return 0.0
    return max(0.0, float(interval_s) - (float(now_wall) - float(last_wall)))


def _should_escalate(r: FeedReadings, cfg: FeedConfig, *, relink_due: bool) -> bool:
    """是否该把主动权交回用户。`escalate_after_relinks = 0` ⇒ 恒假（「关得掉的取值」）。

    `or not r.ws_alive` 是【R:P0-1】的配套：一条被 REST 吊着、换了 N 次仍换不活的 WS，它的
    `feed_age` 永远越不过换链门槛，没有这一支就会无限 relink 而永不升级。
    """
    if cfg.escalate_after_relinks <= 0:
        return False
    if r.relink_attempts < cfg.escalate_after_relinks:
        return False
    return relink_due or not r.ws_alive


def _relink_text(r: FeedReadings) -> str:
    """换链那一句。**如实说出触发它的证据**——「行情老了」与「连接死了」是两件事。"""
    if not r.ws_alive:
        return "实时行情通道已停止工作，正在重建连接"
    if r.link_state in _LINK_DOWN_STATES:
        return "实时行情链路判定为不可达，正在重建连接"
    return f"行情已 {describe_age(r.feed_age_s)}没有新 K 线，正在重建实时连接"


def _escalate_text(r: FeedReadings) -> str:
    return (f"行情已 {describe_age(r.feed_age_s)}没有新 K 线，自动恢复已多次尝试仍未成功；"
            "交易所侧止盈止损仍生效")
