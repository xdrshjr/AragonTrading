"""AI 自动交易「暂停 / 继续」的纯核心（ai-trade-pause-resume §3.6 / §3.10）。

本模块**只做判定与措辞，不做任何 IO**：状态常量、离线差异判定矩阵、超龄判定、以及全部
**面向用户的判定性文案**。文案单点收口在后端（§3.10）的理由是本仓库已有两次前后端各存一份
口径导致静默漂移的事故（`edgeSource` 逐键手抄吞新键、`horizon-profiles` 的 11×5 表）——一句
判定性的话如果两处各写一遍，用户拿着真钱看到的那一句迟早与后端真正据以拒绝的判据分叉。

极性纪律（真实资金，R-1）：**读不到 ≠ 没有**。身份对不上、张数变大、方向反了、读不到持仓，
一律**拒绝续跑**，绝不「猜一个最可能的解释」再替用户接管一笔他自己开的仓位。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------- 状态与原因（落库、进 wire、前端按它选文案）----------
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_ENDED = "ended"

PAUSE_REASON_USER = "user"
PAUSE_REASON_APP_QUIT = "app_quit"
PAUSE_REASON_PROCESS_RESTART = "process_restart"
PAUSE_REASON_DISABLED_BY_CONFIG = "disabled_by_config"
# 续跑武装复检未通过后的整组回滚（ai-trade-lifecycle-truth B-2 ③ / [评审 R1-4]）。
# **扩展稳定枚举要一次改齐所有落点**（CLAUDE.md §五）：本常量 + 下面的文案表 +
# `apps/web/lib/aiTradeTypes.ts` 的 `AiTradePauseReason` 联合类型 + `lib/aiTrade.ts` 的
# `AI_TRADE_PAUSE_REASON_TEXT`。少改一处，回滚后的暂停原因在界面上就是**空的**。
PAUSE_REASON_RESUME_ROLLBACK = "resume_rollback"
# 活性看门狗自动停手（ai-trade-loss-and-liveness-hardening 轴 D）：连续 N 轮**结构上**无法开仓
# 且期间零成交 ⇒ 整组暂停。**绝不平仓**（`flatten=False`）——阻塞态下我们恰恰不确定行情是否可信。
# 同上条：扩展稳定枚举要一次改齐四处镜像（本常量 + 下面的文案表 + `aiTradeTypes.AiTradePauseReason`
# 联合 + `lib/aiTrade.ts` 的 `AI_TRADE_PAUSE_REASON_TEXT`），漏一处界面上就是**空的**。
PAUSE_REASON_LIVENESS = "liveness_blocked"

# 稳定枚举 → 中文说明（§3.0 那张表的唯一实现落点；每一格都有真实产生点，绝不留死值）。
PAUSE_REASON_TEXT = {
    PAUSE_REASON_USER: "你暂停了它",
    PAUSE_REASON_APP_QUIT: "软件退出时自动暂停",
    PAUSE_REASON_PROCESS_RESTART: "上次未正常退出，已自动暂停",
    PAUSE_REASON_DISABLED_BY_CONFIG: "本机已关闭 AI 自动交易，此会话只能结束归档",
    PAUSE_REASON_RESUME_ROLLBACK: "继续失败，已退回暂停",
    PAUSE_REASON_LIVENESS: "长时间无法开仓且零成交，已自动停手（仓位保留）",
}

# ---------- 十一个 verdict（§3.6）----------
VERDICT_MATCHED = "matched"
VERDICT_FLAT_OK = "flat_ok"
VERDICT_PARTIALLY_CLOSED = "partially_closed"
VERDICT_CLOSED_OFFLINE = "closed_offline"
VERDICT_SIZE_INCREASED = "size_increased"
VERDICT_SIDE_FLIPPED = "side_flipped"
VERDICT_FOREIGN_POSITION = "foreign_position"
VERDICT_OPENED_OFFLINE = "opened_offline"
VERDICT_UNVERIFIABLE_IDENTITY = "unverifiable_identity"
VERDICT_UNKNOWN_ORDER = "unknown_order"
VERDICT_UNREADABLE = "unreadable"

# 直接接管（绿点）/ 需勾选确认（黄条）/ 一律拒绝（红条）。三集合的并集即全部 verdict。
READY_VERDICTS = frozenset({VERDICT_MATCHED, VERDICT_FLAT_OK})
CONFIRM_VERDICTS = frozenset({VERDICT_PARTIALLY_CLOSED, VERDICT_CLOSED_OFFLINE})
BLOCKED_VERDICTS = frozenset({
    VERDICT_SIZE_INCREASED, VERDICT_SIDE_FLIPPED, VERDICT_FOREIGN_POSITION,
    VERDICT_OPENED_OFFLINE, VERDICT_UNVERIFIABLE_IDENTITY, VERDICT_UNKNOWN_ORDER,
    VERDICT_UNREADABLE})
ALL_VERDICTS = READY_VERDICTS | CONFIRM_VERDICTS | BLOCKED_VERDICTS

# 组级 verdict（§5.2 的 `verdict` 键）。
GROUP_READY = "ready"
GROUP_NEEDS_CONFIRM = "needs_confirm"
GROUP_BLOCKED = "blocked"

# 阻断该成员的兜底缺失码（`ai_trade_resume_backstop_required=True` 时才产生）。
BACKSTOP_MISSING_CODE = "AI_TRADE_RESUME_BACKSTOP_MISSING"

# 未成交挂单里会阻断续跑的两类（与 `manager._fetch_blocking_pending` 同一份分类真源）。
_BLOCKING_ORDER_KINDS = ("limit_open", "limit_close")


@dataclass(frozen=True)
class DriftInput:
    """单成员差异判定的全部输入（聚合结构体：CLAUDE.md 形参 ≤5 的既有做法，同 `SessionReadings`）。

    `expected`：暂停那一刻落进状态表的持仓断面（`side`/`size`/`posId`/`openCTime`），无仓 ⇒ None。
    `actual`：续跑前向交易所读回的该标的持仓（`side`/`size`/`posId`/`cTimeS`），无仓 ⇒ None。
    `readable=False` 表示 S3 取数失败——它与「没有持仓」在真实资金上**不是同一件事**。
    `position_unknown=True` 表示**暂停那一刻**就没读到持仓（状态表里的 `positionUnknown`），
    于是 `expected` 是「不知道」而不是「没有」——同一条极性，只是发生在另一个时刻。
    """

    expected: dict | None = None
    actual: dict | None = None
    pending: list[dict] = field(default_factory=list)
    known_cl_ord_ids: frozenset[str] = frozenset()
    paused_ts: int = 0
    size_tol: float = 0.02
    readable: bool = True
    position_unknown: bool = False


def classify_member_drift(inp: DriftInput) -> tuple[str, str]:
    """离线差异判定矩阵 → `(verdict, message)`。**纯函数、无 IO、绝不抛。**

    只比 `(side, size)` 是不够的（[P0-1]）：「我们的仓被 OCO 平掉、用户随后自己开了同向同量的
    一手」在只比 `(side,size)` 的判据下是 `matched`，那正是 R-1 明令禁止的静默接管。故身份
    （`posId`，退而求其次 `cTimeS ≤ pausedTs`）是**先于**方向与张数的第一道判据。
    """
    if not inp.readable:
        return VERDICT_UNREADABLE, "无法向交易所读取该标的的持仓，暂时不能确认续跑是否安全。请稍后重试，或先结束归档。"
    verdict, message = _classify_position(inp)
    if verdict in BLOCKED_VERDICTS:
        return verdict, message
    unknown = _unknown_orders(inp)
    if unknown:
        return VERDICT_UNKNOWN_ORDER, (
            f"该标的有 {len(unknown)} 笔不是本会话下出的未成交挂单，"
            "AI 自动交易拒绝接管来路不明的挂单：请先在实盘面板撤单或处理后再继续。")
    return verdict, message


def _classify_position(inp: DriftInput) -> tuple[str, str]:
    """持仓侧的九格判定（身份 → 方向 → 张数）；挂单一格由调用方叠加。"""
    exp = _position_view(inp.expected)
    act = _position_view(inp.actual)
    if inp.position_unknown:
        # 暂停那一刻就没读到持仓：现在**有**仓 ⇒ 无从证明它是不是我们那一笔 ⇒ 拒绝；
        # 现在**无**仓 ⇒ 以空仓接管是安全的，但那一段的盈亏可能缺失，故要求用户确认。
        if act is not None:
            return VERDICT_UNVERIFIABLE_IDENTITY, (
                "暂停时未能向交易所读到该标的的持仓，现在却有一笔持仓——无法确认它是不是本会话"
                "开出的，故拒绝续跑。请到实盘面板确认后再决定，或选择结束归档。")
        return VERDICT_CLOSED_OFFLINE, (
            "暂停时未能向交易所读到该标的的持仓；现在无持仓，续跑将以空仓接管"
            "（暂停到现在这一段的盈亏可能缺失）。")
    if exp is None and act is None:
        return VERDICT_FLAT_OK, "暂停时无持仓，现在也无持仓，可直接以空仓继续。"
    if exp is None:
        return VERDICT_OPENED_OFFLINE, (
            f"暂停期间该标的出现了一笔不是本会话开出的持仓（{_side_cn(act['side'])} {act['size']:g} 张）。"
            "继续会让 AI 接管一笔它从未开过的仓位，故拒绝续跑。")
    if act is None:
        return VERDICT_CLOSED_OFFLINE, (
            f"离线期间该仓已被平掉（暂停时为{_side_cn(exp['side'])} {exp['size']:g} 张，现在无持仓）。"
            "续跑将以空仓接管。")
    identity = _identity_verdict(exp, act, inp.paused_ts)
    if identity is not None:
        return identity
    if exp["side"] != act["side"]:
        return VERDICT_SIDE_FLIPPED, (
            f"当前持仓方向与暂停时不符（暂停时{_side_cn(exp['side'])}，现在{_side_cn(act['side'])}），"
            "拒绝续跑。这笔持仓仍在交易所上，请到实盘面板自行处理，或选择结束归档。")
    return _size_verdict(exp, act, inp.size_tol)


def _identity_verdict(exp: dict, act: dict, paused_ts: int) -> tuple[str, str] | None:
    """身份判据（[P0-1]）：相符 ⇒ None（继续往下比方向与张数）；否则给出拒绝格。

    优先 `posId` 逐字相等；两侧任一缺 `posId` 时退到交易所的建仓时刻 `cTimeS`——它晚于暂停
    时刻即**一定不是我们那一笔**。两者都取不到 ⇒ `unverifiable_identity`（诚实不变式 I-4：
    读不到就说读不到，绝不按「大概是同一笔」接管）。
    """
    exp_id, act_id = exp["pos_id"], act["pos_id"]
    if exp_id and act_id:
        if exp_id == act_id:
            return None
        return VERDICT_FOREIGN_POSITION, _foreign_text(act)
    if act["open_ts"] <= 0:
        return VERDICT_UNVERIFIABLE_IDENTITY, (
            "无法确认当前持仓是不是暂停时那一笔（交易所未回带持仓编号与建仓时间），"
            "为避免接管一笔本会话从未开过的仓位，暂不续跑。请稍后重试，或先结束归档。")
    if paused_ts > 0 and act["open_ts"] > paused_ts:
        return VERDICT_FOREIGN_POSITION, _foreign_text(act)
    return None


def _foreign_text(act: dict) -> str:
    """`foreign_position` 的统一文案（两个产生点共用，绝不各写一遍）。"""
    return ("当前持仓不是暂停时那一笔（交易所记录的持仓编号或建仓时间与暂停时不符）。"
            "继续会让 AI 接管一笔它从未开过的仓位，故拒绝续跑。"
            f"这笔{_side_cn(act['side'])} {act['size']:g} 张的持仓仍在交易所上，请到实盘面板自行处理。")


def _size_verdict(exp: dict, act: dict, size_tol: float) -> tuple[str, str]:
    """张数比对（身份与方向都已相符）：容差内 ⇒ matched；小于 ⇒ 部分平仓；大于 ⇒ 拒绝。"""
    delta = abs(act["size"] - exp["size"])
    # `+1e-9`：容差边界必须**含等号且不受浮点噪声影响**——`0.6 × 1.02` 在二进制下是
    # 0.6120000000000001，裸比会让一个「恰好 2%」的断面随机翻成 `size_increased` 并拒绝续跑。
    if exp["size"] > 0 and delta / exp["size"] <= max(0.0, size_tol) + 1e-9:
        return VERDICT_MATCHED, f"与暂停时一致（{_side_cn(exp['side'])} {act['size']:g} 张）。"
    if act["size"] < exp["size"]:
        return VERDICT_PARTIALLY_CLOSED, (
            f"离线期间该仓被部分平掉（{exp['size']:g} → {act['size']:g} 张）。"
            f"续跑将按交易所真值 {act['size']:g} 张接管；被平掉的那部分盈亏若未能从交易所回读，"
            "本段统计会缺这一笔。")
    return VERDICT_SIZE_INCREASED, (
        f"当前持仓比暂停时更大（{exp['size']:g} → {act['size']:g} 张），"
        "多出来的那部分不是本会话开出的，拒绝续跑。"
        "这笔持仓仍在交易所上，请到实盘面板自行处理，或选择结束归档。")


def _position_view(raw: dict | None) -> dict | None:
    """把状态表快照 / 交易所归一行压成同一个比对视图；无仓（含 size≤0）⇒ None。绝不抛。"""
    if not isinstance(raw, dict):
        return None
    side = str(raw.get("side") or _side_from_okx(raw))
    size = abs(_num(raw.get("size") if raw.get("size") is not None else raw.get("pos")))
    if side not in ("long", "short") or size <= 0:
        return None
    # `openCTime` 是状态表里的键名，`cTimeS` 是交易所归一行的键名——同一个事实的两个落点。
    open_ts = int(_num(raw.get("openCTime") if raw.get("openCTime") is not None
                       else raw.get("cTimeS")))
    return {"side": side, "size": size, "pos_id": str(raw.get("posId") or ""),
            "open_ts": open_ts}


def _side_from_okx(raw: dict) -> str:
    """OKX `posSide`（long/short/net）→ 本仓库立场；net 模式按 `pos` 符号定向。"""
    pos_side = str(raw.get("posSide") or "").lower()
    if pos_side in ("long", "short"):
        return pos_side
    pos = _num(raw.get("pos"))
    return "long" if pos > 0 else "short" if pos < 0 else ""


def _unknown_orders(inp: DriftInput) -> list[dict]:
    """该标的上「不是本会话下出的」阻断类挂单。

    **绝不靠 `clOrdId` 前缀判断**：`aragon` / `aragonc` 是整个应用共用的前缀（含手动下单面板），
    用前缀判会把用户自己挂的单当成「我们的遗留单」接管掉。唯一真源是状态表里的 `knownClOrdIds`。
    """
    known = inp.known_cl_ord_ids or frozenset()
    return [o for o in (inp.pending or [])
            if str(o.get("kind") or "") in _BLOCKING_ORDER_KINDS
            and str(o.get("clOrdId") or "") not in known]


def is_backstop_covered(wire: dict | None, min_coverage: float = 0.999) -> bool:
    """兜底是否**确定已覆盖**（`covered` 的唯一派生点）。

    `classify_backstop` 的 wire 里**没有** `covered` 这个键——它只给 `basis` / `coverageRatio`，
    而覆盖是「交易所口径 + 覆盖率达阈」两个条件的合取。判据与 `discipline_view.naked_backstop`
    的既有阈值同源（**不新开旋钮**，恒复用 `ai_trade_backstop_min_coverage`）。

    极性：`coverageRatio is None` 是「还不知道」而不是「已覆盖」——这里一律按**未覆盖**处置，
    因为本函数的两个消费者（暂停回执文案、续跑强制门禁）在不确定时都该说保守的那句话。
    """
    if not wire or str(wire.get("basis") or "") != "exchange":
        return False
    ratio = wire.get("coverageRatio")
    return ratio is not None and float(ratio) >= float(min_coverage)


def apply_backstop_requirement(verdict: str, backstop: dict | None, *,
                               required: bool) -> tuple[str, str | None]:
    """`ai_trade_resume_backstop_required` 的**唯一读点**（[P2-6]）：矩阵判完、组级 verdict 之前。

    默认 `False` ⇒ 恒直通（复核框里只出黄条警示），与本仓库「裸仓只告警不阻断」的既有极性一致。
    开启后：带仓且可接管的成员若兜底未覆盖（含 `unavailable`）⇒ 降为 blocked。
    """
    if not required or verdict not in (VERDICT_MATCHED, VERDICT_PARTIALLY_CLOSED):
        return verdict, None
    if bool((backstop or {}).get("covered")):
        return verdict, None       # `covered` 已由 §3.1.3 ⑥ 的落盘点派生（见 is_backstop_covered）
    # verdict 本身保持不变（它描述的是「持仓相对暂停时怎么了」这个事实）；阻断经 `blockedReason`
    # 表达，由 `group_verdict` 汇总——两件事分开，UI 才能同时说清「仓没变」与「但没有兜底」。
    return verdict, BACKSTOP_MISSING_CODE


def group_verdict(verdicts: list[str], blocked_reasons: list[str] | None = None) -> str:
    """整组 verdict：任一成员 blocked ⇒ **整组**拒绝（[P2-13]：续跑是整组原子的）。

    半组续跑会同时弄错预算切分、`GroupExposureBook` 的 `unit_notional` 与验收卡口径，
    而「成员级暂停」已被 §九.2 列为非目标——续跑侧必须与它同口径。
    """
    if any(reason for reason in (blocked_reasons or [])):
        return GROUP_BLOCKED
    if any(v in BLOCKED_VERDICTS for v in verdicts):
        return GROUP_BLOCKED
    if any(v in CONFIRM_VERDICTS for v in verdicts):
        return GROUP_NEEDS_CONFIRM
    return GROUP_READY


def is_age_exceeded(paused_ts: int, now_ts: int, max_age_s: float) -> bool:
    """暂停契约是否超龄（超龄只许归档）。`paused_ts<=0` / `max_age_s<=0` ⇒ 永不超龄。"""
    if paused_ts <= 0 or max_age_s <= 0:
        return False
    return (now_ts - paused_ts) > max_age_s


def describe_offline_span(seconds: int) -> str:
    """离线时长中文化（`3 小时 12 分`）。负数按 0 处理；< 1 分钟说「不到 1 分钟」。"""
    total = max(0, int(seconds))
    if total < 60:
        return "不到 1 分钟"
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    parts = [f"{days} 天" if days else "", f"{hours} 小时" if hours else "",
             f"{minutes} 分" if minutes else ""]
    return " ".join(p for p in parts if p) or "不到 1 分钟"


def build_pause_notice(members: list[dict], *, hold_position: bool,
                       flatten_on_stale: bool = False) -> str:
    """暂停回执的 `notice`（判定性文案单点，§3.10）：逐成员如实说明持仓与兜底覆盖。

    带仓而兜底未覆盖时必须把「此刻没有任何持久化兜底」说出来（复用 `exchange_backstop` 的既有
    措辞口径）；`flatten_on_stale=True`（运维显式开启）时追一句 [P2-7] 的条件说明——那条配置会让
    行情断流把一个「保留持仓的暂停」直接平掉并终结会话，用户有权在按下暂停之前就知道。

    「平仓后暂停」那一支**绝不无条件宣告已平掉**：`members` 里的持仓断面是 §3.1.3 ⑤ 在平仓
    **之后**向交易所读回的真值，仍读到仓（或读不到）就说明这一单没有全部成交。用户正要据这句话
    决定关不关电脑，一句「已平掉」的假话会让他带着一笔无人看管的真实仓位去睡觉。
    """
    if not hold_position:
        residual = [row for row in members or []
                    if row.get("positionUnknown")
                    or _position_view(row.get("position")) is not None]
        if not residual:
            return "已暂停，并已按你的选择平掉本会话的自管持仓。"
        # 纯文本，绝不用 `**粗体**`：本仓库的后端文案是直接进 JSX 的，markdown 会原样显示成星号。
        return ("已暂停，但平仓没有完成：" + " ".join(_residual_lines(residual))
                + " 请立即到下方实盘面板手动处理这些持仓。")
    lines: list[str] = []
    for row in members or []:
        inst = str(row.get("instId") or "")
        pos = _position_view(row.get("position"))
        if row.get("positionUnknown"):
            lines.append(f"{inst}：暂停时未能向交易所读到持仓，续跑前需要重新核实。")
            continue
        if pos is None:
            lines.append(f"{inst}：无持仓。")
            continue
        backstop = row.get("backstopAtPause") or {}
        if backstop.get("covered"):
            stop_px = backstop.get("stopPx")
            lines.append(f"{inst} 仍持有{_side_cn(pos['side'])} {pos['size']:g} 张；"
                         f"交易所侧止损 {stop_px} 已核实覆盖，软件关闭期间由它兜底。")
        else:
            lines.append(f"{inst} 仍持有{_side_cn(pos['side'])} {pos['size']:g} 张；"
                         "此刻没有任何持久化兜底——本进程是唯一的退出执行者，"
                         "建议先补挂交易所侧止损，或改选「平仓后暂停」。")
    notice = "已暂停：不再开新仓，但盯市、保护性平仓与交易所侧兜底核实照常运行。"
    if lines:
        notice = notice + " " + " ".join(lines)
    if flatten_on_stale:
        notice += "（注意：本机开启了「行情断流自动平仓」，暂停期间行情长时间断连会平掉持仓并结束会话。）"
    return notice


def build_resume_notice(members: list[dict], *, resumed_count: int) -> str:
    """续跑回执的 `notice`（与 `build_pause_notice` 同一处收口，§3.10）。

    **只说这一刻已经核实过的事**：`resume_group` 只有在 `_verify_armed` 通过之后才返回，
    故「重新武装完成」是有据的。持仓张数**一律不复述** —— 热续跑没有重新向交易所读过持仓，
    照抄本地投影等于在一句判定性文案里混进一个未经核实的数（诚实不变式 I-4：读不到 ≠ 没有，
    没核实过 ≠ 可以当成核实过）。

    不复述张数还不够：不带回执调用时 `_state_member_rows` 走 `session.state_member_row()`，
    那里 `positionUnknown` **恒为 `False`**、`position` 取的是 `executor` 的**自管投影** ——
    换言之点名「哪几个标的有持仓」同样是一句未经核实的断言。故第二句必须**自报数据来源**
    （「按本会话的自管投影」）并**自报未核实**（「本次继续未重新向交易所核实持仓」），
    两个短语由 B-2 机械冻结。冷续跑路径已由 `resume-preview` 做过交易所核实，两条路径共用同一
    措辞是**保守方向**（把已核实的说成未核实，最坏后果是用户多看一眼）。

    纯文本，绝不用 `**粗体**`：后端文案直接进 JSX，markdown 会原样显示成星号（本仓库已复发四次）。

    Args:
        members: `AiTradeManager._state_member_rows(group)` 的逐成员行（与暂停回执同一形状）。
        resumed_count: 后端权威的单调计数（每次 pause→resume 往返 +1）。

    Returns:
        一到三句中文；`members` 为空或全部无仓时只有第一句（与第三句可选追加）。
    """
    held = [str(row.get("instId") or "") for row in members or []
            if _position_view(row.get("position")) is not None]
    count = len(members or [])
    notice = (f"已继续：{count} 个标的重新武装完成，暂停期「不开新仓」的限制已解除。"
              if count else "已继续：暂停期「不开新仓」的限制已解除。")
    if held:
        notice += ("按本会话的自管投影，" + "、".join(held)
                   + " 仍记有持仓并已重新纳入盯市（本次继续未重新向交易所核实持仓）。")
    if resumed_count > 1:
        notice += f"（这是本会话第 {resumed_count} 次继续。）"
    return notice


def _residual_lines(residual: list[dict]) -> list[str]:
    """平仓未完成时逐成员如实描述残留（「读不到」与「没有」分开说，诚实不变式 I-4）。"""
    lines = []
    for row in residual:
        inst = str(row.get("instId") or "")
        pos = _position_view(row.get("position"))
        if pos is None:
            lines.append(f"{inst} 平仓后未能向交易所核实持仓，无法确认是否已平掉。")
        else:
            lines.append(f"{inst} 仍持有{_side_cn(pos['side'])} {pos['size']:g} 张。")
    return lines


def _side_cn(side: str) -> str:
    return {"long": "多头", "short": "空头"}.get(side, side or "无持仓")


def _num(value: object) -> float:
    """转有限浮点（非数值 / 非有限 ⇒ 0.0）；判定层的 NaN 比 0 危险得多。"""
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return out if out == out and out not in (float("inf"), float("-inf")) else 0.0
