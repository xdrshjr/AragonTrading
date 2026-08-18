"""AI 自动交易「分组生命周期」的纯核心（ai-trade-lifecycle-truth §3.1 / §3.2）。

本模块**只做判定与措辞，不做任何 IO**，且**零 import 会话对象**：它只吃 `MemberReadings`
这一个读数结构体，因此可以完全离线单测。范式镜像 `ai_auto/pause.py`（纯核心判定 + 判定性
文案单点生成）。

**本轮的立论（L-1）**：分组生命周期是**派生量**，不是存储量。`AiTradeGroup.state` 从今往后
只是「用户意图」（`declaredState`），真正下发给界面的 `state` 必须由各成员会话的**事实**
现算出来 —— 否则「我刚刚赋了值」与「它现在真的在跑」之间的任何一条缝，都会变成一句界面上
无法被反证的谎话（§1.2 的 12 条根因是同一个形状的 12 次复发）。

**`armed` 不是风控闸（L-2 的边界，必须写在这里防后人改变它的职责）**：它纯粹是**呈现层的
真值** —— 后端绝不会因为 `armed == false` 而拒绝下单，那是既有 `_stale_degraded` /
`_check_staleness` 的职责，本轮一行不动。把它变成第二道闸，就会与既有闸各判一次而漂移。
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------- 派生态（`group_status()["state"]` 的新语义）----------
STATE_RUNNING = "running"
STATE_PAUSED = "paused"
STATE_STOPPED = "stopped"

# ---------- 未武装原因（稳定语义码，进 wire、前端按它选颜色，绝不自己拼中文）----------
ARMED_OK = "armed"
NOT_ARMED_STOPPED = "session_stopped"     # 会话已终止（自终止 / 被停）
NOT_ARMED_PAUSED = "session_paused"       # 会话仍在暂停态
NOT_ARMED_STALE = "feed_stale"            # 行情年龄超过 bar × staleness_factor
NOT_ARMED_LINK = "feed_down"              # 实时链路不可达（链路登记簿判定）
NOT_ARMED_WARMUP = "not_warmed"           # 窗口为空，尚未暖机完成

# 会话停止原因 → 中文（与前端 `STOP_REASON_LABELS` 同一份枚举；缺省不臆造，只说「已停止」）。
STOP_REASON_TEXT = {
    "user_stop": "手动停止",
    "session_stop_loss": "触发会话止损",
    "session_take_profit": "触发会话止盈",
    "shutdown": "服务重启",
    "stale_feed": "行情中断保护",
    "start_failed": "启动失败",
    "resume_failed": "续跑失败",
    "user_end": "已结束归档",
}

# 「链路不可达」只认登记簿的 `down` 这一个值：`unknown` 是「还没探到」（会话刚起 / 通道刚建），
# 把它算成不可达会让每一次启动的头几秒都挂一条假红条 —— 与本轮「读不到 ≠ 没有」的极性一致，
# 但方向相反：这里的保守选择是**不喊狼来了**，因为真正的风控判据在 `_check_staleness` 那边。
_LINK_DOWN_STATES = frozenset({"down"})


@dataclass(frozen=True)
class MemberReadings:
    """单成员的生命周期读数（唯一入参结构；全部是**已经算好的数**，本模块不查任何东西）。

    Attributes:
        inst_id: 标的。
        state: 会话态（`running` | `paused` | `stopped`）。
        stop_reason: 会话终止原因（未终止 ⇒ None）。
        pause_reason: 暂停原因（未暂停 ⇒ None）。
        warmed_bars: 已暖机根数（0 ⇒ 窗口还是空的）。
        feed_age_s: `now_wall - _last_candle_wall`（负数视为 0）。
        stale_after_s: `BAR_SECONDS[bar] * live_auto_staleness_factor`。
        link_state: 链路登记簿口径（`up` | `idle` | `degraded` | `down` | `unknown`）。
        next_wake_in_s: 距下一次墙钟定时唤醒的秒数（未装墙钟唤醒 ⇒ None）。
        next_bar_in_s: 距下一根 K 线收盘的秒数（相位未知 ⇒ None）。
        rounds_completed: 已完成决策轮数。
    """

    inst_id: str
    state: str = STATE_RUNNING
    stop_reason: str | None = None
    pause_reason: str | None = None
    warmed_bars: int = 0
    feed_age_s: float = 0.0
    stale_after_s: float = 0.0
    link_state: str = "unknown"
    next_wake_in_s: float | None = None
    next_bar_in_s: float | None = None
    rounds_completed: int = 0


def derive_group_state(states: list[str]) -> str:
    """分组派生态：任一 `running` ⇒ running；否则任一 `paused` ⇒ paused；否则 stopped。

    空列表同样是 `stopped` —— 一个没有任何成员的分组绝不是「在跑」。这条与 `stop_member` /
    `_note_session_stopped` 的清组判据（`not any(is_active)`）同极性：三处描述的是同一件事。
    """
    if any(s == STATE_RUNNING for s in states):
        return STATE_RUNNING
    if any(s == STATE_PAUSED for s in states):
        return STATE_PAUSED
    return STATE_STOPPED


def classify_member(r: MemberReadings) -> tuple[str, str]:
    """单成员的 `(reason_code, reason_text)`。**判定顺序固定：先致命后次要。**

    `stopped → paused → not_warmed → feed_down → feed_stale → armed`。

    为什么 `stopped` 必须排第一：一个已经终止的会话，它的行情年龄与暖机根数都没有意义 ——
    先说次要原因等于把最重要的事实埋起来，而那正是 D-1 现场的读感（「恢复成功了，但它不交易」）。

    Args:
        r: 该成员的读数。

    Returns:
        `(code, text)`；`code == ARMED_OK` 时 `text` 为空串（没有需要解释的事）。
    """
    if r.state == STATE_STOPPED:
        why = STOP_REASON_TEXT.get(str(r.stop_reason or ""), "")
        return NOT_ARMED_STOPPED, (
            f"会话已终止（{why}），不会再有新的决策。" if why
            else "会话已终止，不会再有新的决策。")
    if r.state == STATE_PAUSED:
        return NOT_ARMED_PAUSED, "会话处于暂停态：不再开新仓，盯市与保护层照常运行。"
    if r.warmed_bars <= 0:
        return NOT_ARMED_WARMUP, "行情窗口尚未暖机完成，Agent 暂不响应。"
    if r.link_state in _LINK_DOWN_STATES:
        return NOT_ARMED_LINK, "实时链路不可达，Agent 暂不响应；交易所侧止盈止损仍生效。"
    if r.stale_after_s > 0 and r.feed_age_s > r.stale_after_s:
        return NOT_ARMED_STALE, (
            f"行情已 {describe_age(r.feed_age_s)}没有新 K 线，Agent 暂不响应；"
            "交易所侧止盈止损仍生效。")
    return ARMED_OK, ""


def is_armed(r: MemberReadings) -> bool:
    """该成员是否**真的**具备思考条件（§3.2 的合取式；`classify_member` 的布尔投影）。"""
    return classify_member(r)[0] == ARMED_OK


def member_lifecycle_wire(r: MemberReadings, *, paused_ts: int = 0,
                          resumed_ts: int = 0) -> dict:
    """成员级 `lifecycle`（§5.2 的形状）。会话 / manager / WS 三处共用**同一次**装配。

    Args:
        r: 该成员的读数。
        paused_ts: 进入暂停态的墙钟秒（未暂停 ⇒ 0）。
        resumed_ts: 最近一次续跑成功的墙钟秒（从未续跑 ⇒ 0）。

    Returns:
        §5.2 的 wire（全部键恒下发，绝不因为「本轮没数据」而整键缺席 —— 前端要能区分
        「没数据」与「后端没上线这个键」）。
    """
    reason, text = classify_member(r)
    return {
        "state": r.state,
        "armed": reason == ARMED_OK,
        "reason": reason,
        "reasonText": text,
        "warmedBars": int(r.warmed_bars),
        "feedAgeS": round(max(0.0, float(r.feed_age_s)), 1),
        "staleAfterS": round(max(0.0, float(r.stale_after_s)), 1),
        "linkState": r.link_state,
        "nextWakeInS": _round_or_none(r.next_wake_in_s),
        "nextBarInS": _round_or_none(r.next_bar_in_s),
        "roundsCompleted": int(r.rounds_completed),
        "pauseReason": r.pause_reason,
        "pausedTs": int(paused_ts or 0),
        "resumedTs": int(resumed_ts or 0),
        "stopReason": r.stop_reason,
    }


def group_lifecycle_wire(readings: list[MemberReadings], *, declared: str,
                         paused_ts: int, pause_reason: str | None,
                         resumed_count: int, now_ts: int) -> dict:
    """分组级 `lifecycle`（§5.1 的形状）。判定性文案在此**单点**生成。

    `armed` 是全成员的与门（与 `[P2-13]` 整组原子续跑同极性），但**分子分母同时下发**
    （`membersArmed` / `membersTotal`）：2 个标的死 1 个时只说「未武装」会让用户以为两个都
    停了，而另一个其实还在正常交易（[评审 R1-7]）。前端绝不自己数。

    Args:
        readings: 逐成员读数（顺序即成员顺序）。
        declared: `AiTradeGroup.state`（意图态，只用于诊断与「两者不等即 bug」的告警）。
        paused_ts: 分组进入暂停的墙钟秒。
        pause_reason: 分组暂停原因（稳定枚举）。
        resumed_count: 已续跑次数。
        now_ts: 服务端墙钟秒（倒计时的锚点，避免本机时钟偏移）。

    Returns:
        §5.1 的 `lifecycle` wire。
    """
    rows = [(r, classify_member(r)) for r in readings]
    armed_rows = [r for r, (code, _) in rows if code == ARMED_OK]
    return {
        "state": derive_group_state([r.state for r in readings]),
        "declaredState": declared,
        "armed": bool(readings) and len(armed_rows) == len(readings),
        "membersArmed": len(armed_rows),
        "membersTotal": len(readings),
        "asOfTs": int(now_ts),
        "notArmed": [{"instId": r.inst_id, "state": r.state,
                      "reason": code, "reasonText": text}
                     for r, (code, text) in rows if code != ARMED_OK],
        "nextWakeInS": _min_or_none([r.next_wake_in_s for r in readings]),
        "nextBarInS": _min_or_none([r.next_bar_in_s for r in readings]),
        "pauseReason": pause_reason,
        "pausedTs": int(paused_ts or 0),
        "resumedCount": int(resumed_count or 0),
    }


def describe_not_armed(readings: list[MemberReadings]) -> str:
    """未武装成员的**逐标的**一句话（拒绝续跑时的 409 文案，§3.6）。

    单标的时省略标的前缀（「继续失败，已退回暂停态（原因：行情不可达）」读起来才像人话）；
    多标的时必须点名 —— 整组与门抹掉活着的成员是本轮明令禁止的读法。
    """
    rows = [(r, classify_member(r)) for r in readings]
    bad = [(r, text) for r, (code, text) in rows if code != ARMED_OK]
    if not bad:
        return ""
    if len(readings) == 1:
        return bad[0][1]
    return " ".join(f"{r.inst_id}：{text}" for r, text in bad)


def describe_age(seconds: float) -> str:
    """行情年龄中文化（`12 分钟` / `2 小时 3 分钟`）。负数按 0；< 60 秒说「不到 1 分钟」。"""
    total = max(0, int(seconds))
    if total < 60:
        return "不到 1 分钟"
    hours, rest = divmod(total, 3600)
    minutes = rest // 60
    if hours and minutes:
        return f"{hours} 小时 {minutes} 分钟"
    if hours:
        return f"{hours} 小时"
    return f"{minutes} 分钟"


def _round_or_none(value: float | None) -> int | None:
    """秒级倒计时取整；`None`（不适用）原样透传 —— 绝不用 0 冒充「不适用」。"""
    return None if value is None else max(0, int(round(float(value))))


def _min_or_none(values: list[float | None]) -> int | None:
    """全组最近的那一次（全为 None ⇒ None）。"""
    present = [float(v) for v in values if v is not None]
    return None if not present else max(0, int(round(min(present))))
