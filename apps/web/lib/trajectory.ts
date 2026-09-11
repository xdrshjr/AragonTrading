/**
 * 后训练轨迹采集的**纯函数层**（ai-trade-trajectory-capture §2.13 / §2.14）。
 *
 * 不读 state、不发请求、不 import 任何组件。文案全部前端自有（后端下发的只有目录校验与
 * 采集层错误的 `message`），故集中在这里，由 `trajectory.test.ts` 逐分支冻结。
 */
import type { AiTradeLaunchMode } from "./aiTradeLaunchSteps";
import type {
  AiTradeTrajectoryConfig,
  AiTradeTrajectoryDirVerdict,
  AiTradeTrajectoryReceipt,
  AiTradeTrajectoryRule,
  AiTradeTrajectoryState,
} from "./trajectoryTypes";

/** 与 `aiTradeLaunchSteps.ts` 的摘要同一个分隔符。 */
const DOT = " · ";

const BYTE_UNITS = ["KB", "MB", "GB", "TB"] as const;

/** 前端自有文案（逐字由 `trajectory.test.ts` 冻结）。 */
export const TRAJECTORY_COPY = {
  configPending: "正在读取采集配置…",
  configFailed: "无法读取采集配置（后端尚未就绪），本次不会采集",
  opsOff: "后训练数据采集已被运维关闭，本次不会采集",
  dirUnavailable: "目录不可用",
  checking: "正在检查目录…",
  checkFailed: "暂时无法检查该目录，启动时会再检查一次",
  newSubdir: "本次启动会新建子目录「日期-分组号」",
  backtest: "回测不采集后训练数据（只采集实盘会话）",
  tradeUnaffected: "交易不受影响",
} as const;

/**
 * 体积格式化。读不到（`null` / `undefined` / 非有限 / 负数）⇒ **`null`**，由调用方整段省略。
 *
 * ⚠️ 必须先判 `null` 再参与运算：`Number(null) === 0`，不先判就会把「没这个读数」显示成
 * 「0 B」——可用空间显示 0 B，等于告诉用户盘已经满了。
 */
export function formatBytes(bytes: number | null | undefined): string | null {
  if (bytes === null || bytes === undefined) return null;
  if (!Number.isFinite(bytes) || bytes < 0) return null;
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  let scaled = bytes / 1024;
  let unit = 0;
  while (scaled >= 1024 && unit < BYTE_UNITS.length - 1) {
    scaled /= 1024;
    unit += 1;
  }
  return `${scaled.toFixed(1)} ${BYTE_UNITS[unit]}`;
}

/**
 * 后端下发文案的纯文本化：去掉 Markdown 强调符与反引号。
 *
 * JSX 文本节点是纯文本，强调符会原样显示成星号——本仓已在后端文案上复发四次，而前端
 * 源码门禁看不见后端字面量，故在唯一的渲染入口上兜一次。
 */
export function plainText(text: string | null | undefined): string {
  return (text ?? "").replace(/\*\*/g, "").replace(/`/g, "").trim();
}

/** `useQuery` 结果里本层用得到的三个字段（纯函数不依赖 React Query 类型）。 */
export interface TrajectoryConfigView {
  isPending: boolean;
  isError: boolean;
  data?: AiTradeTrajectoryConfig;
}

/**
 * 配置层面「为什么开不了」；配置可用 ⇒ `null`。
 *
 * 判定序**先看数据、再看错误**：React Query 在一次后台重取失败时会同时给出旧 `data` 与
 * `isError`。先判 `isError` 会让「实际在采（`effectiveEnabled` 读的是 `data`）」与「说本次
 * 不会采」同屏出现——那正是【v2 · RV3】要消灭的判据分叉。
 */
export function trajectoryConfigBlocker(
  config: TrajectoryConfigView,
): string | null {
  if (config.data?.enabled === true) return null;
  if (config.data?.enabled === false) return TRAJECTORY_COPY.opsOff;
  if (config.isError) return TRAJECTORY_COPY.configFailed;
  return TRAJECTORY_COPY.configPending;
}

export interface TrajectoryNoticeInput {
  mode: AiTradeLaunchMode;
  rule: AiTradeTrajectoryRule;
  config: TrajectoryConfigView;
}

/**
 * 「用户勾了、本次却不会采」的说明（【v2 · RV3】）；与 `effectiveEnabled` 互斥。
 *
 * 它是**说明不是阻断**：配置读不到时启动照常放行、只是整键不发——「不确定一律不翻译成
 * 否决」（`aiTradeLaunchRisk.ts` 的成文纪律）。
 */
export function trajectoryNotice(input: TrajectoryNoticeInput): string | null {
  if (input.mode !== "live" || !input.rule.enabled) return null;
  return trajectoryConfigBlocker(input.config);
}

export interface TrajectoryDirErrorInput {
  active: boolean;
  verdict: AiTradeTrajectoryDirVerdict | null;
}

/**
 * 目录阻断错误；不阻断 ⇒ `null`。
 *
 * **请求在途 / 请求失败 ⇒ 不阻断**：权威校验在服务端 `/ai-trade/start`（422 + 两条出路），
 * 前端只把一份**明确的** `ok=false` 翻译成阻断。空原因回落通用句——空串会让 `ready` 的
 * `!error` 判真，等于把一份 `ok=false` 放行。
 */
export function trajectoryDirError(input: TrajectoryDirErrorInput): string | null {
  const { active, verdict } = input;
  if (!active || verdict == null || verdict.ok) return null;
  return plainText(verdict.message) || TRAJECTORY_COPY.dirUnavailable;
}

/**
 * 桌面端 `openPath` 的拒绝码 → 中文原因（主进程 `dir-access.js` 的三个码）；系统错误文本原样透传。
 *
 * 用 `switch` 而不查普通对象：`reason` 来自 IPC，查表会命中 `toString` 这类原型键。
 */
export function describeOpenPathFailure(reason: string): string {
  switch (reason) {
    case "not_found":
      return "目录不存在（可能已被移动或删除）";
    case "not_directory":
      return "该路径不是文件夹";
    case "invalid_path":
      return "路径无效";
    default:
      return reason;
  }
}

export type TrajectoryVerdictKind = "checking" | "unknown" | "bad" | "ok";

export interface TrajectoryVerdictLine {
  kind: TrajectoryVerdictKind;
  text: string;
}

export interface TrajectoryVerdictInput {
  isFetching: boolean;
  isError: boolean;
  verdict: AiTradeTrajectoryDirVerdict | undefined;
}

/** 卡片校验行（`null` = 还没有任何可说的）。可用空间读不到 ⇒ 整段省略，绝不显示 0 B。 */
export function describeVerdictLine(
  input: TrajectoryVerdictInput,
): TrajectoryVerdictLine | null {
  if (input.isFetching) return { kind: "checking", text: TRAJECTORY_COPY.checking };
  if (input.isError) return { kind: "unknown", text: TRAJECTORY_COPY.checkFailed };
  const verdict = input.verdict;
  if (!verdict) return null;
  if (!verdict.ok) {
    const reason = plainText(verdict.message) || TRAJECTORY_COPY.dirUnavailable;
    return { kind: "bad", text: `⚠ ${reason}` };
  }
  const free = formatBytes(verdict.freeBytes);
  const parts = ["✓ 可写", free && `可用 ${free}`, TRAJECTORY_COPY.newSubdir];
  return { kind: "ok", text: parts.filter(Boolean).join(DOT) };
}

/** 状态条语气。颜色只是第三条通道：形状（`glyph`）+ 文字才承载语义。 */
export type TrajectoryTone = "accent" | "muted" | "warn" | "danger";

/** 语气 → 文字色 token（只用既有墨色；面色只作边框与底色）。 */
export const TRAJECTORY_TONE_COLOR: Record<TrajectoryTone, string> = {
  accent: "var(--accent-ink)",
  muted: "var(--muted)",
  warn: "var(--warn)",
  danger: "var(--short-ink)",
};

export interface TrajectoryReceiptView {
  tone: TrajectoryTone;
  glyph: string;
  text: string;
}

type ReceiptBuilder = (
  receipt: AiTradeTrajectoryReceipt,
  paused: boolean,
) => TrajectoryReceiptView;

const UNAFFECTED = `${DOT}${TRAJECTORY_COPY.tradeUnaffected}`;

function describeRecording(
  receipt: AiTradeTrajectoryReceipt,
  paused: boolean,
): TrajectoryReceiptView {
  const records = receipt.counters.records;
  if (paused) {
    const text = `数据采集待命（交易已暂停）· ${records} 条`;
    return { tone: "muted", glyph: "●", text };
  }
  const bytes = formatBytes(receipt.counters.bytesWritten);
  const parts = ["数据采集中", `${records} 条`, bytes];
  return { tone: "accent", glyph: "●", text: parts.filter(Boolean).join(DOT) };
}

function describeFailed(receipt: AiTradeTrajectoryReceipt): TrajectoryReceiptView {
  const reason = plainText(receipt.lastError?.message) || "原因未知";
  const text = `数据采集已停止：${reason}${UNAFFECTED}`;
  return { tone: "danger", glyph: "⚠", text };
}

const RECEIPT_BUILDERS: Record<AiTradeTrajectoryState, ReceiptBuilder> = {
  off: () => ({ tone: "muted", glyph: "—", text: "数据采集未在运行" }),
  recording: describeRecording,
  degraded: (receipt) => ({
    tone: "warn",
    glyph: "△",
    text:
      `数据采集有写入失败（${receipt.counters.writeErrors} 次），仍在继续` +
      UNAFFECTED,
  }),
  paused_low_disk: () => ({
    tone: "warn",
    glyph: "△",
    text: `数据采集已暂停：可用空间低于下限${UNAFFECTED}`,
  }),
  failed: describeFailed,
  closed: (receipt) => ({
    tone: "muted",
    glyph: "—",
    text: `数据采集已结束${DOT}${receipt.counters.records} 条`,
  }),
};

/** 后端将来加了新状态而前端没跟上：如实说出状态名，而不是冒充成某个已知状态。 */
function describeUnknownState(
  receipt: AiTradeTrajectoryReceipt,
): TrajectoryReceiptView {
  return { tone: "muted", glyph: "—", text: `数据采集状态：${receipt.state}` };
}

/**
 * 运行态状态条的文案（§2.14.2 的表）。`dropped > 0` ⇒ 末尾追加「· 缺口 N 条」。
 *
 * @param receipt 分组回执（调用方已按 `requested` 判过要不要渲染）。
 * @param paused 分组是否已暂停（`recording` 在暂停期说「待命」而不是「采集中」）。
 */
export function describeTrajectoryReceipt(
  receipt: AiTradeTrajectoryReceipt,
  paused: boolean,
): TrajectoryReceiptView {
  const build = RECEIPT_BUILDERS[receipt.state] ?? describeUnknownState;
  const view = build(receipt, paused);
  const dropped = receipt.counters.dropped;
  if (!(dropped > 0)) return view;
  return { ...view, text: `${view.text}${DOT}缺口 ${dropped} 条` };
}
