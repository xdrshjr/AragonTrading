"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Switch } from "@/components/ui";
import { accountApi } from "@/lib/accountApi";
import { agentApi } from "@/lib/agentApi";
import { aiBacktestApi } from "@/lib/aiBacktestApi";
import { aiTradeApi } from "@/lib/aiTradeApi";
import {
  AI_TRADE_BASE_RULE_DEFAULTS,
  AI_TRADE_DEFAULT_CONTEXT_ROUNDS,
  AI_TRADE_DEFAULT_POSITION_STEP,
  AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN,
  AI_TRADE_PROFIT_GUARD_DEFAULTS,
  describeAiTradeLifecycleBadge,
  describeStartError,
  formatNextWake,
  resolveAiTradeArmed,
  resolveAiTradeGate,
  resolveAiTradeLifecycle,
} from "@/lib/aiTrade";
import {
  currentAiTradeEpoch,
  isAiTradeNoticeLive,
  mintAiTradeNotice,
} from "@/lib/aiTradeNotice";
import { ApiRequestError } from "@/lib/api";
import { groupStateFromStatus } from "@/lib/useAiTradeGroupStream";
import type {
  AiTradeEndResult,
  AiTradeNotice,
  AiTradeResumableMember,
  AiTradeResumePreview,
} from "@/lib/aiTradeTypes";
import { fmtNum } from "@/lib/format";
import { useAppStore } from "@/lib/store";
import AiTradeLaunchModal, { type AiTradeLaunchOpts } from "./AiTradeLaunchModal";
import AiTradeEndDialog from "./AiTradeEndDialog";
import AiTradePauseDialog from "./AiTradePauseDialog";
import AiTradeResumeDialog from "./AiTradeResumeDialog";

/**
 * AI 自动交易开关（ai-trade-multi-symbol-and-timeframe §4.2）：挂 AgentPanel 底部「AI 自动交易
 * 控制条」——一条紧贴输入框上沿的常驻通栏（agent-panel-toolbar-two-rows §4.3）。
 * 只要 Agent 已配置模型即可点开弹窗（回测门禁独立于 resolveAiTradeGate）；弹窗内「实盘 / 回测」段控——
 * 实盘段走完整 resolveAiTradeGate 门禁并**自动填入左侧「自动交易」面板的 autoTradeMembers 多标的**（分组
 * 启动），回测段仅需 Agent 已配置。运行中开关用于停止整组；回测确认→启动 job（开关不 latch）。
 */
export default function AiTradeToggle() {
  const accountMode = useAppStore((s) => s.accountMode);
  const instId = useAppStore((s) => s.instId);
  const bar = useAppStore((s) => s.bar);
  const strategyId = useAppStore((s) => s.strategyId);
  const backtestStartTs = useAppStore((s) => s.backtestStartTs);
  const backtestEndTs = useAppStore((s) => s.backtestEndTs);
  const aiTradeGroup = useAppStore((s) => s.aiTradeGroup);
  const aiTradeResumable = useAppStore((s) => s.aiTradeResumable);
  const liveAutoGroup = useAppStore((s) => s.liveAutoGroup);
  const autoTradeMembers = useAppStore((s) => s.autoTradeMembers);
  const autoTradeAllocationMode = useAppStore((s) => s.autoTradeAllocationMode);
  const aiTradeAcknowledged = useAppStore((s) => s.aiTradeAcknowledged);
  const aiTradeConfig = useAppStore((s) => s.aiTradeConfig);
  const aiBacktestConfig = useAppStore((s) => s.aiBacktestConfig);
  const setAiTradeGroup = useAppStore((s) => s.setAiTradeGroup);
  const setAiTradeResumable = useAppStore((s) => s.setAiTradeResumable);
  const aiTradeReconciledAtMs = useAppStore((s) => s.aiTradeReconciledAtMs);
  const noteAiTradeNotice = useAppStore((s) => s.noteAiTradeNotice);
  const dismissAiTradeNotice = useAppStore((s) => s.dismissAiTradeNotice);
  const setAiTradeAcknowledged = useAppStore((s) => s.setAiTradeAcknowledged);
  const setAiTradeConfig = useAppStore((s) => s.setAiTradeConfig);
  const setAiBacktestJobId = useAppStore((s) => s.setAiBacktestJobId);
  const setAiBacktestConfig = useAppStore((s) => s.setAiBacktestConfig);
  const clearAiTradeRuntime = useAppStore((s) => s.clearAiTradeRuntime);

  const qc = useQueryClient();
  // 倒计时每秒 tick 一次（只在有分组时挂表：idle 态挂一个每秒重渲染的定时器纯属浪费）。
  const [nowTs, setNowTs] = useState(() => Math.floor(Date.now() / 1000));
  const [modalOpen, setModalOpen] = useState(false);
  // 局部 `error` 同样纪元化（§4.6，修 E-13）。它**共用同一张规则表、同一个判活谓词**，不新造第二套
  // 判据。E-13 唯一可达的那条路径是**外部发起**的继续（另一个窗口 / 活性看门狗自动停手）：
  // `resumeM.onSuccess` 根本不跑，于是一条早已过期的错误会在**下一次暂停时原地复活**——那比
  // 「不消失」更坏。修它的是 `action_error` 的 `resumeScoped:true`（看的是「这是不是同一段
  // running/paused」，而不是「态叫什么名字」），且**不依赖任何写点记得去清它**。
  const [error, setError] = useState<AiTradeNotice | null>(null);
  // 铸造点读的是**此刻**的 store 断面（`getState()` 而不是闭包里的快照）：本函数身份恒定，
  // 不会让下游 `useCallback` 的依赖每渲染一次就变一次。
  const noteError = useCallback((text: string | null) => {
    const s = useAppStore.getState();
    setError(mintAiTradeNotice("action_error", text,
      currentAiTradeEpoch(s.aiTradeGroup, s.aiTradeResumable, s.aiTradeReconciledAtMs), Date.now()));
  }, []);
  // ai-trade-loss-and-liveness-hardening A-1：422 `AI_TRADE_UNGUARDED_HORIZON` 随错误体带回的
  // **可执行补救**（文案由后端下发，前端只把它渲染成按钮）。其余错误 ⇒ 恒 null ⇒ 逐字回退。
  const [startRemedies, setStartRemedies] = useState<{ id: string; label: string }[] | null>(null);
  const [pauseOpen, setPauseOpen] = useState(false);
  const [resumeOpen, setResumeOpen] = useState(false);
  const [endOpen, setEndOpen] = useState(false);
  const [preview, setPreview] = useState<AiTradeResumePreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  // 进入「正在武装」清单的时刻（秒级墙钟，本地瞬态）。null = 还没提交过。
  const [armingSince, setArmingSince] = useState<number | null>(null);

  const accountsQ = useQuery({ queryKey: ["okx", "accounts"], queryFn: accountApi.accounts.list });
  const defaultAccount =
    accountsQ.data?.accounts.find((a) => a.id === accountsQ.data?.defaultId) ?? null;
  const hasReadyDefault =
    !!defaultAccount && defaultAccount.hasSecret && defaultAccount.hasPassphrase;

  const agentConfigQ = useQuery({ queryKey: ["agent", "config"], queryFn: agentApi.config });
  const agentConfigured = agentConfigQ.data?.configured ?? false;

  // [评审 R0-7] **`refetchInterval` 撤掉了**：对账的唯一 owner 是页面级的
  // `useAiTradeGroupStream`（它不随面板折叠 / 归档而卸载）。两处各按自己的时序覆盖同一份生命
  // 周期，正是 D-5「两处口径」的换皮复发；同时实盘模式下的总请求数从 2 次/15 秒降到 1 次/15 秒。
  // 这里只保留挂载时的那一次，供首屏门禁在 store 还没被对账器写过时有个数。
  const statusQ = useQuery({
    queryKey: ["aiTrade", "status"],
    queryFn: aiTradeApi.status,
    enabled: accountMode === "live",
  });
  // 首屏 store 未水合时它是 `false`（风险 R-11）——门禁文案必须据 `aiTradeSyncState` 区分
  // 「正在与后端同步」和「本机未开启」，后者在首屏是一句**假话**。
  const aiTradeEnabled = useAppStore((s) => s.aiTradeEnabled) || (statusQ.data?.aiTradeEnabled ?? false);
  const aiTradeSyncState = useAppStore((s) => s.aiTradeSyncState);
  const requestAiTradeReconcile = useAppStore((s) => s.requestAiTradeReconcile);

  // 生命周期的唯一推导点（L-4）：任何组件都不得自己拼这个判断。
  const lifecycle = resolveAiTradeLifecycle(aiTradeGroup, aiTradeResumable);
  // 当前纪元（§4.1）：`liveError` 的判据。它订阅了 `aiTradeReconciledAtMs`，故一次「除了时戳
  // 什么都没变」的对账同样会让这一行重算。
  const epoch = currentAiTradeEpoch(aiTradeGroup, aiTradeResumable, aiTradeReconciledAtMs);
  const armed = resolveAiTradeArmed(aiTradeGroup);
  const syncing = aiTradeSyncState === "unknown";
  const running = lifecycle === "running";
  const paused = lifecycle === "paused";
  const stopped = lifecycle === "stopped";
  const badge = describeAiTradeLifecycleBadge(lifecycle, armed, syncing);
  const groupId = aiTradeGroup?.groupId ?? aiTradeResumable?.groupId ?? null;
  // 实盘段门禁（传入弹窗，实盘 tab 据此禁用 + 悬浮说明）；回测段仅需 agentConfigured。
  const liveGateReason = resolveAiTradeGate({
    accountMode,
    hasReadyDefault,
    aiTradeEnabled,
    agentConfigured,
    liveAutoActive: !!liveAutoGroup,
    // 暂停中的分组同样占着那唯一一个活跃位（后端容量闸会 409 `AI_TRADE_PAUSED_GROUP_EXISTS`）。
    // 不把 `paused` 算进来，弹窗会看起来可用，用户填完一整屏参数才在提交时吃一个 409。
    //
    // **[评审 R1-5] `stopped` 刻意不算进来**：B-1 之后后端此刻已经清了组，容量闸不会 409，
    // 用户应当能直接开一个新分组。别「顺手把 stopped 也算进 hasActiveSession」——那会把
    // 「会话已结束却再也开不了新的」这个死结重新系上。
    hasActiveSession: running || paused,
  });
  // 打开弹窗的最小门禁（独立于 resolveAiTradeGate）：Agent 已配置即可（回测在模拟账户模式也可用，R6）。
  const openBlockedReason = running ? null : !agentConfigured ? "请先在设置中心配置 AI 模型" : null;

  const startM = useMutation({
    mutationFn: (opts: AiTradeLaunchOpts) =>
      aiTradeApi.start({
        members: opts.members,
        bar: opts.bar,                       // 可编辑周期真正生效（不再直读 store.bar）
        strategyIds: opts.strategyIds,
        allocationMode: opts.allocationMode,
        capitalUsd: opts.capitalUsd,
        leverage: opts.leverage,
        takeProfitUsd: opts.takeProfitUsd,
        stopLossUsd: opts.stopLossUsd,
        stopLossPct: opts.stopLossPct,       // E4：单仓 OCO 止损%（透传，服务端夹紧）
        takeProfitPct: opts.takeProfitPct,   // E4：单仓 OCO 止盈%
        positionStepPct: opts.positionStepPct,   // 仓位操作档位（透传，服务端默认回填/量化）
        wakeIntervalMinutes: opts.wakeIntervalMinutes,   // Agent 墙钟定时唤醒周期（透传，服务端默认回填/夹紧）
        reasoningDepth: opts.reasoningDepth,     // 研判深度档（透传；留空=服务端默认 deep，仅实盘）
        baseRules: opts.baseRules,               // 【基础策略】铁律（透传；取消勾选即 enabled:false）
        profitGuard: opts.profitGuard,           // 利润保护层（透传；取消勾选即 enabled:false）
        tradingHorizon: opts.tradingHorizon,     // 交易周期模式（同上；缺省=服务端默认开启）
        discipline: opts.discipline,             // 交易纪律预设（同上；缺省=服务端默认 balanced）
        experience: opts.experience,             // 经验模式（同上；取消勾选即 enabled:false）
        // 市场情报研判（intel-session-binding 轴 A）：**整键缺省 ⇒ 服务端不注入**（与上面
        // 六项的方向相反，这是需求的明文要求）。显式下发让「用户看过并选了不开」在
        // `start_wire` 里留下痕迹，冷续跑据它把老会话与新会话区分开。
        intel: opts.intel,
        direction: opts.direction,               // 方向对称性（同上；取消勾选即 contrarianEnabled:false）
        trend: opts.trend,                       // 执行周期趋势基调（同上；取消勾选即 enabled:false）
        contextRefreshRounds: opts.contextRefreshRounds,
      }),
    onSuccess: (res, opts) => {
      setAiTradeGroup(groupStateFromStatus(res));
      // 记忆偏好（策略集合 + 刷新轮数 + 仓位档位 + 唤醒周期），下次启动回填。[R-4] positionStepPct/
      // wakeIntervalMinutes 须取自 opts——setAiTradeConfig 是整对象替换且 res 不回显此二键，取 res 会让
      // 所选偏好每次重开都回落默认、永不持久。
      setAiTradeConfig({
        strategyIds: res.strategyIds,
        contextRefreshRounds: res.contextRefreshRounds,
        positionStepPct: opts.positionStepPct,
        wakeIntervalMinutes: opts.wakeIntervalMinutes ?? undefined,
        // [R-4] 同理取 opts：回执不回显研判深度档，取 res 会让这个选择每次重开都回落默认。
        reasoningDepth: opts.reasoningDepth ?? undefined,
        // [R-4] 同理：res 不回显完整规则参数，取 opts 才能让所选铁律参数真正持久（回执只镜像装配态）。
        baseRules: opts.baseRules.maBandEntry ?? undefined,
        // 同上取 opts：诊断根因 ③ 的根治点——单仓 OCO 与利润保护偏好从此被记住并回填，
        // 不再每次重开都清空、让用户在不知情下一直吃 10%/5% 的宽默认。
        profitGuard: opts.profitGuard,
        // [R-4] 同理取 opts：回执镜像的是**服务端解析后的生效值**（含两个派生量），拿它回填会把
        // 用户「留空=按预设」的意图固化成一组具体数字，下次改周期时不再联动。
        tradingHorizon: opts.tradingHorizon,
        stopLossPct: opts.stopLossPct ?? undefined,
        takeProfitPct: opts.takeProfitPct ?? undefined,
        // [R-4] 同理取 opts：回执不回显经验模式规则，取 res 会让这个勾选每次重开都回落默认。
        experience: opts.experience,
        // [R-4] 同理取 opts：回执里的 `intel` 是**服务端解析后的生效值**（含 `source` 与
        // 派生档 `off`），拿它回填会把「我选了索引档但这次被运维总闸忽略了」固化成「我选了
        // 关闭」——下次打开弹窗时用户的选择就被服务端的一次临时状态改写了。
        intel: opts.intel,
      });
      setModalOpen(false);
      noteError(null);
      setStartRemedies(null);
      // 新会话起来了，上一次的生命周期提示就过期了（那笔孤儿仓的告知使命到此为止）。
      // 走 `dismiss` 而不是铸一条空提示：语义上这与用户点「知道了」等价，且它是**唯一**能越过
      // §4.3 铸造闸清掉一条 sticky 的合法出口。
      dismissAiTradeNotice();
      qc.invalidateQueries({ queryKey: ["aiTrade", "status"] });
    },
    onError: (e) => {
      noteError(e instanceof ApiRequestError ? describeStartError(e) : "启动失败，请稍后重试");
      setStartRemedies(e instanceof ApiRequestError ? (e.remedies ?? null) : null);
    },
  });

  const startBacktestM = useMutation({
    mutationFn: (opts: AiTradeLaunchOpts) =>
      aiBacktestApi.start({
        instId: opts.instId,
        bar: opts.bar,                       // 回测同享可编辑周期
        strategyIds: opts.strategyIds,
        startTs: opts.startTs ?? 0,
        endTs: opts.endTs ?? 0,
        decisionEveryBars: opts.decisionEveryBars ?? null,
        initialCapital: opts.capitalUsd,
        leverage: opts.leverage,
        stopLossPct: opts.stopLossPct,
        takeProfitPct: opts.takeProfitPct,
        takeProfitUsd: opts.takeProfitUsd,
        stopLossUsd: opts.stopLossUsd,
        contextRefreshRounds: opts.contextRefreshRounds,
        minConsensus: opts.minConsensus ?? null,
        positionStepPct: opts.positionStepPct,   // 回测同享档位约束（决策脑单点收口，实盘/回测同生效）
        baseRules: opts.baseRules,               // 回测同享铁律（P1-4：默认必须与实盘一致，否则调参失真）
        // 回测同享交易周期模式（同一 `_resolve_horizon` 解析 + 一层回测运维总闸）。同一条理由——
        // 两侧参数不同源，用户拿回测调出来的周期在实盘直接失真。
        tradingHorizon: opts.tradingHorizon,
        // F3：回测同享利润保护（bar 内极值 + 保守成交序）。同一条理由——两侧参数不同源，
        // 用户拿回测调出来的参数在实盘直接失真。
        profitGuard: opts.profitGuard,
        // 回测同享交易纪律预设（同一 `_resolve_discipline`，R-3）。同一条理由：不同源就失真，
        // 且回测正是唯一能对这组阈值做「开 vs 不开」净额对照的地方。
        discipline: opts.discipline,
        // 回测同享方向对称性（同一 `_resolve_direction`）。同一条理由：不同源就失真。
        direction: opts.direction,
        // 回测同享执行周期趋势基调（同一 `_resolve_trend`，评审 RP1-10）。回测正是唯一能对
        // 「开 vs 不开基调层」做净额对照的地方，两侧不同源就等于那个对照跑的不是实盘参数。
        trend: opts.trend,
      }),
    onSuccess: (res, opts) => {
      setAiBacktestJobId(res.jobId);
      setAiBacktestConfig({
        decisionEveryBars: res.decisionEveryBars,
        contextRefreshRounds: opts.contextRefreshRounds,
      });
      setModalOpen(false);
      noteError(null);
    },
    onError: (e) =>
      noteError(e instanceof ApiRequestError ? e.message : "回测启动失败，请稍后重试"),
  });

  const stopM = useMutation({
    mutationFn: (id: string) => aiTradeApi.stopGroup(id),
    onSettled: () => {
      clearAiTradeRuntime();
      qc.invalidateQueries({ queryKey: ["aiTrade", "status"] });
    },
  });

  // ---- 暂停 / 继续 / 结束（pause-resume §3.9.2）----
  /**
   * 写端点的失败分流（L-7 / [评审 R0-6]）。
   *
   * **超时的写请求不得渲染成「失败」** —— 一个超时的 `/resume` 很可能**已经成功了**
   * （后端还在跑冷续跑的暖机），把它显示成「继续失败」会让用户再点一次，而那是一次真实资金的
   * 开仓授权。
   *
   * 判据必须是 `code === "TIMEOUT"`：`aiTradeApi.request` 的 `catch` 已经把超时经 `toApiError`
   * 换成了 `ApiRequestError("TIMEOUT", …)`，到 `onError` 这一层原始异常的 `name` 早已不在，
   * `isTimeoutError` 在这里**恒为假**（照 v1 写，L-7 承诺的「结果未知」一次都不会出现）。
   *
   * 命中即：**不写 error**，改为把后端 `timeoutMessage` 单点生成的那句「本次操作结果未知……」
   * 挂成持久提示 + 请一次快对账，由 3 秒节奏的对账给出最终结论。
   */
  const handleActionError = useCallback(
    (e: unknown, fallback: string) => {
      if (e instanceof ApiRequestError && e.code === "TIMEOUT") {
        noteError(null);
        // 文案由 `timeoutMessage` 单点生成，前端不再拼第二句。`action_unknown` 是**唯一**由对账
        // 结算的种类（`settledByReconcile`）：t+1.5s 之内落地的那次不算（它问的是一个可能还在处理
        // 这次写请求的后端），t+3s 的快对账落地即判死 —— 这才兑现了旁边那句「由对账给出最终结论」。
        // 若此刻槽位上压着一条**仍然成立**的 sticky（比如「平仓没有完成，请立即手动处理」），
        // 铸造闸会丢弃这一条：一句「我不知道现在是什么态」压不过一句真钱待办（§4.3）。
        noteAiTradeNotice("action_unknown", e.message);
        requestAiTradeReconcile();
        return;
      }
      noteError(e instanceof ApiRequestError ? describeStartError(e) : fallback);
    },
    [noteError, noteAiTradeNotice, requestAiTradeReconcile],
  );

  const pauseM = useMutation({
    mutationFn: (opts: { holdPosition: boolean }) =>
      aiTradeApi.pauseGroup(groupId ?? "", { holdPosition: opts.holdPosition }),
    onSuccess: (res) => {
      // 回执的 `notice` 是后端单点生成的判定性文案（§3.10）：它是唯一一处会说出「平仓其实没成功」
      // 与「此刻没有任何持久化兜底」的地方。只写不读 = 用户永远看不到那句话。
      // 铸造时本地 `lifecycle` 仍是 `"running"`：`pause_group` 的回执**不是** `group_status()`
      // 形状（没有 lifecycle 证据可供 `groupStateFromStatus` 消费），故这里刻意不置本地态——
      // 让前端自己推导分组结论是 L-1 的反面。谓词的「未落地宽限」子句负责在权威结论落地前
      // 接住这条刚铸出来的话（§4.4 路径 A′），对账一落地就恢复成硬判据。
      noteAiTradeNotice("paused", res.notice ?? null);
      setPauseOpen(false);
      noteError(null);
      requestAiTradeReconcile();               // 3 秒快对账：让权威回执尽快确认这次迁移
      qc.invalidateQueries({ queryKey: ["aiTrade", "status"] });
    },
    onError: (e) => handleActionError(e, "暂停失败，请稍后重试"),
  });

  const resumeM = useMutation({
    mutationFn: (opts: { acknowledge: boolean }) =>
      aiTradeApi.resumeGroup(groupId ?? "", { acknowledge: opts.acknowledge }),
    onSuccess: (res) => {
      // 续跑成功后热态回来了：装上分组视图并清掉冷占位（两者互斥，绝不同时非空）。
      setAiTradeGroup(groupStateFromStatus(res));
      setAiTradeResumable(null);
      noteError(null);
      // 用户报告的那条缺陷的**写点**（本轮新增）。但请注意这条链路真正的性质：即使这一行被误删，
      // 暂停提示照样消失 —— 上一行 `setAiTradeGroup` 已把 lifecycle 推到 `running`，渲染点的谓词
      // 当场判死它。写点只负责「换上新的那句话」，不负责「记得清掉旧的」，这正是本设计与
      // 「补一行 setNotice(null)」的本质区别（G-2）。
      // 老后端没有这个键 ⇒ `?? null` ⇒ 只清不换，G-1 依然成立（后端是增益，不是前提）。
      noteAiTradeNotice("resumed", res.notice ?? null);
      // **对话框不立刻关闭**（F-8）：进入「正在武装」清单，由 `lifecycle.armed` 决定它什么时候
      // 该消失。点完「继续」就把框关掉，等于把「它到底起来了没有」这个问题重新交还给用户去猜。
      requestAiTradeReconcile();
      qc.invalidateQueries({ queryKey: ["aiTrade", "status"] });
    },
    onError: (e) => {
      // **明确失败（非超时）⇒ 退出武装清单**，回到复核屏把后端的逐标的明细摊开给用户看，
      // 主按钮也立刻可用。让一次已经被 409 拒绝的续跑继续对着一张「武装中」的清单等 45 秒，
      // 就是本轮要消灭的那句「貌似在进行中」。
      // 超时**不**退出：那一格的语义是「结果未知」，清单继续等，由快对账给最终结论（L-7）。
      if (!(e instanceof ApiRequestError && e.code === "TIMEOUT")) setArmingSince(null);
      handleActionError(e, "继续失败，请稍后重试");
    },
  });

  const endM = useMutation({
    mutationFn: (opts: { flatten: boolean | null; acknowledgeOrphanPosition: boolean }) =>
      aiTradeApi.endGroup(groupId ?? "", opts),
    onSuccess: (res) => {
      clearAiTradeRuntime();
      // `orphanPositions` = 此次结束后仍留在交易所上的持仓（§5.4 要求前端据它出一条**持久**提示）。
      // 归档后 AI 面板整块消失，若这条提示也跟着消失，「这些仓从此没人管了」就再也没人告诉用户。
      noteAiTradeNotice("ended", describeOrphanPositions(res.orphanPositions));
      setEndOpen(false);
      setResumeOpen(false);
      noteError(null);
      requestAiTradeReconcile();
      qc.invalidateQueries({ queryKey: ["aiTrade", "status"] });
    },
    onError: (e) => handleActionError(e, "结束失败，请稍后重试"),
  });

  // 「继续」按钮三态（`继续` / `正在核对持仓…` / 错误原因内联展示）：先拉复核数据再开框，
  // 用户看到的第一屏就是「离线期间发生了什么」，而不是一个空壳弹窗。
  const openResume = useCallback(() => {
    if (!groupId) return;
    noteError(null);
    setPreview(null);
    setArmingSince(null);          // 重新打开即回到第一屏（复核），而不是接着上次的武装清单
    setResumeOpen(true);
    setPreviewLoading(true);
    aiTradeApi
      .resumePreview(groupId)
      .then(setPreview)
      .catch((e: unknown) =>
        noteError(e instanceof ApiRequestError ? describeStartError(e) : "无法核对持仓，请稍后重试"),
      )
      .finally(() => setPreviewLoading(false));
  }, [groupId]);

  const hasLifecycleWire = !!aiTradeGroup?.lifecycle;
  const armingOpen = armingSince != null;
  useEffect(() => {
    if (!hasLifecycleWire && !armingOpen) return;
    const timer = setInterval(() => setNowTs(Math.floor(Date.now() / 1000)), 1_000);
    return () => clearInterval(timer);
  }, [hasLifecycleWire, armingOpen]);

  // 武装清单全绿 ⇒ 1 秒后自动关闭（给用户看一眼「全部就绪」再收起来，而不是一闪而过）。
  const fullyArmed = armingOpen && lifecycle === "running" && armed.armed && !armed.unavailable;
  useEffect(() => {
    if (!fullyArmed) return;
    const timer = setTimeout(() => {
      setResumeOpen(false);
      setArmingSince(null);
      setPreview(null);
    }, 1_000);
    return () => clearTimeout(timer);
  }, [fullyArmed]);

  const onToggle = (next: boolean) => {
    noteError(null);
    if (next) {
      if (openBlockedReason) return;
      setModalOpen(true);
    } else if (groupId) {
      stopM.mutate(groupId);
    }
  };

  const defaultStrategyIds =
    aiTradeConfig?.strategyIds && aiTradeConfig.strategyIds.length > 0
      ? aiTradeConfig.strategyIds
      : strategyId != null
        ? [strategyId]
        : [];

  // `resuming`（F-6 的关键一态）：**本地瞬态，不来自后端**。从点下「继续」到
  // `lifecycle.armed === true` 之间可能有几十秒（冷续跑要暖机），而在本轮之前这段时间界面上
  // 什么都没有——用户只能靠猜。
  const resuming = resumeM.isPending;
  // `unknown` 下动作按钮全部禁用（L-7 的极性：读不到就不让点）。
  const actionsDisabled = syncing || resuming;
  const countdown = formatNextWake(aiTradeGroup?.lifecycle, lifecycle, nowTs);
  // 全部五个读点（行内一处 + 四个弹窗）一律吃这一个值：写点带纪元、读点过谓词，中间不留第二条
  // 通路。`action_error` 的 `ttlMs` 为 null，故此处的 `Date.now()` 只是形参，不引入每秒重渲染。
  const liveError = error && isAiTradeNoticeLive(error, epoch, Date.now()) ? error.text : null;

  return (
    <div className="flex w-full min-w-0 flex-col gap-1">
      {/* 行容器承接**唯一一份** title：挂在「行」而不是左簇，是为了保住改前的悬停覆盖面——暂停态
          那段 title 恰恰是在解释「继续 / 结束」这两颗按钮，挂到左簇会让它们悬停无提示。代价是行内
          空白处也会弹气泡：可接受，这一整行本就是一个被 aria-label 命名过的语义分组。
          min-h-[24px] 把三态行高锁成同一个值：idle 行内最高的是 20px 的 Switch，running / paused
          多出 24px 的 sm Button，不钉死就会在开启 / 停止时抖 4px 并连带推动输入框。 */}
      <div
        data-testid="ai-trade-toggle-row"
        className="flex min-h-[24px] w-full min-w-0 items-center justify-between gap-2"
        title={
          syncing
            ? "正在与后端核对 AI 自动交易的状态；核对完成前不接受新的动作"
            : resuming
              ? "正在恢复：核对持仓 → 重建会话 → 接上行情。全部就绪后才会回到「运行中」"
              : stopped
                ? (openBlockedReason
                  ?? "这次 AI 自动交易已结束。点「知道了」清理面板，或直接开启一个新的分组")
                : paused
                  ? "已暂停：不再开新仓，但盯市与保护层照常运行。点「继续」核对持仓后接着跑，或「结束」归档"
                  : (openBlockedReason
                    ?? (running ? "点击停止 AI 自动交易并平掉自管持仓" : "开启 AI 自动交易 / 回测"))
        }
      >
        {/* 左簇：标签 + 开关。全行**唯一**的压力吸收点（min-w-0 + truncate）——宽度不足时只有它退让。 */}
        <div data-testid="ai-trade-toggle-main" className="flex min-w-0 items-center gap-1.5">
          <span
            className={
              running || paused
                ? "min-w-0 truncate text-[11px] font-medium text-[var(--accent-ink)]"
                : "min-w-0 truncate text-[11px] text-[var(--muted)]"
            }
          >
            AI 自动交易
          </span>
          {/* 四态 + `resuming` 控件（F-6）。`paused` / `resuming` / `unknown` 时开关保持 off 且
              **禁用**——避免「关开关」与「结束」两个入口语义重叠：暂停可逆、结束不可逆，二者绝不
              能长得像同一个动作；而读不到后端状态时不让点，是 L-7 的同一条极性。 */}
          <Switch
            checked={running}
            onChange={paused || resuming || syncing ? noop : onToggle}
            label="AI 自动交易开关"
            className={
              (openBlockedReason && !running) || paused || resuming || syncing
                ? "opacity-40"
                : undefined
            }
          />
          {/* 「下一次决策 03:12」——`running` 且**真的武装**时才走秒（[评审 R1-6]）。
              这条倒计时是「已经恢复到 Agent 执行交易状态」的主要证据：用户看到「运行中」的同时
              看到下一次思考还有多久，才叫真的恢复了。 */}
          {countdown && (
            <span
              data-testid="ai-trade-next-wake"
              className="shrink-0 rounded border border-[var(--border)] px-1 py-px text-[9px] tabular text-[var(--muted)]"
              title="下一次 Agent 决策的预计时刻（以服务端时钟为锚点）"
            >
              {countdown}
            </span>
          )}
        </div>

        {/* 右簇：生命周期动作。shrink-0 ⇒ 宽度压力一律流向左簇标签的省略号，按钮永不被压折。
            idle 态它是个空 div——刻意**无条件渲染**，让排版契约在三态下都有同一个落点。 */}
        <div data-testid="ai-trade-toggle-actions" className="flex shrink-0 items-center gap-1.5">
          {/* 徽标是四态 + `armed` 分档的**唯一**呈现点（`describeAiTradeLifecycleBadge`）：
              idle 态不渲染（那时这一行本就该是空的）。 */}
          {lifecycle !== "idle" && (
            <Badge tone={badge.tone}>
              <span data-testid="ai-trade-lifecycle-badge">
                {resuming ? "正在恢复…" : badge.label}
              </span>
            </Badge>
          )}
          {running && !resuming && (
            <Button
              variant="ghost"
              size="sm"
              aria-label="暂停 AI 自动交易"
              disabled={actionsDisabled}
              onClick={() => {
                noteError(null);
                setPauseOpen(true);
              }}
            >
              ⏸ 暂停
            </Button>
          )}
          {(paused || resuming) && (
            <>
              <Button
                variant="primary"
                size="sm"
                aria-label="继续 AI 自动交易"
                disabled={actionsDisabled}
                onClick={openResume}
              >
                {resuming ? "正在恢复…" : "▶ 继续"}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                aria-label="结束 AI 自动交易"
                disabled={actionsDisabled}
                onClick={() => {
                  noteError(null);
                  setEndOpen(true);
                }}
              >
                结束
              </Button>
            </>
          )}
          {/* 终态留痕（F-5）：会话自行终止（会话止损 / 强平）是用户**必须**知道的事，面板整块
              消失等于没说。点「知道了」才清运行期态。 */}
          {stopped && (
            <Button
              variant="ghost"
              size="sm"
              aria-label="清理已停止的 AI 自动交易面板"
              disabled={syncing}
              onClick={() => {
                noteError(null);
                clearAiTradeRuntime();
              }}
            >
              知道了
            </Button>
          )}
        </div>
      </div>

      {/* 错误文案独占第二行：在通栏里再挤一段截断文本，等于把「继续 / 结束」按钮挤没。
          whitespace-normal 显式覆盖控制条继承下来的 nowrap（这是本方案里唯一需要换行的文本）。
          data-testid 是必需的：四个弹窗里的错误段落同样是 role="alert"，只靠 role 定位有歧义。
          触发面很窄——四个弹窗的 onClose 一律 noteError(null)，唯一到达路径是「续跑复核在途时
          先关掉弹窗、随后复核失败」这个竞态。 */}
      {(paused || stopped) && liveError && !resumeOpen && !endOpen && (
        <span
          data-testid="ai-trade-toggle-error"
          className="whitespace-normal break-words text-[10px] leading-relaxed text-[var(--short-ink)]"
          role="alert"
        >
          {liveError}
        </span>
      )}

      <AiTradePauseDialog
        open={pauseOpen}
        group={aiTradeGroup}
        submitting={pauseM.isPending}
        error={pauseOpen ? liveError : null}
        onClose={() => {
          setPauseOpen(false);
          noteError(null);
        }}
        onConfirm={(opts) => pauseM.mutate(opts)}
      />
      {/* F-8：提交后**不立刻关闭**，改为进入「正在武装」清单 —— 从点下「继续」到真的 armed
          之间可能有几十秒（冷续跑要暖机），关掉框等于把「它到底起来了没有」还给用户去猜。
          `armingSince` 是进入第三屏的时刻（本地瞬态），45 秒还没 armed ⇒ 清单原地变成如实的
          失败说明 + 两个出路。 */}
      <AiTradeResumeDialog
        open={resumeOpen}
        preview={preview}
        loading={previewLoading}
        submitting={resumeM.isPending}
        error={resumeOpen ? liveError : null}
        arming={armingSince != null}
        armingSince={armingSince}
        lifecycle={aiTradeGroup?.lifecycle ?? null}
        members={aiTradeGroup ? Object.values(aiTradeGroup.members) : []}
        nowTs={nowTs}
        onClose={() => {
          setResumeOpen(false);
          setArmingSince(null);
          noteError(null);
        }}
        onConfirm={(opts) => {
          setArmingSince(Math.floor(Date.now() / 1000));
          resumeM.mutate(opts);
        }}
        onEnd={() => {
          setResumeOpen(false);
          setArmingSince(null);
          setEndOpen(true);
        }}
      />
      <AiTradeEndDialog
        open={endOpen}
        members={endDialogMembers(aiTradeResumable?.members, aiTradeGroup)}
        // 冷续跑卡上没有会话对象，后端也就没有任何东西能代用户下平仓单（服务端同样 fail-closed，
        // 回 `AI_TRADE_END_FLATTEN_UNAVAILABLE`）。这里**不给**那个选项，而不是给了再报错。
        canFlatten={!!aiTradeGroup}
        submitting={endM.isPending}
        error={endOpen ? liveError : null}
        onClose={() => {
          setEndOpen(false);
          noteError(null);
        }}
        onConfirm={(opts) => endM.mutate(opts)}
      />
      <AiTradeLaunchModal
        open={modalOpen}
        onClose={() => {
          setModalOpen(false);
          noteError(null);
          setStartRemedies(null);
        }}
        onConfirm={(opts) => (opts.mode === "backtest" ? startBacktestM.mutate(opts) : startM.mutate(opts))}
        submitting={startM.isPending || startBacktestM.isPending}
        acknowledged={aiTradeAcknowledged}
        onAcknowledge={setAiTradeAcknowledged}
        bar={bar}
        defaultMembers={autoTradeMembers}
        defaultAllocationMode={autoTradeAllocationMode}
        defaultInstId={instId}
        defaultStrategyIds={defaultStrategyIds}
        defaultContextRounds={aiTradeConfig?.contextRefreshRounds ?? AI_TRADE_DEFAULT_CONTEXT_ROUNDS}
        defaultPositionStepPct={aiTradeConfig?.positionStepPct ?? AI_TRADE_DEFAULT_POSITION_STEP}
        defaultWakeIntervalMinutes={aiTradeConfig?.wakeIntervalMinutes ?? AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN}
        defaultReasoningDepth={aiTradeConfig?.reasoningDepth}
        defaultProfitGuard={aiTradeConfig?.profitGuard ?? AI_TRADE_PROFIT_GUARD_DEFAULTS}
        defaultStopLossPct={aiTradeConfig?.stopLossPct ?? undefined}
        defaultTakeProfitPct={aiTradeConfig?.takeProfitPct ?? undefined}
        defaultBaseRule={aiTradeConfig?.baseRules ?? AI_TRADE_BASE_RULE_DEFAULTS}
        // 无本地预设兜底：模式表由 `GET /ai-trade/horizon-profiles` 下发，未选过时留 undefined 即可
        // （弹窗采用接口返回的 defaultProfile）——前端复制一份默认模式必然与服务端漂移（R-3）。
        defaultTradingHorizon={aiTradeConfig?.tradingHorizon}
        defaultExperience={aiTradeConfig?.experience}
        // 老 localStorage 无此键 ⇒ `undefined` ⇒ 弹窗回落 `AI_TRADE_INTEL_RULE_DEFAULT`
        // （默认不勾）。**不为此改 persist version**，沿用 baseRules 先例。
        defaultIntel={aiTradeConfig?.intel}
        error={liveError}
        errorRemedies={startRemedies}
        liveGateReason={liveGateReason}
        defaultStartTs={backtestStartTs}
        defaultEndTs={backtestEndTs}
        defaultDecisionEveryBars={aiBacktestConfig?.decisionEveryBars}
      />
    </div>
  );
}

/**
 * 暂停态下开关的空动作。
 *
 * 刻意**不给 `Switch` 加 `disabled` 形参**：`components/ui/index.tsx` 不在本轮的文件变更计划里，
 * 而它是全仓共用的 UI kit（改它要配一条 kit 用例，见 spec [P2-2] 对 `Badge tone="warn"` 的同款
 * 判断）。视觉上用 `opacity-40` 表达不可用，行为上在此处吃掉点击 —— 暂停期「关开关」与「结束」
 * 两个入口的语义绝不能重叠：暂停可逆，结束不可逆。
 */
const noop = () => {};

/**
 * 结束确认框的成员行：冷占位有落盘快照就用它，热暂停则由内存持仓派生。
 *
 * 两条路径都要走 —— 冷启后内存里没有会话（只有契约），而热暂停时冷占位是空的；少一条，
 * 「带仓时强制二选一」这道闸就会在另一条路径上**静默失效**（用户会看到一个无仓的普通确认框）。
 */
function endDialogMembers(
  cold: AiTradeResumableMember[] | undefined,
  group: ReturnType<typeof useAppStore.getState>["aiTradeGroup"],
): AiTradeResumableMember[] {
  if (cold && cold.length > 0) return cold;
  if (!group) return [];
  return Object.values(group.members).map((m) => ({
    instId: m.instId,
    runId: m.session?.runId ?? null,
    sessionId: m.sessionId,
    position: m.position
      ? {
          side: (m.position.side === "short" ? "short" : "long") as "long" | "short",
          size: Number(m.position.size ?? 0),
        }
      : null,
    positionUnknown: false,
  }));
}

/**
 * 结束回执里的孤儿持仓 → 一条持久提示（§5.4）；没有孤儿仓 ⇒ `null`（不打扰）。
 *
 * 只做**结构化事实**的中文装配（哪一个标的、多少张、关机前有没有兜底），不下任何新判断 ——
 * 判定性口径仍由后端那份 `orphanPositions` 决定（§3.10）。
 */
function describeOrphanPositions(rows: AiTradeEndResult["orphanPositions"]): string | null {
  if (!rows || rows.length === 0) return null;
  const parts = rows.map((row) => {
    // 措辞对冷（关机前那次核实）/ 热（刚刚平仓失败，还没核实过）两条路径都要成立，故只说
    // 「最后一次核实」而不说「关机前」——后者在热路径上是一句不成立的话。
    const cover = row.backstop?.covered ? "最后一次核实：交易所侧止损已覆盖" : "未确认有交易所侧兜底";
    if (row.positionUnknown || !row.position) return `${row.instId}（持仓未能读取，${cover}）`;
    const side = row.position.side === "long" ? "多头" : "空头";
    return `${row.instId} ${side} ${fmtNum(Number(row.position.size ?? 0))} 张（${cover}）`;
  });
  return `已结束并归档。以下持仓仍留在交易所上，从此不再由任何自动化管理，请到实盘面板自行处理：${parts.join("；")}。`;
}

// `describeStartError` 已迁到 `lib/aiTrade.ts`（F-10 / [评审 R1-1]）：续跑对话框的失败屏要渲染
// **同一份**逐标的明细，留在组件里就会被抄第二遍 —— 那是 D-5 的形状。同时它的判据从「一个 code」
// 变成了一张 code 白名单，否则本轮两个新 409 的 `members` 明细一个字都不会出现。
