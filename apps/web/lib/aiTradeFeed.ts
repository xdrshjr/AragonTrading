/**
 * 行情断连自愈的**呈现层**（ai-trade-feed-self-healing 轴 D）。
 *
 * 纪律（沿用 `aiTradeExit.ts` / `aiTradeHorizon.ts` 的成文教训）：**本文件不重算任何一个数、不硬编
 * 任何一个阈值**。重连次数、补取次数、倒计时、行情年龄、以及那句判定性文案全部来自后端 wire；
 * 这里只做「枚举 → 配色 + 按钮集合」的映射，以及把已经算好的串拼上标的前缀。
 *
 * 一张前后端各存一份的表必然漂移——这是本仓库反复出事的形态（`edgeSource` 逐键手抄吞新键）。
 */
import type { AiTradeFeedRecovery, AiTradeGroupMemberState } from "./aiTradeTypes";

export interface AiTradeFeedRecoveryView {
  instId: string;
  /** 多标的时才带标的前缀（单标的分组里那个前缀是纯噪音，复用 `NotArmedBanner` 的既有规则）。 */
  label: string;
  stuck: boolean;
  /**
   * 倒计时秒数；`null` ⇒ **整行不渲染倒计时**。
   *
   * 绝不对着一个不会到来的时刻读秒——与 `aiTrade.test.ts` 冻结的那条既有规则同极性。
   */
  nextAttemptInS: number | null;
}

/**
 * 单成员的恢复卡视图；不该渲染时返回 `null`。
 *
 * 三档入参**表现一致但语义不同**（见 `AiTradeMemberLifecycle.feedRecovery` 的注释）：
 * `undefined`（后端没上线）与 `null`（行情正常）都不渲染；`state === "healthy"` 同样不渲染
 * （防御性——后端在那一档下发的是 `null`，但值域里它存在）。
 *
 * 视图模型**只留组件真的会读的四个字段**：重连 / 补取次数、行情年龄、以及「当前 K 线由 REST 补取
 * 维持」那一句，全部已由后端 `feed_watchdog.describe` 拼进 `recoveryText`，在这里再抄一份就会变成
 * 一个只写不读的字段——那正是本仓库反复出事的「算了但没人看」形状。
 *
 * @param wire 成员的 `lifecycle.feedRecovery`。
 * @param instId 标的。
 * @param membersTotal 分组成员总数（决定要不要带标的前缀）。
 */
export function describeFeedRecovery(
  wire: AiTradeFeedRecovery | null | undefined,
  instId: string,
  membersTotal: number,
): AiTradeFeedRecoveryView | null {
  if (!wire || wire.state === "healthy") return null;
  const text = wire.recoveryText ?? "";
  if (!text) return null;
  return {
    instId,
    label: membersTotal > 1 ? `${instId}：${text}` : text,
    stuck: wire.state === "stuck",
    nextAttemptInS: wire.nextAttemptInS ?? null,
  };
}

/**
 * 整组的恢复卡行集合（组件内部据它 `.map()`）。
 *
 * `feedRecovery` 是**成员级**的（一组可以有 5 个），而恢复卡的挂载位是**分组级**上下文，故入参
 * 必须是 `members` 数组、由组件自己逐行渲染：挂一个只吃单成员的卡在那里无法表达 5 个标的，而在
 * `AiTradePanel.tsx` 里补一段 `.map()` 会直接顶穿 CLAUDE.md 的 800 行红线。
 *
 * 全部成员都不该渲染 ⇒ 空数组 ⇒ 整个卡不渲染（连外框都不渲染，不占位——常态多一行只会稀释真正
 * 要紧的信息）。
 */
export function describeFeedRecoveryRows(
  members: AiTradeGroupMemberState[],
): AiTradeFeedRecoveryView[] {
  const total = members.length;
  return members
    .map((m) => describeFeedRecovery(m.session?.lifecycle?.feedRecovery, m.instId, total))
    .filter((v): v is AiTradeFeedRecoveryView => v !== null);
}

/** 任一成员处于 `stuck` ⇒ 整卡按 danger 取色（最坏的那一行决定整卡的语气）。 */
export function isFeedRecoveryStuck(rows: AiTradeFeedRecoveryView[]): boolean {
  return rows.some((r) => r.stuck);
}

export const FEED_RELINK_TITLE = "立即重连";
export const FEED_RELINK_PENDING_TEXT = "正在重连…";
export const FEED_STOP_MEMBER_TEXT = "仅停止该标的";
