// 交易所侧兜底自愈的前端纯格式化（ai-trade-backstop-repair-and-net-backtest §2.4.2）。
//
// **本文件零计算**：状态、原因、张数、价格全部由后端下发，这里只做中文化与色调选择——镜像
// `aiTradeTurnover.ts` 的既有姿态（那条纪律的由来：前后端各存一份口径必然静默漂移）。
//
// 两条色彩纪律（沿用上一轮）：
// 1. **不用黄色**。黄色会被读成「快好了」，而本层的中间态语义是「还没有兜底」。
// 2. `planned` 用 `accent` 而不是红——「有方案待执行」不是坏消息，它是一个**可行动**的状态；
//    红色只留给确定的坏消息（`failed` 与 `px_through`）。
import type { AiTradeBackstopRepairWire, AiTradeRepairState } from "./aiTradeTypes";
import type { TurnoverRow, TurnoverTone } from "./aiTradeTurnover";

/**
 * 暂缓原因的中文标签。**值域与后端 `backstop_repair` 的常量逐条镜像**——缺一条，用户就会在
 * 卡片上看到一个原始英文码（上一轮已经踩过这个坑）。
 */
export const REPAIR_BLOCK_LABEL: Record<string, string> = {
  not_applicable: "此刻无需补挂",
  coverage_unknown: "交易所未回报可核对的委托张数",
  no_local_px: "本地没有可用的止损价",
  no_mark_px: "当前判定价不可用",
  px_stale: "判定价已过期，无法确认是否仍在止损有利侧",
  px_through: "现价已越过本地止损价",
  px_too_close: "止损价距离现价过近",
  size_below_min: "未覆盖差额不足最小下单量",
  order_in_flight: "有一笔限价委托在途",
  cooling: "两次补挂之间的冷却尚未结束",
  attempts_exhausted: "补挂次数已达上限",
  verify_failed: "已补挂但核实仍未覆盖",
};

/** 本行是否该给出一颗可点的按钮，以及它的文案（`null` ⇒ 不给按钮）。 */
export interface RepairAction {
  label: string;
  reason: AiTradeRepairState;
}

export interface RepairRow extends TurnoverRow {
  action: RepairAction | null;
}

/**
 * 运行卡的第五行；键缺席 / `idle` ⇒ `null` ⇒ **整行不渲染**。
 *
 * `placed` 一律说「已发出 · 核实中」而**不说「已补挂」**：OKX 回了一个 algoId 不等于那张单真的
 * 挂在那里（可能被风控立刻撤销、可能触发价被判非法而部分接受）。这条纪律与后端 prompt 那一句
 * 同源——不许说没核实过的话，包括系统自己刚做的事。
 */
export function describeRepair(wire?: AiTradeBackstopRepairWire | null): RepairRow | null {
  if (!wire || wire.state === "idle") return null;
  const px = wire.plannedStopPx == null ? "—" : trim(wire.plannedStopPx);
  const sz = wire.plannedSz == null ? "—" : trim(wire.plannedSz);
  const quota = `本周期 ${wire.attemptsEpoch}/${wire.maxPerEpoch} · 本会话 ${wire.attemptsSession}/${wire.maxPerSession}`;
  if (wire.state === "planned") {
    return row("方案就绪", "accent", `补挂止损 ${px} / ${sz} 张（${quota}）——等待你授权`, {
      label: "立即补挂",
      reason: "planned",
    });
  }
  if (wire.state === "placing" || wire.state === "placed") {
    return row("已发出 · 核实中", "accent",
      `已发出补挂止损 ${px} / ${sz} 张，正在向交易所核实是否真的挂上`, null);
  }
  if (wire.state === "verified") {
    return row("已补挂 · 已核实", "long",
      `${trimOrDash(wire.lastResult?.sz)} 张由系统补挂并已向交易所核实通过`, null);
  }
  if (wire.state === "failed") {
    // 读后端**预渲染**的中文标签，**绝不显示裸码**：归一码的值域由 `okx/trading.py` 拥有并随
    // 交易所错误码演进，在前端抄一张表，下一次新增码就会让卡片上出现一个原始英文串（这正是
    // `REPAIR_BLOCK_LABEL` 那份镜像表能成立、而本处不能的差别——那份值域是自愈层自己的）。
    const why = wire.lastResult?.codeLabel ?? wire.lastResult?.code ?? "未知原因";
    return row("补挂失败", "short", `交易所拒单（${why}）——当前仍无持久化兜底`, {
      label: "重试",
      reason: "failed",
    });
  }
  return blockedRow(wire);
}

/** `blocked` 的三档：`px_through` / `verify_failed` 是确定的坏消息（红），其余是「暂时不动手」。 */
function blockedRow(wire: AiTradeBackstopRepairWire): RepairRow {
  const reason = wire.blockedReason ?? "";
  const label = REPAIR_BLOCK_LABEL[reason] ?? reason ?? "未知原因";
  const severe = reason === "px_through" || reason === "verify_failed";
  const cooling =
    wire.coolingSeconds == null ? "" : `（冷却剩余 ${Math.round(wire.coolingSeconds)}s）`;
  // `px_through` 不给重试按钮：补一张会立刻触发的止损等于立刻市价平仓，重试只会更快地做错事。
  const action: RepairAction | null =
    reason === "px_through" || reason === "not_applicable"
      ? null
      : { label: "重试", reason: "blocked" };
  return row("已暂缓", severe ? "short" : "muted", `${label}${cooling}——当前仍无持久化兜底`,
    action);
}

function row(value: string, tone: TurnoverTone, detail: string,
             action: RepairAction | null): RepairRow {
  return { key: "repair", label: "兜底自愈", value, detail, tone, action };
}

function trimOrDash(value?: number | null): string {
  return value == null ? "—" : trim(value);
}

function trim(value: number): string {
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(6)));
}
