// OKX 链路状态的**纯展示模型**（okx-link-resilience §2.5.3）——无 React、无 IO、可单测。
//
// 设计立场：**状态不是一个 tooltip 能承载的东西**。旧的 `ConnDot` 把三条链路压成一个
// 「已连接 / 不可达」，且只有 hover 才看得到时延——用户在断线时最想知道的三件事
// （断的是哪一路、断了多久、系统正在做什么）它一件也答不了。
//
// **状态中文的唯一口径在本文件**（`STATE_LABEL` / `KIND_LABEL`），组件不另抄字符串：
// 本仓库已两次因为「中文活在注释里 / 前端另抄一份」而在用户界面上显示原始英文码。

import { relativeTime } from "./discoveryHealth";
import type { OkxConnectivity, OkxLinkChannel, OkxLinkKind, OkxLinkState } from "./okxLinkTypes";

export type Tone = "long" | "short" | "warn" | "neutral";

/** 面板行里的状态中文。`idle` 说「空闲」——把闲的说成坏的，和把坏的说成好的一样是说谎。 */
export const STATE_LABEL: Record<OkxLinkState, string> = {
  up: "正常",
  idle: "空闲",
  degraded: "连接不稳",
  down: "已断开",
  unknown: "探测中",
};

export const KIND_LABEL: Record<OkxLinkKind, string> = {
  restPublic: "行情接口",
  restPrivate: "账户接口",
  wsBusiness: "实时流(K 线)",
  wsPublic: "实时流(盘口)",
};

/**
 * 顶栏芯片的文案。`idle` **不另造一句**——顶栏是给人扫一眼的，「空闲」这种技术细节属于面板
 * 里的那一行，把它升到芯片上只会制造第五种需要被解释的颜色。
 */
export const CHIP_LABEL: Record<OkxLinkState, string> = {
  up: "OKX 已连接",
  idle: "OKX 已连接",
  degraded: "连接不稳",
  down: "OKX 不可达",
  unknown: "探测中",
};

const TONE: Record<OkxLinkState, Tone> = {
  up: "long",
  idle: "long",
  degraded: "warn",
  down: "short",
  unknown: "neutral",
};

export function stateTone(state: OkxLinkState): Tone {
  return TONE[state] ?? "neutral";
}

/**
 * 语义色 → `Badge` 的 tone 值域。`Badge` 没有 `warn`，「连接不稳」借用 `accent`（强调色）——
 * 映射只在这里有一份，组件不各自 `tone === "warn" ? ... : ...`。
 */
export function badgeTone(tone: Tone): "neutral" | "long" | "short" | "accent" {
  return tone === "warn" ? "accent" : tone;
}

export function stateLabel(state: OkxLinkState): string {
  return STATE_LABEL[state] ?? STATE_LABEL.unknown;
}

export function chipLabel(state: OkxLinkState): string {
  return CHIP_LABEL[state] ?? CHIP_LABEL.unknown;
}

export function channelLabel(channel: OkxLinkChannel): string {
  return channel.label || KIND_LABEL[channel.kind] || channel.key;
}

/**
 * 芯片视图。**端点缺失时回落到既有 `okxUp` 二元展示**，绝不白屏——老后端 / 端点 404 下
 * 顶栏仍要能用。
 */
export interface ChipView {
  state: OkxLinkState;
  label: string;
  tone: Tone;
  /** 回落态：面板没有内容可展开，点击只是无害地开合。 */
  degradedToLegacy: boolean;
}

export function chipView(
  connectivity: OkxConnectivity | null | undefined,
  okxUp: boolean | null,
): ChipView {
  if (!connectivity) {
    const state: OkxLinkState = okxUp === null ? "unknown" : okxUp ? "up" : "down";
    return { state, label: chipLabel(state), tone: stateTone(state), degradedToLegacy: true };
  }
  const state = connectivity.overall;
  return { state, label: chipLabel(state), tone: stateTone(state), degradedToLegacy: false };
}

/** 面板里的一行。相对时间复用 `discoveryHealth.relativeTime`，**不另写第二份**。 */
export interface ChannelRow {
  key: string;
  label: string;
  state: OkxLinkState;
  stateText: string;
  tone: Tone;
  /** 右侧那一列：健康时说时延 / 最近数据，异常时说「已断多久」。 */
  detail: string;
}

export function channelRows(
  connectivity: OkxConnectivity | null | undefined,
  now?: number,
): ChannelRow[] {
  if (!connectivity) return [];
  return connectivity.channels.map((channel) => ({
    key: channel.key,
    label: channelLabel(channel),
    state: channel.state,
    stateText: stateLabel(channel.state),
    tone: stateTone(channel.state),
    detail: channelDetail(channel, now),
  }));
}

function channelDetail(channel: OkxLinkChannel, now?: number): string {
  const seen = channel.lastMessageTs ?? channel.lastOkTs;
  if (channel.state === "degraded" || channel.state === "down") {
    // 「断了多久」是断线时用户最想知道的三件事之一，而它过去在界面上根本没有落点。
    return seen ? `最后数据 ${relativeTime(seen, now)}` : "尚未连通";
  }
  if (channel.latencyMs != null) return `${channel.latencyMs}ms · ${relativeTime(seen, now)}`;
  return seen ? relativeTime(seen, now) : "等待数据";
}

/** 页脚那一行：1 小时内重连 / 补根的滚动计数（用计数而非逐事件流水，避免重连风暴刷屏）。 */
export function activitySummary(connectivity: OkxConnectivity | null | undefined): string {
  if (!connectivity) return "";
  const reconnects = sumBy(connectivity.channels, (c) => c.reconnects1h);
  const repairs = sumBy(connectivity.channels, (c) => c.gapRepairs1h);
  const parts: string[] = [];
  if (reconnects > 0) parts.push(`1 小时内重连 ${reconnects} 次`);
  if (repairs > 0) parts.push(`补齐 K 线 ${repairs} 次`);
  return parts.length ? parts.join(" · ") : "1 小时内无重连";
}

/** 最近一次错误（取时间最新的那条），用于面板中段。已由后端脱敏并截断。 */
export interface LastError {
  ts: number;
  code: string;
  message: string;
}

export function lastError(connectivity: OkxConnectivity | null | undefined): LastError | null {
  if (!connectivity) return null;
  const withError = connectivity.channels.filter((c) => c.lastErrorTs && c.lastErrorMessage);
  if (!withError.length) return null;
  const worst = withError.reduce((a, b) => ((b.lastErrorTs ?? 0) > (a.lastErrorTs ?? 0) ? b : a));
  return {
    ts: worst.lastErrorTs as number,
    code: worst.lastErrorCode ?? "",
    message: worst.lastErrorMessage as string,
  };
}

/** 账户面单独一行的文案；`unknown`（没配实盘）时返回 null——不给用户一个不存在的问题。 */
export function accountLine(connectivity: OkxConnectivity | null | undefined): string | null {
  if (!connectivity || connectivity.accountState === "unknown") return null;
  return `账户接口 ${stateLabel(connectivity.accountState)}`;
}

/** 本机与交易所的时钟偏差；|偏差| < 2s 视为无须展示（签名容忍是 ±30s）。 */
export function clockLine(connectivity: OkxConnectivity | null | undefined): string | null {
  const offset = connectivity?.clockOffsetMs;
  if (offset == null || Math.abs(offset) < 2000) return null;
  const seconds = (offset / 1000).toFixed(1);
  return `本机时间比交易所${offset > 0 ? "慢" : "快"} ${Math.abs(Number(seconds))} 秒（已自动补偿）`;
}

function sumBy<T>(rows: T[], pick: (row: T) => number): number {
  return rows.reduce((total, row) => total + (pick(row) || 0), 0);
}
