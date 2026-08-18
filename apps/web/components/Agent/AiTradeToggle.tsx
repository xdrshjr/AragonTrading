"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
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
  resolveAiTradeGate,
  resolveAiTradeLifecycle,
} from "@/lib/aiTrade";
import { ApiRequestError } from "@/lib/api";
import { groupStateFromStatus } from "@/lib/useAiTradeGroupStream";
import type {
  AiTradeEndResult,
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
  const setAiTradeLifecycleNotice = useAppStore((s) => s.setAiTradeLifecycleNotice);
  const setAiTradeAcknowledged = useAppStore((s) => s.setAiTradeAcknowledged);
  const setAiTradeConfig = useAppStore((s) => s.setAiTradeConfig);
  const setAiBacktestJobId = useAppStore((s) => s.setAiBacktestJobId);
  const setAiBacktestConfig = useAppStore((s) => s.setAiBacktestConfig);
  const clearAiTradeRuntime = useAppStore((s) => s.clearAiTradeRuntime);

  const qc = useQueryClient();
  const [modalOpen, setModalOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pauseOpen, setPauseOpen] = useState(false);
  const [resumeOpen, setResumeOpen] = useState(false);
  const [endOpen, setEndOpen] = useState(false);
  const [preview, setPreview] = useState<AiTradeResumePreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const accountsQ = useQuery({ queryKey: ["okx", "accounts"], queryFn: accountApi.accounts.list });
  const defaultAccount =
    accountsQ.data?.accounts.find((a) => a.id === accountsQ.data?.defaultId) ?? null;
  const hasReadyDefault =
    !!defaultAccount && defaultAccount.hasSecret && defaultAccount.hasPassphrase;

  const agentConfigQ = useQuery({ queryKey: ["agent", "config"], queryFn: agentApi.config });
  const agentConfigured = agentConfigQ.data?.configured ?? false;

  const statusQ = useQuery({
    queryKey: ["aiTrade", "status"],
    queryFn: aiTradeApi.status,
    enabled: accountMode === "live",
    refetchInterval: 15_000,
  });
  const aiTradeEnabled = statusQ.data?.aiTradeEnabled ?? false;

  // 生命周期的唯一推导点（pause-resume §3.9.1）：任何组件都不得自己拼这个判断。
  const lifecycle = resolveAiTradeLifecycle(aiTradeGroup, aiTradeResumable);
  const running = lifecycle === "running";
  const paused = lifecycle === "paused";
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
        baseRules: opts.baseRules,               // 【基础策略】铁律（透传；取消勾选即 enabled:false）
        profitGuard: opts.profitGuard,           // 利润保护层（透传；取消勾选即 enabled:false）
        tradingHorizon: opts.tradingHorizon,     // 交易周期模式（同上；缺省=服务端默认开启）
        discipline: opts.discipline,             // 交易纪律预设（同上；缺省=服务端默认 balanced）
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
      });
      setModalOpen(false);
      setError(null);
      // 新会话起来了，上一次的生命周期提示就过期了（那笔孤儿仓的告知使命到此为止）。
      setAiTradeLifecycleNotice(null);
      qc.invalidateQueries({ queryKey: ["aiTrade", "status"] });
    },
    onError: (e) => setError(e instanceof ApiRequestError ? describeStartError(e) : "启动失败，请稍后重试"),
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
      }),
    onSuccess: (res, opts) => {
      setAiBacktestJobId(res.jobId);
      setAiBacktestConfig({
        decisionEveryBars: res.decisionEveryBars,
        contextRefreshRounds: opts.contextRefreshRounds,
      });
      setModalOpen(false);
      setError(null);
    },
    onError: (e) =>
      setError(e instanceof ApiRequestError ? e.message : "回测启动失败，请稍后重试"),
  });

  const stopM = useMutation({
    mutationFn: (id: string) => aiTradeApi.stopGroup(id),
    onSettled: () => {
      clearAiTradeRuntime();
      qc.invalidateQueries({ queryKey: ["aiTrade", "status"] });
    },
  });

  // ---- 暂停 / 继续 / 结束（pause-resume §3.9.2）----
  const pauseM = useMutation({
    mutationFn: (opts: { holdPosition: boolean }) =>
      aiTradeApi.pauseGroup(groupId ?? "", { holdPosition: opts.holdPosition }),
    onSuccess: (res) => {
      // 回执的 `notice` 是后端单点生成的判定性文案（§3.10）：它是唯一一处会说出「平仓其实没成功」
      // 与「此刻没有任何持久化兜底」的地方。只写不读 = 用户永远看不到那句话。
      setAiTradeLifecycleNotice(res.notice || null);
      setPauseOpen(false);
      setError(null);
      qc.invalidateQueries({ queryKey: ["aiTrade", "status"] });
    },
    onError: (e) => setError(e instanceof ApiRequestError ? e.message : "暂停失败，请稍后重试"),
  });

  const resumeM = useMutation({
    mutationFn: (opts: { acknowledge: boolean }) =>
      aiTradeApi.resumeGroup(groupId ?? "", { acknowledge: opts.acknowledge }),
    onSuccess: (res) => {
      // 续跑成功后热态回来了：装上分组视图并清掉冷占位（两者互斥，绝不同时非空）。
      setAiTradeGroup(groupStateFromStatus(res));
      setAiTradeResumable(null);
      setResumeOpen(false);
      setPreview(null);
      setError(null);
      qc.invalidateQueries({ queryKey: ["aiTrade", "status"] });
    },
    onError: (e) =>
      setError(e instanceof ApiRequestError ? describeStartError(e) : "继续失败，请稍后重试"),
  });

  const endM = useMutation({
    mutationFn: (opts: { flatten: boolean | null; acknowledgeOrphanPosition: boolean }) =>
      aiTradeApi.endGroup(groupId ?? "", opts),
    onSuccess: (res) => {
      clearAiTradeRuntime();
      // `orphanPositions` = 此次结束后仍留在交易所上的持仓（§5.4 要求前端据它出一条**持久**提示）。
      // 归档后 AI 面板整块消失，若这条提示也跟着消失，「这些仓从此没人管了」就再也没人告诉用户。
      setAiTradeLifecycleNotice(describeOrphanPositions(res.orphanPositions));
      setEndOpen(false);
      setResumeOpen(false);
      setError(null);
      qc.invalidateQueries({ queryKey: ["aiTrade", "status"] });
    },
    onError: (e) => setError(e instanceof ApiRequestError ? e.message : "结束失败，请稍后重试"),
  });

  // 「继续」按钮三态（`继续` / `正在核对持仓…` / 错误原因内联展示）：先拉复核数据再开框，
  // 用户看到的第一屏就是「离线期间发生了什么」，而不是一个空壳弹窗。
  const openResume = useCallback(() => {
    if (!groupId) return;
    setError(null);
    setPreview(null);
    setResumeOpen(true);
    setPreviewLoading(true);
    aiTradeApi
      .resumePreview(groupId)
      .then(setPreview)
      .catch((e: unknown) =>
        setError(e instanceof ApiRequestError ? e.message : "无法核对持仓，请稍后重试"),
      )
      .finally(() => setPreviewLoading(false));
  }, [groupId]);

  const onToggle = (next: boolean) => {
    setError(null);
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
          paused
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
          {/* 三态控件（§3.9.2）。`paused` 时开关保持 off 且**禁用**——避免「关开关」与「结束」两个
              入口语义重叠：暂停可逆、结束不可逆，二者绝不能长得像同一个动作。 */}
          <Switch
            checked={running}
            onChange={paused ? noop : onToggle}
            label="AI 自动交易开关"
            className={(openBlockedReason && !running) || paused ? "opacity-40" : undefined}
          />
        </div>

        {/* 右簇：生命周期动作。shrink-0 ⇒ 宽度压力一律流向左簇标签的省略号，按钮永不被压折。
            idle 态它是个空 div——刻意**无条件渲染**，让排版契约在三态下都有同一个落点。 */}
        <div data-testid="ai-trade-toggle-actions" className="flex shrink-0 items-center gap-1.5">
          {running && (
            <Button
              variant="ghost"
              size="sm"
              aria-label="暂停 AI 自动交易"
              onClick={() => {
                setError(null);
                setPauseOpen(true);
              }}
            >
              ⏸ 暂停
            </Button>
          )}
          {paused && (
            <>
              <Badge tone="accent">已暂停</Badge>
              <Button
                variant="primary"
                size="sm"
                aria-label="继续 AI 自动交易"
                disabled={resumeM.isPending}
                onClick={openResume}
              >
                {resumeM.isPending ? "正在核对持仓…" : "▶ 继续"}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                aria-label="结束 AI 自动交易"
                onClick={() => {
                  setError(null);
                  setEndOpen(true);
                }}
              >
                结束
              </Button>
            </>
          )}
        </div>
      </div>

      {/* 错误文案独占第二行：在通栏里再挤一段截断文本，等于把「继续 / 结束」按钮挤没。
          whitespace-normal 显式覆盖控制条继承下来的 nowrap（这是本方案里唯一需要换行的文本）。
          data-testid 是必需的：四个弹窗里的错误段落同样是 role="alert"，只靠 role 定位有歧义。
          触发面很窄——四个弹窗的 onClose 一律 setError(null)，唯一到达路径是「续跑复核在途时
          先关掉弹窗、随后复核失败」这个竞态。 */}
      {paused && error && !resumeOpen && !endOpen && (
        <span
          data-testid="ai-trade-toggle-error"
          className="whitespace-normal break-words text-[10px] leading-relaxed text-[var(--short-ink)]"
          role="alert"
        >
          {error}
        </span>
      )}

      <AiTradePauseDialog
        open={pauseOpen}
        group={aiTradeGroup}
        submitting={pauseM.isPending}
        error={pauseOpen ? error : null}
        onClose={() => {
          setPauseOpen(false);
          setError(null);
        }}
        onConfirm={(opts) => pauseM.mutate(opts)}
      />
      <AiTradeResumeDialog
        open={resumeOpen}
        preview={preview}
        loading={previewLoading}
        submitting={resumeM.isPending}
        error={resumeOpen ? error : null}
        onClose={() => {
          setResumeOpen(false);
          setError(null);
        }}
        onConfirm={(opts) => resumeM.mutate(opts)}
        onEnd={() => {
          setResumeOpen(false);
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
        error={endOpen ? error : null}
        onClose={() => {
          setEndOpen(false);
          setError(null);
        }}
        onConfirm={(opts) => endM.mutate(opts)}
      />
      <AiTradeLaunchModal
        open={modalOpen}
        onClose={() => {
          setModalOpen(false);
          setError(null);
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
        defaultProfitGuard={aiTradeConfig?.profitGuard ?? AI_TRADE_PROFIT_GUARD_DEFAULTS}
        defaultStopLossPct={aiTradeConfig?.stopLossPct ?? undefined}
        defaultTakeProfitPct={aiTradeConfig?.takeProfitPct ?? undefined}
        defaultBaseRule={aiTradeConfig?.baseRules ?? AI_TRADE_BASE_RULE_DEFAULTS}
        // 无本地预设兜底：模式表由 `GET /ai-trade/horizon-profiles` 下发，未选过时留 undefined 即可
        // （弹窗采用接口返回的 defaultProfile）——前端复制一份默认模式必然与服务端漂移（R-3）。
        defaultTradingHorizon={aiTradeConfig?.tradingHorizon}
        error={error}
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

/** 整组前置失败（AI_TRADE_GROUP_PREFLIGHT）逐标的明细中文化；否则回落 message。 */
function describeStartError(err: ApiRequestError): string {
  if (err.code === "AI_TRADE_GROUP_PREFLIGHT" && err.members?.length) {
    return err.members.map((m) => `${m.instId}：${m.message}`).join("；");
  }
  return err.message;
}
