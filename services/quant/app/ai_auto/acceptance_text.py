"""周期验收卡与资金费的中文渲染唯一真源（ai-trade-horizon-acceptance-loop §2.4 / §2.2）。

与纯核心 `horizon_acceptance.py` / `funding_ledger.py` 分离的理由和本仓库既有的
`cost_text.py` / `hold_horizon_text.py` 完全相同：判定是判定、文案是文案，混在一起会让「卡片
形状」的回归测试必须先造一个完整的会话运行时。

一条贯穿全模块的纪律：**任何一次降级都必须在文案里说出来**。「口径不可得」与「未跨结算点」
是两个结论、「样本不足」与「未通过」是两个结论——把它们渲染成同一句话（或同一种颜色），
就等于把 L1 的放行纪律作废。
"""
from __future__ import annotations

# 五行判据的中文标签（前端不复制任何一个，全部由后端下发）。
ROW_LABELS: dict[str, str] = {
    "avgHold": "平均持仓时间",
    "withinTargetRate": "落在目标区间的比例",
    "turnoverPerDay": "每日换手次数（墙钟）",
    "feeToGross": "手续费占毛利润",
    "netAfterFunding": "资金费后净收益",
}

# 十值退出来源的中文标签（镜像 `exit_attribution.EXIT_SOURCES`，G-9 双向覆盖断言守）。
EXIT_SOURCE_LABELS: dict[str, str] = {
    "agent": "Agent 主动平仓",
    "agentOverride": "Agent 最高优先级离场",
    "agentReverse": "Agent 反手",
    "profitGuard": "利润保护层",
    "exchangeOco": "交易所侧止盈止损",
    "timeExit": "强制时间平仓",
    "sessionStop": "会话级止盈损 / 停止",
    "windowEnd": "区间收尾强制平仓",
    "external": "外部 / 手动 / 强平",
    "unknown": "未记录来源",
}

# 第 3 行的固定硬上限（P2-3）：`hourly_swing` 的纯派生阈是 240 次/日，任何配置都不可能违反——
# 一个恒真的门槛比没有门槛更危险，故加一条「每小时一次往返已是物理极限」的兜底。
TURNOVER_HARD_CAP = 24.0

_UNAVAILABLE_REASONS: dict[str, str] = {
    "disabled": "未接入交易所结算读数（对账总闸关闭），资金费为本地计提值",
    "no_creds": "未接入交易所结算读数（模拟盘或无凭证会话），资金费为本地计提值",
    "not_run": "本会话尚未跑过一次交易所对账，资金费暂为本地计提值",
    "untrusted": "最近一次对账未通过可信判据（覆盖不齐或口径自检失败），资金费退回本地计提值",
    "truncated": "交易所结算样本已达单页上限（可能被截断），资金费退回本地计提值",
}


def format_hold_duration(seconds: float | None) -> str:
    """秒 → 人话时长（`35m 41s` / `12h` / `1d 6h`）；`None` ⇒ `"—"`。"""
    if seconds is None:
        return "—"
    total = int(max(0.0, float(seconds)))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m {total % 60}s" if total % 60 else f"{total // 60}m"
    if total < 86400:
        hours, rest = divmod(total, 3600)
        return f"{hours}h {rest // 60}m" if rest // 60 else f"{hours}h"
    days, rest = divmod(total, 86400)
    return f"{days}d {rest // 3600}h" if rest // 3600 else f"{days}d"


def format_value(unit: str, value: float | None) -> str:
    """按量纲渲染实测值；`None` ⇒ `"—"`（**绝不渲染成 0**）。"""
    if value is None:
        return "—"
    if unit == "seconds":
        return format_hold_duration(value)
    if unit == "ratio":
        return f"{float(value) * 100:.1f}%"
    if unit == "count":
        return f"{float(value):.1f} 次/日"
    if unit == "usd":
        return f"{float(value):+.2f} USDT"
    return f"{value}"


def format_target(key: str, threshold: float | None) -> str:
    """门槛列文案；阈值缺失 ⇒ 明说「未设目标区间」而不是留白。"""
    if key == "netAfterFunding":
        return "> 0"
    if threshold is None:
        return "未设目标区间"
    if key == "avgHold":
        return f"≥ {format_hold_duration(threshold)}"
    if key == "withinTargetRate":
        return f"≥ {float(threshold) * 100:.0f}%"
    if key == "turnoverPerDay":
        return f"≤ {float(threshold):.1f} 次/日"
    if key == "feeToGross":
        return f"≤ {float(threshold) * 100:.0f}%"
    return "—"


def avg_hold_note(truncated: int, has_target: bool) -> str | None:
    """第 1 行 note：截断样本披露（[P1-8]）+ 无目标区间时的原因。"""
    parts: list[str] = []
    if not has_target:
        parts.append("本会话未设目标持仓区间（自定义模式全默认），该行不构成门槛")
    if truncated > 0:
        parts.append(f"含 {truncated} 笔被区间 / 会话收尾截断的样本，实际持仓时长被低估"
                     "（刻意不剔除——剔除即只统计走完全程的持仓，会造幸存者偏差）")
    return "；".join(parts) if parts else None


def turnover_note(in_position_value: float | None, threshold: float | None,
                  derived: float | None, *, gate_armed: bool = False,
                  gate_vetoes: int = 0) -> str:
    """第 3 行 note：墙钟 vs 在场口径对照 + 弱门槛自认（P2-3）+ 运行期闸的自指披露（§2.2.6）。

    `gate_armed=False`（默认）时本函数产出**逐字节不变**——既有会话与既有冻结用例零回归。

    自指披露为什么是必须的：一旦 G5 在运行期执行 `turnoverPerDay <= 阈`，这一行就从「独立
    观测」退化为「执行结果」，**它会近乎恒真**。本仓库已经为「恒真的判据」栽过两次跟头。
    处置办法不是把闸关掉，而是说出来，并把用户的注意力指回第 1、4、5 行。

    Args:
        in_position_value: 在场口径换手率（仅供对照）。
        threshold: 已夹的判定阈。
        derived: 未夹的派生阈（弱门槛自认据它产出）。
        gate_armed: 本会话是否武装了 G5 换手预算闸。
        gate_vetoes: G5 实际拦下的开仓意图次数（**它本身就是一条需要看的读数**）。
    """
    parts = ["分母为会话在线时长（含空仓等待）——它与平均持仓时长互相独立，"
             "而在场口径与平均持仓时长互为倒数、不是独立读数"]
    if in_position_value is not None:
        parts.insert(0, f"在场口径 {float(in_position_value):.1f} 次/日，仅供对照")
    if (threshold is not None and derived is not None
            and derived > TURNOVER_HARD_CAP + 1e-9):
        parts.append(f"本模式按目标下界派生的阈值高达 {derived:.0f} 次/日，实质恒真，"
                     f"故该行由固定上限 {TURNOVER_HARD_CAP:.0f} 次/日 兜底，"
                     "不构成周期契约意义上的门槛")
    if gate_armed:
        cap = "" if threshold is None else f"**本会话**上限 {float(threshold):.1f} 次/日、"
        parts.append(f"⚠ 本会话开启了换手预算闸（{cap}会话重启后预算重新计算；"
                     f"实际拦下 {int(gate_vetoes)} 次），故本行接近恒真，"
                     "**请以第 1、4、5 行为准**；拦截次数越大说明模型仍在试图高频换手，"
                     "本身就是一条需要看的读数")
    return "；".join(parts)


def net_after_funding_note(net_pnl_usd: float, funding_cost_usd: float | None,
                           basis: str) -> str:
    """第 5 行 note：把减法算式写出来；口径不可得时明说是「没算成」而不是 0。"""
    if funding_cost_usd is None:
        return ("资金费口径不可得，未做减法——这与「资金费为 0」是两个结论，"
                "故本行判为样本不足而非通过")
    label = {"settled+accrued": "交易所结算 + 当前持仓计提",
             "accrued": "本地计提"}.get(basis, basis)
    return (f"= 净额 {net_pnl_usd:+.2f} − 资金费 {funding_cost_usd:.2f}"
            f"（{label} 口径）")


def fee_to_gross_note(abs_gross_usd: float) -> str | None:
    """第 4 行 note：口径词是**必须**的（与既有成本卡的「近 200 笔」口径并存，R-13）。"""
    if abs_gross_usd <= 0:
        return "尚无可用的毛利润样本，无法计算占比"
    return "全程累计口径（会话内全部往返），与成本卡上「近 200 笔」的滚动口径分开看"


def within_target_note(has_target: bool) -> str | None:
    if has_target:
        return None
    return "本会话未设目标持仓上界，没有靶子就没有命中率"


def sample_note(round_trips: int, sample_floor: int) -> str:
    """卡底样本进度（`insufficient` 时必须出现，否则灰卡看起来像坏了）。"""
    if round_trips >= sample_floor:
        return f"样本 {round_trips} 笔（已达下限 {sample_floor}）"
    return (f"样本 {round_trips} / {sample_floor} —— 未达样本下限，结论仅供参考；"
            "延长回测区间即可，**不得**下调样本下限来蒙混通过")


def funding_note(*, basis: str, settled_usd: float | None,
                 accrued_closed_usd: float, accrued_open_usd: float | None,
                 unavailable_reason: str | None = None) -> str:
    """资金费一句话口径说明（三态各一句，绝不含糊）。"""
    if basis == "unavailable":
        reason = _UNAVAILABLE_REASONS.get(str(unavailable_reason or ""), "")
        tail = f"；{reason}" if reason else ""
        return ("尚未取到可用的资金费读数（结算周期未知或快照持续陈旧），"
                f"本项按「口径不可得」呈现，绝不按 0 计{tail}")
    open_part = ("" if not accrued_open_usd
                 else f"，当前持仓另计提 {accrued_open_usd:.4f}")
    if basis == "settled+accrued":
        return (f"已按交易所结算口径计入 {float(settled_usd or 0.0):.4f} USDT"
                f"{open_part}。交易所口径覆盖该持仓的完整生命周期，"
                "可能含会话启动前的结算点")
    reason = _UNAVAILABLE_REASONS.get(str(unavailable_reason or ""), "")
    tail = f"；{reason}" if reason else ""
    return (f"本地计提 {accrued_closed_usd:.4f} USDT{open_part}"
            f"（已平仓段按逐结算点计提）{tail}")


def format_funding_accrued_for_prompt(view: dict | None) -> str:
    """每轮决策 prompt 的「本仓已实付资金费」一句（§2.2.5）；键缺席 ⇒ `""` ⇒ 整段省略。

    **后半句是必须的、不是装饰**：单独把一个随时间单调增长的成本摆在模型面前，会造出一条新的
    抢跑偏置（「越拿越贵，不如先跑」），恰好抵消上一轮 §2.9 的三处偏置矫正。措辞与利润保护宪章
    改后的口径一致：保护线执行退出同样正确，成本本身不构成离场理由。

    Args:
        view: `payload["costAwareness"]["fundingAccrued"]`（未装配 / 空仓 / 账本关 ⇒ 缺席）。

    Returns:
        单句文本，或 `""`。
    """
    if not view:
        return ""
    accrued = view.get("accruedOpenUsd")
    if accrued is None:
        return ""
    crossings = int(view.get("crossings") or 0)
    interval = view.get("intervalS")
    rate = view.get("lastRateBps")
    detail = [f"已跨 {crossings} 个结算点"]
    if interval:
        detail.append(f"当前结算周期 {float(interval) / 3600:g}h")
    else:
        detail.append("结算周期未知，故本项只记已确认跨过的结算点")
    if rate is not None:
        detail.append(f"最近费率 {float(rate):g} bps/期")
    return (f"本仓已实际计提资金费 {float(accrued):+.4f} USDT（{'、'.join(detail)}）。"
            "该项是持有的既定成本、已计入净额；**它本身不构成离场理由**——"
            "离场要另付两笔手续费，只有「持仓理由已失效」才是离场理由。")


def build_diagnostics(*, agent_share: float | None, fee_to_gross: float | None,
                      fee_threshold: float, checks: dict) -> list[str]:
    """三条冻结的派生诊断（纯函数、可单测）；条件不成立即整条不出现。

    Args:
        agent_share: `agent` 来源占全部退出的比例（无样本 ⇒ `None`）。
        fee_to_gross: 第 4 行实测值。
        fee_threshold: 第 4 行门槛。
        checks: `{"withinTargetPass": bool, "netAfterFunding": float | None,
                  "fundingBasis": str, "avgHoldSeconds": float | None}`。
    """
    out: list[str] = []
    if (agent_share is not None and agent_share > 0.8
            and fee_to_gross is not None and fee_to_gross > fee_threshold):
        out.append(f"Agent 主动平仓占 {agent_share * 100:.1f}%，且手续费已吃掉 "
                   f"{fee_to_gross * 100:.0f}% 的毛利润 —— 抢跑退出仍占主导。")
    net = checks.get("netAfterFunding")
    if checks.get("withinTargetPass") and net is not None and float(net) <= 0:
        out.append("持仓周期已达标但扣完全部成本仍然亏损 —— 问题在方向判断，"
                   "不在换手频率；继续拉长持仓不会改善这一项。")
    avg_hold = checks.get("avgHoldSeconds")
    if (checks.get("fundingBasis") == "unavailable" and avg_hold is not None
            and float(avg_hold) >= 4 * 3600):
        out.append("平均持仓已跨过资金费结算点，但资金费口径不可得 —— "
                   "此处的净额偏乐观，不可据此放大实盘资金。")
    out.extend(exit_override_diagnostics(checks.get("exitOverride")))
    return out


def exit_override_diagnostics(row: dict | None) -> list[str]:
    """最高优先级离场的**事后净额**那一句（exit-priority X-9 的兑现点）；零笔 ⇒ `[]`。

    「Agent 用掉了几次最高优先级、其中几次事后证明是对的」必须是一个能查的数——否则这条通道
    无法被证伪，也就无法被信任。这一句是它在验收卡上的唯一落点，故**措辞一律由净额符号派生**
    （沿用 `describe_exit_outcome` 的既有纪律：硬编码「效果良好」在净额为负时就是一句假话）。
    """
    if not isinstance(row, dict) or int(row.get("count") or 0) <= 0:
        return []
    count = int(row["count"])
    net = float(row.get("netUsd") or 0.0)
    win_rate = row.get("winRate")
    verdict = "净额为正" if net > 0 else ("净额为负" if net < 0 else "净额持平")
    win_text = "" if win_rate is None else f"、胜率 {float(win_rate) * 100:.0f}%"
    return [f"最高优先级离场共 {count} 笔，合计 {verdict}（{net:+.2f} USDT{win_text}）"
            f"—— 这是「Agent 的终局判断是否值得信任」的唯一可核对读数。"]
