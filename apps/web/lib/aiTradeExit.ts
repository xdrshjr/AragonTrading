/**
 * 离场优先级与离场意图兑现的**呈现层**（ai-trade-exit-priority-and-commitment 轴 D）。
 *
 * 纪律（X-8，沿用 `aiTradeHorizon.ts` / `hold_horizon_text` 的成文教训）：**本文件不重算任何一个
 * 数、不硬编任何一个阈值**。档位、降档原因、解闸集合、配额、尝试次数全部来自后端 wire；这里只做
 * 「英文枚举 → 中文标签 + 徽标色」的映射，以及把已经算好的数拼成一句人话。
 *
 * 一张前后端各存一份的表必然漂移——这是本仓库反复出事的形态（`edgeSource` 逐键手抄吞新键）。
 * 唯一允许留在前端的是**标签与配色**（它们不是判定口径，不会造成读数分叉）。
 */
import type { AiTraceTone } from "./aiTrade";
import type { AiTradeExitCommitmentWire, AiTradeExitPriorityWire } from "./aiTradeTypes";

/** 五档的中文标签（与后端 `hold_horizon_text._LEVEL_LABEL` 镜像；后端 wire 只发枚举值）。 */
const LEVEL_LABEL: Record<string, string> = {
  none: "不适用",
  normal: "P1 · 常规",
  expedite: "P2 · 加速",
  adjudicated: "P3 · 复核官背书",
  override: "P3 · 最高优先级",
};

/**
 * 档位徽标色。**放行一律用 accent、绝不用红色**：本层是 fail-open 的，用红色会让用户以为被拦住
 * 了——这与 `describeHorizonGate` 对「限制已解除」的取色是同一条纪律。
 */
const LEVEL_TONE: Record<string, AiTraceTone> = {
  none: "muted",
  normal: "muted",
  expedite: "accent",
  adjudicated: "accent",
  override: "accent",
};

/** 四种降档原因的中文（与后端 `_DOWNGRADE_LABEL` 镜像）。 */
const DOWNGRADE_LABEL: Record<string, string> = {
  evidence_missing: "未给出可核对的新证据",
  ledger_short: "跨轮离场账本还不够厚",
  quota_exhausted: "本会话最高优先级配额已用尽",
  min_hold_floor: "距开仓不足绝对地板时长",
};

/** 三道闸的人话（轨迹里 `releasedGates` 只有 G1/G2/G3 三个裸码）。 */
const GATE_LABEL: Record<string, string> = {
  G1: "最短持仓",
  G2: "离场连续确认",
  G3: "换手迟滞",
};

export interface AiExitPriorityView {
  /** 徽标文案，如「P2 · 加速（已解除 离场连续确认）」。 */
  label: string;
  tone: AiTraceTone;
  /** 账本与配额那一行；无可说时为 `null`（整行省略，不写「暂无」这种噪音）。 */
  detail: string | null;
  /** 降档 / 挂起的解释；未降档且未挂起 ⇒ `null`。 */
  note: string | null;
}

/**
 * 离场优先级中文化（轨迹段与面板共用的单点）。
 *
 * 未装配 / 本轮非平仓（`level` 缺席或为 `none`）⇒ `null` ⇒ 组件整段不渲染（老会话与老快照零影响）。
 */
export function describeExitPriority(
  wire?: AiTradeExitPriorityWire | null,
): AiExitPriorityView | null {
  if (!wire || !wire.level || wire.level === "none") return null;
  const gates = (wire.releasedGates ?? []).map((g) => GATE_LABEL[g] ?? g);
  const label =
    gates.length > 0
      ? `${LEVEL_LABEL[wire.level] ?? wire.level}（已解除 ${gates.join(" / ")}）`
      : (LEVEL_LABEL[wire.level] ?? wire.level);
  return {
    label,
    tone: LEVEL_TONE[wire.level] ?? "muted",
    detail: buildLedgerDetail(wire),
    note: buildPriorityNote(wire),
  };
}

/** 账本进度 + 配额余量（两个数都来自 wire；`intentLapses` 为 0 时不提衰减，避免噪音）。 */
function buildLedgerDetail(wire: AiTradeExitPriorityWire): string | null {
  const parts: string[] = [];
  if (wire.intentStreak != null) {
    const lapses =
      wire.intentLapses != null && wire.intentLapses > 0
        ? `（其间 ${wire.intentLapses} 轮未提离场，已按衰减计入）`
        : "";
    parts.push(`离场账本 ${wire.intentStreak} 轮${lapses}`);
  }
  if (wire.overridesUsed != null && wire.maxOverridesPerSession != null) {
    parts.push(`最高优先级已用 ${wire.overridesUsed}/${wire.maxOverridesPerSession} 次`);
  }
  return parts.length > 0 ? parts.join(" ｜ ") : null;
}

/**
 * 降档 / 挂起的解释。**降档轮必须写清原因**——「你声明了 final，但因为 X 本轮按 high 处理」是
 * 用户（和模型）理解这一轮为什么没走成的唯一依据；静默降档是本轮要根除的那类不诚实。
 */
function buildPriorityNote(wire: AiTradeExitPriorityWire): string | null {
  const parts: string[] = [];
  if (wire.downgradeReason) {
    const why = DOWNGRADE_LABEL[wire.downgradeReason] ?? wire.downgradeReason;
    parts.push(`声明了 ${wire.declared ?? "final"}，但${why}，本轮按 high 处理`);
  }
  if (wire.pending) {
    parts.push("本轮已挂起，交由独立复核官裁决");
  }
  if (wire.failOpenReason) {
    parts.push("本笔离场未经复核官复核（复核链路故障，按放行处理）");
  }
  return parts.length > 0 ? parts.join("；") : null;
}

export interface AiExitCommitmentView {
  label: string;
  tone: AiTraceTone;
  detail: string;
  /** 进度条比例 0~1（`attempts / maxAttempts`）；`stuck` 时恒为 1。 */
  progress: number;
  stuck: boolean;
}

/**
 * 离场兑现三态（§4.6.3）：未武装 ⇒ `null` ⇒ 整块不渲染；`stuck` ⇒ danger + 【立即离场】按钮。
 *
 * 「正在兑现」用 accent 而不是 danger：那是系统正在替用户做正确的事，不是一个错误。
 */
export function describeExitCommitment(
  wire?: AiTradeExitCommitmentWire | null,
): AiExitCommitmentView | null {
  if (!wire || !wire.armed) return null;
  const attempts = wire.attempts ?? 0;
  const maxAttempts = Math.max(1, wire.maxAttempts ?? 1);
  const age = formatExitAge(wire.ageSeconds);
  if (wire.stuck) {
    return {
      label: "离场受阻",
      tone: "short",
      detail: `已尝试 ${attempts} 次仍未成交（持续 ${age}），已停止自动重试；交易所侧止损止盈仍生效`,
      progress: 1,
      stuck: true,
    };
  }
  return {
    label: "离场已确认，正在兑现",
    tone: "accent",
    detail: `第 ${attempts + 1}/${maxAttempts} 次尝试 · 已 ${age}`,
    progress: Math.min(1, attempts / maxAttempts),
    stuck: false,
  };
}

/** 秒 → 人话（与 `formatHoldDuration` 分开：这里的量级恒在分钟内，秒级精度才有意义）。 */
export function formatExitAge(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m${s % 60 ? ` ${s % 60}s` : ""}` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

/**
 * 【立即离场】二次确认弹窗的三句话（§4.6.3）。
 *
 * 三句缺一不可：①它会真的花钱平仓；②它**不是**【停止】——会话继续跑；③交易所侧的保护腿会被
 * 一并撤销（平仓即撤单）。少说第 ③ 句，用户会以为「先手动平掉、止损还留着」——那是一个不存在的
 * 状态，而它恰好是最危险的一种误解。
 */
export const MANUAL_EXIT_CONFIRM_LINES: readonly string[] = [
  "这会按市价平掉本会话的自管持仓，立即成交、价格不可控。",
  "会话将继续运行（不同于【停止】）：Agent 之后仍可能重新开仓。",
  "交易所侧的止损止盈会随平仓一并撤销（平仓即撤单）。",
];

export const MANUAL_EXIT_TITLE = "立即离场";
export const MANUAL_EXIT_CONFIRM_TEXT = "确认离场";
export const MANUAL_EXIT_CANCEL_TEXT = "再想想";

/**
 * 「提议被拦下」那一行（决策工具卡片的保真行，F-6 + directional-symmetry B-3）。
 *
 * 作用域已从 `horizon` 泛化到八道确定性门禁，故层名**必须吃后端下发的 `layerLabel`**。
 * 保留 `vetoedBy === "horizon"` 那条映射**只是为了老快照**（本特性合入前落的 trace 里没有
 * `layerLabel`）。
 *
 * ⚠️ **绝不允许回退到 `vetoedBy ?? "门禁"`**：泛化之后 `vetoedBy` 会是 `consensus` /
 * `baseRuleMaBand` 这样的英文 key，那条旧分支会把它们原样怼到用户脸上——而本轮的主张恰恰是
 * 「让方向被拦这件事对用户可读」。
 */
export function describeVetoedProposal(
  proposedAction?: string | null,
  vetoedBy?: string | null,
  layerLabel?: string | null,
): string | null {
  if (!proposedAction) return null;
  const by = layerLabel ?? (vetoedBy === "horizon" ? "交易周期层" : "门禁");
  return `模型提议的是「${ACTION_LABEL[proposedAction] ?? proposedAction}」，被${by}降级`;
}

/** 动作字面量 → 中文（只覆盖本模块用得到的四个；未知值原样显示，绝不静默吞掉）。 */
const ACTION_LABEL: Record<string, string> = {
  open_long: "开多",
  open_short: "开空",
  close: "平仓",
  hold: "观望",
  cancel_order: "撤单",
};
