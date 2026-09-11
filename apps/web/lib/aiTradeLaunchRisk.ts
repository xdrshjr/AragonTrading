/**
 * AI 自动交易「引导式启动」的**风险汇聚**（ai-trade-guided-launch §4.1.2 / §4.2.5）。
 *
 * ── 本文件存在的唯一理由：不变式 I-1「风险零丢失」──────────────────────
 * 分步化有一个天然的、必须被设计掉的副作用：**藏起来的警示等于消失的警示**。今天那些非阻断
 * 提示之所以还起作用，是因为它们和启动按钮在同一屏；分步之后，一个从未翻到第 5 步的用户就
 * 再也看不到第 5 步的警示。故：**任何一条在某一步上产生的非阻断警示，必须在最后一步的风险
 * 清单里再出现一次**；反过来，最后一步不得出现任何不来自某个具体步骤的警示。
 *
 * ── I-1 的作用域是一张显式清单，不是一句「任何一条」（【v2 · P1-3】）────────
 * 等值门禁只能证明「注册表里的都到了第 6 步」，证明不了「弹窗里所有非阻断警示都在注册表里」
 * ——后者没有任何机械判据能覆盖（新警示可以出现在任何一个子组件的任何一行 JSX 里）。故边界
 * 写死在这里：
 *
 * **在册的 10 条**：`AI_TRADE_LAUNCH_RISK_IDS`，走 W-13 / W-14 / W-23 的等值判据。
 *
 * **已知排除项（本轮明确不进风险清单，各附理由）**：
 *  · `AiTradeHorizonFields` 的「契约摘要」四行 —— 它是**当前选择的说明**，不是警示；进了清单
 *    会把一份必然出现的常规文案永久钉在最后一屏，把真警示淹掉。
 *  · `AiTradeExperienceFields` 的「只读模式」提示（`value.write === false`）—— 它是用户**主动
 *    选择的一个正常档位**的回执，`--muted` 色，与 `experienceOff` 的 `--warn` 是两个不同的东西。
 *  · `AiTradeIntelFields` / `AiTradeDirectionFields` 内的档位说明文案 —— 同上，说明而非警示。
 *  · `AiTradeTrajectoryFields` 的目录与空间说明（ai-trade-trajectory-capture §2.13.3）—— 说明而非
 *    警示；目录不可用是**阻断错误**，走步骤模型（`trajectoryDirError`）而非风险清单。
 *  · 第 6 步的 `ai-launch-trajectory-notice` 行（`var(--warn)`，「勾了采集、本次却不会采」）——
 *    说明而非警示：配置接口一次瞬时失败不该在清单里钉一条风险（不确定一律不翻译成否决）。
 *
 * **实施纪律**：往任一 `Step*.tsx` 或其子组件里新增一处 `var(--warn)` 文本时，必须同时给出
 * 「进注册表」或「进上面这张排除清单」的判断。这条不由门禁守（守不住），由本注释写明——
 * 宁可把边界写死在文档里，也不要留一条自称全覆盖、实际只覆盖自己想得起来那几项的不变式。
 */
import type { AiTradeHorizonProfile, AiTradeHorizonProfileId } from "@/lib/aiTradeTypes";
import type { AiTradeLaunchMode, AiTradeLaunchStepId } from "@/lib/aiTradeLaunchSteps";

export const AI_TRADE_LAUNCH_RISK_IDS = [
  "gearResolution", // 仓位档位过粗 ⇒ 三层风控的缩减被量化吸附抹掉
  "tradabilityInfeasible", // 可交易性预检判定当前周期几乎开不出单
  "profitGuardOff", // 利润保护未装配（tone 随 unguardedHorizon() 升为 danger）
  "inertThresholds", // 纪律预设里那一行硬止盈「显示了但不作数」
  "allocationOversubscribed", // 独立上限模式下各标的上限合计 > 100%
  "budgetClamped", // 填写的总预算被账户级上限夹回
  "membersTruncated", // 左侧配置的标的数超过上限，已截断
  "deepWakeTooShort", // 「极致」研判档 × 唤醒周期 < 6 分钟
  // 【v2 · P1-3】以下两条是评审在源码里搜出来的、v1 漏掉的既有非阻断警示。它们各自住在一个
  // **用户最可能整步跳过**的步骤里（第 4 步 / 第 2 步），恰恰是 I-1 最该覆盖的那两条。
  "experienceOff", // 经验模式整关：本次会话既不读取也不沉淀
  "wakeFloorRaised", // 唤醒周期低于与目标持仓相干的下限，启动时会被服务端抬上去
] as const;

export type AiTradeLaunchRiskId = (typeof AI_TRADE_LAUNCH_RISK_IDS)[number];

export interface AiTradeLaunchRisk {
  id: AiTradeLaunchRiskId;
  /** 渲染落点；`steps[0]` 同时是风险清单上「去处理」按钮的跳转目标。 */
  steps: AiTradeLaunchStepId[];
  tone: "warn" | "danger";
  title: string;
  detail: string;
  /** 需要一次显式勾选才能启动（仅 `tradabilityInfeasible` 与互锁武装时的 `profitGuardOff`）。 */
  requiresAck?: boolean;
}

/**
 * 「装了持仓周期锁却没有利润侧钥匙」——与后端 `discipline.unguarded_horizon` **逐字同构**。
 *
 * 两侧同值由 U-5 的真值表用例钉死（表格逐行照抄 `test_g_a9_unguarded_horizon_truth_table_is_frozen`）。
 * `interlockArmed` 取自 wire 上的 `requireProfitExitWithHorizon`，**从不**取自「本轮默认是 false」
 * 这句话——否则显式配置过该键的环境会出现「前端放行、后端 422」的假门禁。
 */
export function unguardedHorizon(input: {
  profitGuardEnabled: boolean;
  horizonEnabled: boolean;
  effectiveMinHoldSeconds: number;
  interlockArmed: boolean;
}): boolean {
  return (
    input.interlockArmed &&
    !input.profitGuardEnabled &&
    input.horizonEnabled &&
    input.effectiveMinHoldSeconds > 0
  );
}

export interface EffectiveMinHoldInput {
  /** 预设表是否已落地（`horizonQ.isPending` / 读取失败 ⇒ `false`）。 */
  profilesReady: boolean;
  profileId: AiTradeHorizonProfileId;
  profiles: readonly AiTradeHorizonProfile[];
  /** `custom` 模式下用户填的「最短持仓（秒）」原始串；**空串 ⇒ 0**（安全退出口）。 */
  customMinHoldRaw: string;
}

/**
 * 本次会话真正会生效的「最短持仓（秒）」——`unguardedHorizon()` 判据的另一半。
 *
 * 三条纪律：
 *  1. 非 `custom` 模式取**服务端下发值**，前端不复制任何一个数（`?? 0` 不是防御性写法而是类型
 *     事实：`profile_wire` 对 `target_hold_min_s is None` 的条目下发 `null`，漏了它红的是 `tsc`
 *     而不是行为，【v2 · P2-2】）。
 *  2. `custom` 模式取用户输入，**空串 ⇒ 0**（与服务端「custom 预设 min_hold_s=0」逐字一致）。
 *  3. 预设表尚未落地 ⇒ **先于模式分支**返回 `0` ⇒ 不阻断。这条判定必须排在 `custom` 分支之前：
 *     `horizonProfileId` 在预设表落地之前会回落成字面量 `"custom"`，先分支就会去读一个与服务端
 *     无关的用户输入。**不确定一律不翻译成否决**，这是本仓库四次「永不开单」事故之后的成文纪律。
 */
export function effectiveMinHoldSeconds(input: EffectiveMinHoldInput): number {
  if (!input.profilesReady) return 0;
  if (input.profileId === "custom") {
    const raw = input.customMinHoldRaw.trim();
    if (!raw) return 0;
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? n : 0;
  }
  const hit = input.profiles.find((p) => p.id === input.profileId);
  return hit?.minHoldSeconds ?? 0;
}

export interface AiTradeLaunchRiskInput {
  mode: AiTradeLaunchMode;
  /** 档位分辨率警示原文（`null` ⇒ 该档位上本就没什么可说的）。 */
  gearNote: string | null;
  /** 可交易性预检的四档判定；`null` = 算不出 / 未取到（一律不阻断）。 */
  tradabilityLevel: string | null;
  /** 预检的一句话结论（后端下发，前端一个字都不重写）。 */
  tradabilityHeadline: string | null;
  profitGuardEnabled: boolean;
  horizonEnabled: boolean;
  effectiveMinHoldSeconds: number;
  /** wire 上的 `requireProfitExitWithHorizon`（键缺席 ⇒ 调用方按 `false` 传）。 */
  interlockArmed: boolean;
  /** 纪律预设里那一行硬止盈的**后端下发原文**；`null` ⇒ 该预设没有这一行。 */
  inertHardTakeProfitText: string | null;
  /** 独立上限模式下各标的上限之和（百分数）；仅 `max_ratio` 模式下 > 100 才是风险。 */
  allocationOversubscribedPct: number | null;
  /** 总预算被账户级上限夹回时的说明原文；`null` ⇒ 未夹紧。 */
  budgetClampText: string | null;
  /** 左侧配置的标的数（超过上限时才传，`null` ⇒ 未截断）。 */
  truncatedFrom: number | null;
  maxSymbols: number;
  reasoningDepth: string;
  wakeMinutes: number | null;
  experienceEnabled: boolean;
  /** wire 上的 `wakeHorizonDivisor`；`0` ⇒ 服务端没开这条抬升 ⇒ **恒不触发**。 */
  wakeFloorDivisor: number;
  /** 当前模式的目标最短持仓（秒，服务端下发）。 */
  targetHoldMinSeconds: number;
}

/** 「极致」研判档单轮的分钟数下界（与 `AiTradeLaunchModal` 既有提示逐字同源）。 */
const DEEP_WAKE_FLOOR_MINUTES = 6;

/**
 * 汇聚本次启动的全部非阻断风险。**只会产出 `AI_TRADE_LAUNCH_RISK_IDS` 中的 id**（U-4）。
 *
 * 顺序即优先级：需要一次显式勾选的排在最前，其余按「后果严重度 → 产地步骤」排。
 */
export function collectRisks(input: AiTradeLaunchRiskInput): AiTradeLaunchRisk[] {
  const risks: AiTradeLaunchRisk[] = [];

  if (input.tradabilityLevel === "infeasible") {
    risks.push({
      id: "tradabilityInfeasible",
      steps: ["guards"],
      tone: "danger",
      title: "可交易性预检：当前周期几乎开不出单",
      detail:
        input.tradabilityHeadline ??
        "当前标的 / 周期 / 纪律阈值下，绝大多数开仓意图会被最小 edge 门禁否决。",
      requiresAck: true,
    });
  }

  if (!input.profitGuardEnabled) {
    const fatal = unguardedHorizon({
      profitGuardEnabled: input.profitGuardEnabled,
      horizonEnabled: input.horizonEnabled,
      effectiveMinHoldSeconds: input.effectiveMinHoldSeconds,
      interlockArmed: input.interlockArmed,
    });
    // 极性注意：本条的渲染条件是「利润保护未勾选」**本身**，不是 `unguardedHorizon()`——即便
    // 没有周期锁，关掉利润保护也值得在最后一步说一句。`unguardedHorizon()` 只决定它的 tone
    // 与是否需要勾选。
    const requiresAck = fatal && input.mode === "live";
    const base =
      "关闭后，回吐棘轮、保本线、峰值浮盈读数与保护性平仓冷却在本次会话中全部不生效：" +
      "浮盈没有任何自动兑现通道，只能靠 Agent 主动平仓或交易所侧 OCO 止盈。";
    const horizonClause =
      input.horizonEnabled && input.effectiveMinHoldSeconds > 0
        ? "而本次还装了持仓周期锁（最短持仓 " +
          `${Math.round(input.effectiveMinHoldSeconds / 60)} 分钟），` +
          "该窗口内只有亏损侧豁免、没有利润侧对手盘——锁被装上了，钥匙被拔走了。"
        : "";
    // 【v2 · P1-7】互锁武装时必须说出「是谁在拦你」：`interlockArmed === true` 只可能来自运维侧
    // 的显式配置，而本轮的产品承诺是「默认放行」，两者相遇时用户看到的是一个与发行说明矛盾的
    // 二次确认框。
    const opsClause = requiresAck
      ? "本机构已开启运维互锁 AI_TRADE_REQUIRE_PROFIT_EXIT_WITH_HORIZON，" +
        "未勾选下方确认时服务端会拒绝启动。"
      : "";
    risks.push({
      id: "profitGuardOff",
      steps: ["guards"],
      tone: fatal ? "danger" : "warn",
      title: "利润保护层未装配",
      detail: [base, horizonClause, opsClause].filter(Boolean).join(""),
      ...(requiresAck ? { requiresAck: true } : {}),
    });
  }

  if (!input.profitGuardEnabled && input.inertHardTakeProfitText) {
    risks.push({
      id: "inertThresholds",
      steps: ["guards"],
      tone: "warn",
      title: "纪律预设里有一行阈值显示了但不作数",
      detail:
        `${input.inertHardTakeProfitText}——该项由利润保护层执行，` +
        "本次会话关闭了保护层，因此它逐字节不生效。",
    });
  }

  if (input.gearNote) {
    risks.push({
      id: "gearResolution",
      steps: ["capital"],
      tone: "warn",
      title: "仓位档位过粗",
      detail: input.gearNote,
    });
  }

  if (input.budgetClampText) {
    risks.push({
      id: "budgetClamped",
      steps: ["capital"],
      tone: "warn",
      title: "总预算已被账户级上限夹回",
      detail: input.budgetClampText,
    });
  }

  // 分配器**只在实盘段渲染**（回测是单标的）：`members` / `allocationMode` 在切到回测之后仍然
  // 留在表单态里，不带这条模式判据就会在一次单标的回测上说「各标的上限合计 200%」，而第 1 步
  // 连那张表都不渲染——「去处理」按钮把用户送到一个什么都改不了的步骤，正是 P0-3 要消灭的死路。
  if (input.mode === "live" && input.allocationOversubscribedPct != null) {
    risks.push({
      id: "allocationOversubscribed",
      steps: ["scope"],
      tone: "warn",
      title: "各标的上限合计超过 100%",
      detail:
        `独立上限模式下各标的上限合计 ${input.allocationOversubscribedPct}%：多标的同时触发时` +
        "组合敞口可能超过总预算。真实敞口仍受总预算上限、单笔名义上限与单仓 OCO 约束。",
    });
  }

  if (input.truncatedFrom != null) {
    risks.push({
      id: "membersTruncated",
      steps: ["scope"],
      tone: "warn",
      title: "标的数超过上限，已截断",
      detail:
        `左侧已配置 ${input.truncatedFrom} 个标的，超过 AI 自动交易上限 ${input.maxSymbols}，` +
        `仅前 ${input.maxSymbols} 个入选（可在第 1 步手动增删）。`,
    });
  }

  if (
    input.reasoningDepth === "exhaustive" &&
    input.wakeMinutes != null &&
    input.wakeMinutes > 0 &&
    input.wakeMinutes < DEEP_WAKE_FLOOR_MINUTES
  ) {
    // 成因在第 4 步（选了「极致」），修法在第 2 步（把唤醒周期调大），故两步都渲染；跳转目标
    // 是 `rhythm`。这类跨步耦合正是分步化最容易丢东西的地方，用 `steps[]` 一次性表达清楚。
    risks.push({
      id: "deepWakeTooShort",
      steps: ["rhythm", "cognition"],
      tone: "warn",
      title: "研判深度与唤醒节奏打架",
      detail:
        `「极致」档单轮可能 5–10 分钟，而当前唤醒周期是 ${input.wakeMinutes} 分钟——推理会挤占` +
        `交易节奏，建议把周期调到 ≥ ${DEEP_WAKE_FLOOR_MINUTES} 分钟（仅提示，不阻止启动）。`,
    });
  }

  // 回测车道**恒不装配经验库**（服务端如此，经验卡在回测下渲染的也是「回测不读取也不写入经验库」
  // 那句常规说明，`ai-trade-experience-off-note` 整段不可达）。不带这条模式判据，第 6 步就会出现
  // 一条**产地步骤从没说过、且在回测下本就为假**的警示——与 `wakeFloorRaised` 在除数为 0 时不说话
  // 是同一条纪律：算不出/不成立的事一个字都不说。
  if (input.mode === "live" && !input.experienceEnabled) {
    risks.push({
      id: "experienceOff",
      steps: ["cognition"],
      tone: "warn",
      title: "经验模式整关",
      detail:
        "本次会话既不读取也不沉淀经验——这段时间的交易不会进入经验库。只想「用但不沉淀」的话，" +
        "请在第 4 步保持勾选并取消「写回经验库」。",
    });
  }

  // 唤醒下限只在服务端真的开着这条抬升时才说（`divisor === 0` 是本仓默认：只观测不改行为）。
  // 除数为 0 时说一句「会被抬到 N 分钟」就是一句假话。
  const wakeSeconds = (input.wakeMinutes ?? 0) * 60;
  if (
    input.wakeFloorDivisor > 0 &&
    input.targetHoldMinSeconds > 0 &&
    wakeSeconds > 0 &&
    wakeSeconds < input.targetHoldMinSeconds / input.wakeFloorDivisor
  ) {
    const floorMinutes = input.targetHoldMinSeconds / input.wakeFloorDivisor / 60;
    risks.push({
      id: "wakeFloorRaised",
      steps: ["rhythm"],
      tone: "warn",
      title: "唤醒周期会被服务端抬高",
      detail:
        `唤醒周期短于与目标持仓相干的下限：启动时会被自动抬到约 ${floorMinutes.toFixed(1)} 分钟` +
        `（= 目标最短持仓 / ${input.wakeFloorDivisor}），并如实列在启动回执的「服务端调整」里。`,
    });
  }

  return risks;
}

/** 该步上就地内联渲染的风险（每条风险在其 `steps` 列出的每一步各渲染一次）。 */
export function risksForStep(
  risks: readonly AiTradeLaunchRisk[],
  id: AiTradeLaunchStepId,
): AiTradeLaunchRisk[] {
  return risks.filter((risk) => risk.steps.includes(id));
}
