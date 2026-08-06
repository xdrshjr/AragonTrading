"""交易成本的中文文案唯一真源（ai-trade-cost-truth-and-net-pnl 轴 D · D-1，镜像 `sizing_text.py`）。

**渲染与判定同源**：prompt 里写出来的每一个 bps、每一个平衡价，都由本模块从 `CostRuntime` 产出的
**同一份** `CostBreakdown` 渲染，杜绝「prompt 说 8 bps、门禁按 12 bps 判」的信任崩塌（A-6）。任一键
缺席即整句省略——不假数据、不写「当前无 xx」的噪音。

为什么模型必须看得见成本：在本特性之前，决策 prompt 与系统 prompt **一个字都没提成本**（F-13），
而每一笔往返在真实账户上都要付掉双边手续费。模型于是在一个「开平不要钱」的世界里做决策，只会在
事后被一个它看不见的确定性门禁否决。软引导（本模块）与硬门禁（`edge_gate`）读同一份成本读数，
分工见 §3.4 D-6。
"""
from __future__ import annotations

# 本模块**不出回测免责文案**：轴 D 只接实盘决策链（`backtest.py` 不在 §四 变更计划内，回测的系统
# prompt 与 payload 里根本没有成本证据）。既有的 `_EDGE_GATE_BACKTEST_NOTE` / `SIZING_BACKTEST_NOTE`
# 之所以存在，是因为回测结果里**真的有**对应读数需要被免责；为一份不存在的读数写免责，就是在告诉
# 用户系统做了一件它没做的事（终审 RV-2）。回测若将来要接轴 D，免责文案与接线一并加。


def _pct_text(bps: object) -> str:
    """bps → 百分数短语（`12.5 bps` ⇒ `0.125%`）；不可数 ⇒ `"—"`。"""
    try:
        return f"{float(bps) / 100:.3f}%"          # 1 bps = 0.01%
    except (TypeError, ValueError):
        return "—"


def _fee_source_text(view: dict) -> str:
    """费率来源子句：官方档位 vs 本地保守常数（**绝不含糊**——数从哪来决定它可不可信）。"""
    maker, taker = view.get("feeOpenBps"), view.get("feeCloseBps")
    level = view.get("level") or ""
    if view.get("source") == "exchange":
        tier = f"档位 {level}，" if level else ""
        return f"费率取自交易所官方接口（{tier}开仓腿 {maker} / 平仓腿 {taker} bps）"
    return (f"费率取自本地保守常数（未取到交易所官方档位；开仓腿 {maker} / 平仓腿 {taker} bps），"
            "真实档位可能更高或更低")


def format_cost_charter(view: dict | None) -> str:
    """系统 prompt 的成本宪章章；`view` 为 `None` ⇒ `""` ⇒ 系统 prompt 逐字节回退（零回归）。

    Args:
        view: `CostRuntime.cost_view()` 产物（宪章章与每轮 `payload["costAwareness"]` 同一份视图）。

    Returns:
        宪章章文本（多行），或 `""`。
    """
    if not view:
        return ""
    total = view.get("roundTripBps")
    lines = ["【交易成本 · 系统确定性扣减】"]
    lines.append(
        f"· 本会话每笔往返的确定性成本约 **{total} bps**（≈{_pct_text(total)}）："
        f"开仓腿 {view.get('feeOpenBps')} + 平仓腿 {view.get('feeCloseBps')} bps 手续费，"
        f"再加 {view.get('crossingLegs')} 条吃价差腿的价差与冲击"
        f"（半价差 {view.get('halfSpreadBps')} + 冲击 {view.get('impactBps')} bps）"
        f"{_funding_clause(view)}。{_fee_source_text(view)}。")
    lines.append(
        f"· 折算到价格：**价格必须朝你的方向走过约 {_pct_text(total)} 你才回本**"
        "——这个距离**与杠杆无关**（杠杆只放大保证金收益率，不改变回本所需的价格距离）。"
        "反手（先平后开）要付两倍。")
    lines.append(_basis_line(bool(view.get("netBasis"))))
    lines.append(
        "· 频繁进出会把成本乘以次数：每一次「再看看」的开平都在真实扣钱。"
        "只有当你预期的价格空间**明显大于**上面这个往返成本时，这笔交易才值得做。")
    return "\n".join(lines)


def _funding_clause(view: dict) -> str:
    """资金费子句；缺数据（`None`）⇒ 如实说明未计入，**绝不按 0 计**。"""
    carry = view.get("fundingCarryBps")
    if carry is None:
        return "（资金费率暂未取到，未计入——真实成本只会更高）"
    return f"，另含预估资金费 {carry} bps" if carry else ""


def _basis_line(net_basis: bool) -> str:
    """盈亏口径声明：模型必须知道它看到的那个盈亏数字是毛额还是净额。"""
    if net_basis:
        return ("· 你看到的「会话累计盈亏」**已扣除**手续费（净额口径）；持仓浮盈同样按"
                "「此刻平掉能拿到手的钱」呈现，不是交易所标记价上的账面浮盈。")
    return ("· 你看到的「会话累计盈亏」是**毛额**口径（**未扣**手续费）：真实到手金额比它低"
            "约「往返成本 × 已完成往返笔数」，请自行折算后再判断。")


def format_cost_for_prompt(view: dict | None) -> str:
    """每轮决策 prompt 的成本段；`view` 缺席 ⇒ `""` ⇒ 整段省略（零回归）。

    Args:
        view: 本轮 `payload["costAwareness"]`（总闸关时该键**整键缺席**）。

    Returns:
        单行文本（子句间以「；」相接），或 `""`。
    """
    if not view:
        return ""
    total = view.get("roundTripBps")
    parts = [f"本轮往返成本≈{total} bps（≈{_pct_text(total)}，"
             f"按{_leg_text(view.get('previewOrdType'))}腿、{_side_text(view.get('previewSide'))}"
             f"方向估算）"]
    parts.extend(_break_even_parts(view))
    share = view.get("atrShare")
    if share is not None:                       # ATR 不可用 ⇒ 该子句省略而非写「未知」
        parts.append(f"成本约占当根 ATR 的 {float(share) * 100:.0f}%")
    fee_usd = view.get("sessionFeeUsd")
    if fee_usd is not None:
        parts.append(f"本会话累计已付手续费 {float(fee_usd):.4f} USDT")
    return "【交易成本】" + "；".join(parts) + "。"


def _break_even_parts(view: dict) -> list[str]:
    """持仓中的平衡价子句（无持仓 ⇒ 两键为 `None` ⇒ 空表）。"""
    px = view.get("breakEvenPx")
    if px is None:
        return ["当前空仓：新开一笔即需先赚回上述成本才回到原点"]
    gap = view.get("breakEvenGapPct")
    out = [f"当前持仓的盈亏平衡价≈{px}"]
    if gap is not None:
        out.append("现价已越过平衡价（这笔已覆盖成本）" if float(gap) <= 0
                   else f"距平衡价还差 {float(gap) * 100:.3f}%")
    return out


def _leg_text(ord_type: object) -> str:
    return "限价" if ord_type == "limit" else "市价"


def _side_text(side: object) -> str:
    return "多头" if side == "long" else "空头"
