// AI 自动交易「判定性提示」的纪元纯核心（ai-trade-notice-state-sync §4.1 ~ §4.3）。
//
// 用户报告的缺陷是「暂停 → 继续之后，那条暂停提示不消失」。它**不是**渲染时序问题：那条提示
// 在本轮之前是一个裸的 `string | null`，谁写谁负责清，而全仓五个写点里恰恰只有「继续」这条路径
// 没有写点。本模块把「这条提示此刻还成不成立」收敛成**一张规则表 + 一个纯谓词**，于是
// 「继续之后暂停提示消失」变成一条**结构上成立**的性质，而不是「某个写点记得去清」。
//
// 零 IO、零 React、绝不自取时钟（`nowMs` 一律由调用方注入，便于用例给定断面）。
// 模块方向恒为 `aiTradeTypes.ts`（叶） ← `aiTrade.ts` ← 本文件 ← `store.ts` / 组件，
// 两端各由源码门禁 G-N4 / G-N7 冻结。
import { resolveAiTradeLifecycle } from "./aiTrade";
import type {
  AiTradeGroupState,
  AiTradeNotice,
  AiTradeNoticeEpoch,
  AiTradeNoticeKind,
  AiTradeNoticeRule,
  AiTradeResumableGroup,
} from "./aiTradeTypes";

// 原地再导出：调用方只需 import 本模块一个（类型的真源仍在叶模块，见 §5.3）。
export type {
  AiTradeLifecycle,
  AiTradeNotice,
  AiTradeNoticeEpoch,
  AiTradeNoticeKind,
  AiTradeNoticeRule,
} from "./aiTradeTypes";

/**
 * 「本次操作结果未知」的结算窗（§4.3）。**不是保险丝，是竞态修复**：
 * `handleActionError` 铸造 `action_unknown` 的同时会 `requestAiTradeReconcile()`，那会让对账
 * effect 立刻补拉一次；那一次虽然出发在动作之后，问的却是一个**可能还在处理这次写请求**的后端。
 * 1.5 秒的窗把这一次排除掉，同时保证**下一次**（快窗口 3 秒后出发）必然结算。
 *
 * 与 `AI_TRADE_RECONCILE_FAST_MS = 3_000` 的关系必须恒为 `SETTLE < FAST`，由 U-4 的两条边界值冻结。
 */
export const AI_TRADE_NOTICE_SETTLE_MS = 1_500;

/** 「已继续」回执的存活时长：状态迁移在界面上是**换了一句话**，而不是「一句话凭空不见了」。 */
export const AI_TRADE_RESUMED_NOTICE_TTL_MS = 12_000;

/**
 * 七种提示的判据表（§4.2 / §4.6 / §4.8-1）。
 *
 * 写成 `satisfies Record<AiTradeNoticeKind, AiTradeNoticeRule>`：漏登记一种 kind 就**编译失败**
 * （G-2），同时保留字面量的窄类型以便呈现层直接取 `tone` / `glyph`。
 *
 * 三处**反直觉但必须如此**的取值，改动前请先读理由：
 * ① `ended` / `backend_ended` 的 `groupScoped` 必须是 false —— 结束那一刻 `aiTradeGroup` 被清成
 *    null，按 groupScoped 判，这条「以下持仓从此不再由任何自动化管理」的话会在**它最该出现的
 *    那一瞬间**被自己判死。
 * ② `action_unknown` 的 `validIn` 是空数组 —— 它的语义就是「我不知道现在是什么态」，拿一个自己
 *    都不确定的态去判自己的死活是循环论证。它只由对账落地来结算。
 * ③ `paused` 的 `resumeScoped` 不是多余的 —— `running → paused(第 2 次)` 时 `lifecycle` 又回到
 *    `"paused"`，没有这一位，**上一次**暂停的回执会跟着新暂停一起复活。
 */
export const AI_TRADE_NOTICE_RULES = {
  paused: {
    validIn: ["paused"], groupScoped: true, resumeScoped: true, settledByReconcile: false,
    ttlMs: null, sticky: true, persist: false,
    tone: "accent", glyph: "‖", urgent: false, dismissLabel: "知道了",
  },
  resumed: {
    validIn: ["running"], groupScoped: true, resumeScoped: true, settledByReconcile: false,
    ttlMs: AI_TRADE_RESUMED_NOTICE_TTL_MS, sticky: false, persist: false,
    tone: "long", glyph: "▶", urgent: false, dismissLabel: "关闭",
  },
  ended: {
    validIn: ["idle", "stopped"], groupScoped: false, resumeScoped: false,
    settledByReconcile: false, ttlMs: null, sticky: true, persist: true,
    tone: "short", glyph: "!", urgent: true, dismissLabel: "知道了",
  },
  backend_ended: {
    validIn: ["idle", "stopped"], groupScoped: false, resumeScoped: false,
    settledByReconcile: false, ttlMs: null, sticky: true, persist: true,
    tone: "short", glyph: "!", urgent: true, dismissLabel: "知道了",
  },
  action_unknown: {
    validIn: [], groupScoped: false, resumeScoped: false, settledByReconcile: true,
    ttlMs: null, sticky: false, persist: false,
    tone: "warn", glyph: "?", urgent: false, dismissLabel: "关闭",
  },
  member_status: {
    validIn: [], groupScoped: true, resumeScoped: true, settledByReconcile: false,
    ttlMs: null, sticky: false, persist: false,
    tone: "warn", glyph: "", urgent: false, dismissLabel: "关闭",
  },
  action_error: {
    validIn: [], groupScoped: true, resumeScoped: true, settledByReconcile: false,
    ttlMs: null, sticky: false, persist: false,
    tone: "short", glyph: "×", urgent: true, dismissLabel: "关闭",
  },
} satisfies Record<AiTradeNoticeKind, AiTradeNoticeRule>;

/**
 * 当前纪元（§4.1）。`groupId` **只取热分组**：冷占位卡没有「这一组的回执」这回事，
 * 拿它当组身份会让一条属于上一组的回执在冷卡出现时复活（U-9 冻结这条）。
 */
export function currentAiTradeEpoch(
  group: AiTradeGroupState | null | undefined,
  resumable: AiTradeResumableGroup | null | undefined,
  reconciledAtMs: number,
): AiTradeNoticeEpoch {
  return {
    groupId: group?.groupId ?? null,
    lifecycle: resolveAiTradeLifecycle(group, resumable),
    resumedCount: group?.resumedCount ?? null,
    reconciledAtMs: Number.isFinite(reconciledAtMs) ? reconciledAtMs : 0,
  };
}

/** 铸造：`text` 为空 / 全空白 ⇒ `null`（= 清空，不留一条空条）。 */
export function mintAiTradeNotice(
  kind: AiTradeNoticeKind,
  text: string | null | undefined,
  epoch: AiTradeNoticeEpoch,
  nowMs: number,
): AiTradeNotice | null {
  const body = (text ?? "").trim();
  if (!body) return null;
  return { kind, text: body, epoch, ts: nowMs };
}

/**
 * **唯一**的判活谓词（§4.3）：纯函数、零 IO、绝不抛。渲染点与 store 卫生共用它，不存在第二套判据。
 */
export function isAiTradeNoticeLive(
  notice: AiTradeNotice | null | undefined,
  now: AiTradeNoticeEpoch,
  nowMs: number,
): boolean {
  if (!notice) return false;
  const rule = AI_TRADE_NOTICE_RULES[notice.kind];
  if (!rule) return false;                                  // 未登记的 kind ⇒ 不渲染（U-6：不抛）
  if (rule.groupScoped && now.groupId !== notice.epoch.groupId) return false;
  if (rule.resumeScoped && now.resumedCount !== notice.epoch.resumedCount) return false;
  if (rule.validIn.length > 0 && !(rule.validIn as readonly string[]).includes(now.lifecycle)) {
    // 【未落地宽限，修 RP0-1】权威结论还没落地之前，不拿一份已知落后的本地投影去判一条刚铸出来
    // 的话的死活。`pause_group` 的回执不是 `group_status()` 形状，`pauseM.onSuccess` 因此不置本地
    // 态 —— 没有这一句，那条唯一会说出「平仓其实没成功」的话会在 0.3~3 秒内不渲染，最坏一次都不出现。
    // **`settled` 一旦为真本子句立刻失效**，`validIn` 恢复为硬判据（U-10 用成对边界值冻结：
    // 只写「宽限下判活」而不写「越过 notice.ts 后判死」，等于把宽限做成了永久豁免）。
    if (now.reconciledAtMs > notice.ts) return false;
  }
  if (rule.settledByReconcile && now.reconciledAtMs > notice.ts + AI_TRADE_NOTICE_SETTLE_MS) {
    return false;
  }
  if (rule.ttlMs != null && nowMs - notice.ts > rule.ttlMs) return false;
  return true;
}

/**
 * 铸造闸（§4.3 末段，修 RP1-6）：**非 sticky 不得驱逐一条仍判活的 sticky**。
 *
 * 单槽位真正会出事的不是 `ended` 撞 `resumed`（那两个确实互斥），而是 `action_unknown` 撞一条
 * **仍然成立**的 `paused`：用户在一条「已暂停，但平仓没有完成：…请立即到下方实盘面板手动处理
 * 这些持仓。」还挂着的时候点【继续】并超时 ⇒ 真钱待办被一句「本次操作结果未知」覆盖 ⇒ 3 秒后
 * 「结果未知」被对账结算掉 ⇒ **两句话一起消失**。
 *
 * 极性是刻意的：一句「我不知道现在是什么态」压不过一句「你有一笔仓没平掉、现在就去处理」。
 * 「结果未知」那条信息并不因此丢失 —— 动作在飞时按钮的「正在恢复…」与武装清单本来就在如实呈现
 * 进度，最终结论由徽标给出。反向不会误伤：`resumed` 铸造时本地态已被推到 `running`，那条 `paused`
 * sticky 此刻已判死，不在「仍判活」的保护范围内。
 */
export function isAiTradeNoticeBlockedBySticky(
  current: AiTradeNotice | null | undefined,
  incoming: AiTradeNoticeKind,
  now: AiTradeNoticeEpoch,
  nowMs: number,
): boolean {
  if (AI_TRADE_NOTICE_RULES[incoming]?.sticky) return false;
  if (!current || !AI_TRADE_NOTICE_RULES[current.kind]?.sticky) return false;
  return isAiTradeNoticeLive(current, now, nowMs);
}
