// 后训练轨迹采集的全部前端 wire 类型（ai-trade-trajectory-capture §5.4）。
//
// **住在本文件而不是 `aiTradeTypes.ts`**：后者已远超 CLAUDE.md 的 800 行阈值，本特性在那边
// 只加对这里的引用（沿用 `experienceTypes.ts` 的先例）。
//
// 口径：后端回执统一 camelCase；`schemaVersion` 只在改名 / 删字段时加一，新增字段不加版本
// ——读侧一律「缺键即缺席」。

/**
 * 会话级采集约定：启动请求体里的 `trajectoryCapture`，也是持久化偏好的形状。
 *
 * `directory === null` ⇒ 用服务端默认根目录。默认值随部署变化（桌面端在 userData 下、
 * 开发形态在 services/quant 下），故前端**不复制**它，只展示服务端下发的那一份。
 */
export interface AiTradeTrajectoryRule {
  enabled: boolean;
  directory: string | null;
}

/** 卡片默认值：**默认不勾**（需求原文「默认关闭」）。 */
export const AI_TRADE_TRAJECTORY_RULE_DEFAULT: AiTradeTrajectoryRule = {
  enabled: false,
  directory: null,
};

/** `GET /ai-trade/trajectory/config` 的回执（只读配置，不守 AI 总闸）。 */
export interface AiTradeTrajectoryConfig {
  /** 运维可用性总闸 `AI_TRADE_TRAJECTORY_ENABLED`。 */
  enabled: boolean;
  /** 服务端默认根目录的完整绝对路径（Windows 下原样反斜杠）。 */
  defaultRoot: string;
  minFreeBytes: number;
  segmentMaxBytes: number;
  compressSegments: boolean;
  schemaVersion: number;
  backtestSupported: boolean;
  thinkingCapturedProviders: string[];
}

/**
 * `POST /ai-trade/trajectory/validate` 的回执。**`ok=false` 也是 200**：校验结果是数据，
 * 不是错误；只有请求本身失败才会进 React Query 的 error 分支。
 */
export interface AiTradeTrajectoryDirVerdict {
  ok: boolean;
  /** 判定码（`TRAJECTORY_DIR_*`）；通过 ⇒ `null`。 */
  code: string | null;
  /** 后端下发的中文原因；通过 ⇒ `null`。 */
  message: string | null;
  /** 归一化后的目录字符串。 */
  directory: string;
  isDefault: boolean;
  exists: boolean;
  /** 读不到 ⇒ `null`（绝不补 0）。 */
  freeBytes: number | null;
  minFreeBytes: number;
}

/** 分组回执的状态机取值（§2.9）。 */
export type AiTradeTrajectoryState =
  | "off"
  | "recording"
  | "degraded"
  | "paused_low_disk"
  | "failed"
  | "closed";

/** 「服务端替我决定了什么」：请求 / 未请求 / 运维关 / 解析失败。 */
export type AiTradeTrajectorySource =
  | "request"
  | "not_requested"
  | "ops_off"
  | "resolve_failed";

/** 写线程计数器（形状恒定）。 */
export interface AiTradeTrajectoryCounters {
  records: number;
  llmCalls: number;
  rounds: number;
  bytesWritten: number;
  segments: number;
  dropped: number;
  writeErrors: number;
  serializeErrors: number;
  redactions: number;
  lateCalls: number;
  manifestWriteErrors: number;
}

/** 最近一次采集层错误（`message` 由后端下发中文）。 */
export interface AiTradeTrajectoryLastError {
  code: string;
  message: string;
  ts: number;
}

/**
 * 分组回执 `group_status().trajectoryCapture`（形状恒定；老后端 ⇒ 整键缺席）。
 *
 * ⚠️ `enabled` 与 `requested` 是两个键（【v2 · RV2】）：打开失败 / 写线程崩溃时
 * `enabled=false`、`requested=true`、`state="failed"`——恰是最需要被看见的状态。
 * 运行态状态条因此**只按 `requested` 渲染**，绝不按 `enabled`。
 */
export interface AiTradeTrajectoryReceipt {
  /** 本会话此刻是否真的在采集（生效值）。 */
  enabled: boolean;
  /** 用户请求了采集且未被运维关（状态条的渲染判据）。 */
  requested: boolean;
  source: AiTradeTrajectorySource;
  state: AiTradeTrajectoryState;
  /** 未启用 ⇒ `null`。 */
  root: string | null;
  /** 本分组的数据子目录；未启用 ⇒ `null`。 */
  directory: string | null;
  isDefaultRoot: boolean | null;
  counters: AiTradeTrajectoryCounters;
  pendingBytes: number;
  /** 最近一次检查值；从未检查 ⇒ `null`（绝不补 0）。 */
  freeBytes: number | null;
  lastError: AiTradeTrajectoryLastError | null;
  schemaVersion: number;
}
