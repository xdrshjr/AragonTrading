"use client";

import { useMemo } from "react";
import { formatSeconds as formatHoldDuration } from "@/lib/aiTradeAcceptance";
import {
  horizonCoherenceHints,
  horizonContractLines,
  type HorizonContractLine,
} from "@/lib/aiTradeHorizon";
import type {
  AiTradeAcceptanceThresholds,
  AiTradeBackstopRepairConfig,
  AiTradeBackstopRepairMode,
  AiTradeHorizonProfile,
  AiTradeHorizonProfileId,
  AiTradeHorizonProfilesResponse,
} from "@/lib/aiTradeTypes";

/**
 * 交易周期模式整段的**受控**子组件（ai-trade-holding-horizon-modes §2.11）。
 *
 * 抽出的唯一理由与 `ProfitGuardFields.tsx` 完全相同——CLAUDE.md 的 800 行红线：
 * `AiTradeLaunchModal.tsx` 开工前已 1272 行。
 *
 * **值与 setter 全部由父组件传入，本组件内不得写任何 effect**：弹窗的重置逻辑必须留在父组件那个
 * 依赖收敛为 `[open]` 的 effect 里。在子组件里另写一个 reset effect，正是
 * `ai-trade-modal-revert-recurrence` 这条复发性缺陷的根因（非稳定依赖 → 输入约 1 秒后回弹）。
 *
 * **本组件不含任何预设数值或中文标签**（R-3 / G-10）：模式表全部来自
 * `GET /ai-trade/horizon-profiles`，接口未就绪时如实显示「加载中」，绝不用一份本地副本顶上。
 */
export interface HorizonCustomFields {
  minHoldRaw: string;
  confirmRoundsRaw: string;
  hysteresisRaw: string;
  targetMinHoursRaw: string;
  targetMaxHoursRaw: string;
}

export type HorizonCustomKey = keyof HorizonCustomFields;

export interface AiTradeHorizonFieldsProps {
  data: AiTradeHorizonProfilesResponse | null;
  loading: boolean;
  enabled: boolean;
  onEnabledChange: (value: boolean) => void;
  profileId: AiTradeHorizonProfileId;
  onProfileChange: (profile: AiTradeHorizonProfile) => void;
  custom: HorizonCustomFields;
  onCustomChange: (key: HorizonCustomKey, value: string) => void;
  /** 当前表单值（契约摘要与一致性提示全部据它们**现算**，[P1-10]）。 */
  bar: string;
  leverage: number | null;
  stopLossPct: number | null;
  defaultStopLossPct: number;
  wakeMinutes: number | null;
  /** 「按模式对齐」：把 bar / 唤醒周期重新拉回本模式的建议值（非阻断提示的配套动作）。 */
  onAlign: () => void;
  /** 交易所侧兜底自愈模式（backstop-repair §2.4.1）；`null` ⇒ 用接口下发的 `defaultMode`。 */
  backstopRepairMode: AiTradeBackstopRepairMode | null;
  onBackstopRepairModeChange: (mode: AiTradeBackstopRepairMode) => void;
  disabled: boolean;
}

export default function AiTradeHorizonFields(props: AiTradeHorizonFieldsProps) {
  const { data, loading, enabled, profileId, disabled } = props;
  const profiles = data?.profiles ?? [];
  const active = profiles.find((p) => p.id === profileId) ?? null;

  const lines = useMemo<HorizonContractLine[]>(
    () =>
      data == null
        ? []
        : horizonContractLines({
            profile: active,
            bar: props.bar,
            leverage: props.leverage,
            stopLossPct: props.stopLossPct,
            defaultStopLossPct: props.defaultStopLossPct,
            estimatedCostBps: data.estimatedCostBps ?? null,
            lossReleaseFrac: data.lossReleaseFrac,
            lossReleaseFallbackPct: data.lossReleaseReturnPctFallback,
            turnoverGateEnabled: data.turnoverGateEnabled,
          }),
    [data, active, props.bar, props.leverage, props.stopLossPct, props.defaultStopLossPct],
  );
  const hints = useMemo(
    () =>
      horizonCoherenceHints({
        profile: active,
        bar: props.bar,
        wakeMinutes: props.wakeMinutes,
        leverage: props.leverage,
      }),
    [active, props.bar, props.wakeMinutes, props.leverage],
  );

  return (
    <div
      className="flex flex-col gap-1.5 rounded-md border border-[var(--accent-soft)] bg-[color-mix(in_srgb,var(--accent)_5%,transparent)] px-3 py-2"
      data-testid="ai-trade-horizon"
    >
      <label className="flex cursor-pointer items-start gap-2 text-xs">
        <input
          type="checkbox"
          checked={enabled}
          disabled={disabled}
          onChange={(e) => props.onEnabledChange(e.target.checked)}
          className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
          aria-label="交易周期模式（推荐开启）"
        />
        <span className="flex flex-col gap-0.5">
          <span className="font-medium text-[var(--text)]">交易周期模式（推荐开启）</span>
          <span className="text-[11px] leading-relaxed text-[var(--muted)]">
            决定 AI <strong className="text-[var(--text)]">应该持有多久</strong>：一次选择即同时约束
            执行周期建议、唤醒节奏与三条<strong className="text-[var(--text)]">确定性换手约束</strong>
            （最短主动平仓窗口 / 离场连续确认 / 换手迟滞）。
            <strong className="text-[var(--text)]">只约束 AI 主动发起的平仓与反手</strong>——交易所
            止损止盈、利润保护层、会话止盈损随时可以平仓，不受本层任何限制。
          </span>
        </span>
      </label>

      {enabled && (loading || data == null ? <LoadingHint failed={!loading} /> : (
        <div className="mt-1 flex flex-col gap-2">
          <ProfileGrid
            profiles={profiles}
            defaultProfile={data.defaultProfile}
            activeId={profileId}
            disabled={disabled}
            onSelect={props.onProfileChange}
          />
          <ContractSummary lines={lines} />
          <AcceptancePreview
            acceptance={active?.acceptance ?? null}
            turnoverGateArmed={data.turnoverGateEnabled !== false
              && (active?.turnoverBudgetPerDay ?? 0) > 0}
          />
          <BackstopNotice profile={active} response={data} />
          {/* [P1-10] 自愈控件**不受跨日条件门控**：随单 OCO 丢失与持仓周期长短无关，
              `hourly_swing` 的仓一样会裸露。四个预设 + custom 一律渲染。 */}
          <BackstopRepairField
            config={data.backstopRepair ?? null}
            mode={props.backstopRepairMode}
            onChange={props.onBackstopRepairModeChange}
            disabled={disabled}
          />
          {active?.bar == null && (
            <CustomFields custom={props.custom} onChange={props.onCustomChange} disabled={disabled} />
          )}
          {hints.length > 0 && (
            <CoherenceHints hints={hints} onAlign={props.onAlign} disabled={disabled} />
          )}
        </div>
      ))}
    </div>
  );
}

/**
 * 预设表尚不可用时的占位。
 *
 * **读取失败必须说是失败**：本地不保留副本（R-3），此时卡片确实画不出来；一直挂着「正在读取…」
 * 会让用户以为还在转圈，而实际上本次启动会走服务端默认模式——静默的降级与静默的保守一样，都会被
 * 当成故障（本仓库成文纪律）。
 */
function LoadingHint({ failed }: { failed: boolean }) {
  return (
    <p className="mt-1 text-[11px] text-[var(--muted)]" data-testid="ai-trade-horizon-loading">
      {failed
        ? "周期模式预设读取失败：本地不保留副本，故此处画不出卡片。本次启动仍会由服务端按默认模式装配；如需自选模式请重开弹窗重试。"
        : "正在读取周期模式预设…（数值与说明由服务端下发，本地不保留副本）"}
    </p>
  );
}

/**
 * 4 + 1 张卡片的单选组：`role="radiogroup"`，卡片本身是 `<button role="radio">`，支持方向键切换，
 * `aria-checked` 正确反映状态（人机交互最佳实践：单选语义不该靠一堆看起来像卡片的 div 来表达）。
 */
function ProfileGrid({
  profiles,
  defaultProfile,
  activeId,
  disabled,
  onSelect,
}: {
  profiles: AiTradeHorizonProfile[];
  defaultProfile: string;
  activeId: string;
  disabled: boolean;
  onSelect: (profile: AiTradeHorizonProfile) => void;
}) {
  const move = (delta: number) => {
    if (disabled || profiles.length === 0) return;
    const index = Math.max(0, profiles.findIndex((p) => p.id === activeId));
    const next = (index + delta + profiles.length) % profiles.length;
    onSelect(profiles[next]);
  };
  return (
    <div
      role="radiogroup"
      aria-label="交易周期模式"
      className="grid grid-cols-2 gap-1.5 max-[420px]:grid-cols-1"
      onKeyDown={(e) => {
        if (e.key === "ArrowRight" || e.key === "ArrowDown") {
          e.preventDefault();
          move(1);
        } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
          e.preventDefault();
          move(-1);
        }
      }}
    >
      {profiles.map((profile) => (
        <ProfileCard
          key={profile.id}
          profile={profile}
          checked={profile.id === activeId}
          recommended={profile.id === defaultProfile}
          disabled={disabled}
          onSelect={() => onSelect(profile)}
        />
      ))}
    </div>
  );
}

function ProfileCard({
  profile,
  checked,
  recommended,
  disabled,
  onSelect,
}: {
  profile: AiTradeHorizonProfile;
  checked: boolean;
  recommended: boolean;
  disabled: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      tabIndex={checked ? 0 : -1}
      disabled={disabled}
      onClick={onSelect}
      title={profile.description}
      className="flex flex-col gap-0.5 rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-left transition-colors hover:bg-[var(--surface-2)] disabled:opacity-50 aria-[checked=true]:border-[var(--accent)] aria-[checked=true]:bg-[color-mix(in_srgb,var(--accent)_10%,transparent)]"
    >
      <span className="flex items-center justify-between gap-1">
        <span className="text-xs font-medium text-[var(--text)]">
          {checked ? "● " : "○ "}
          {profile.title}
        </span>
        {recommended && <span className="text-[9px] text-[var(--accent-ink)]">推荐</span>}
      </span>
      <span className="text-[10px] text-[var(--muted)]">
        {profile.targetLabel ? `目标持仓 ${profile.targetLabel}` : profile.description}
      </span>
      {profile.bar != null && (
        <span className="text-[10px] tabular text-[var(--muted)]">
          {profile.bar} · {describeWake(profile.wakeIntervalMinutes)}
          {profile.leverageMaxHint != null ? ` · ≤${profile.leverageMaxHint}×` : ""}
        </span>
      )}
    </button>
  );
}

function describeWake(minutes?: number | null): string {
  if (minutes == null) return "不联动";
  return minutes > 0 ? `唤醒 ${minutes}min` : "仅K线驱动";
}

/**
 * 契约摘要——本设计最重要的一块信息，**必须默认可见、不藏在 details 里**：用户在这里读到的就是
 * 系统将强制执行的东西，每一行的数都随同屏表单值现算（无静态算例，G-12）。
 */
function ContractSummary({ lines }: { lines: HorizonContractLine[] }) {
  if (lines.length === 0) return null;
  return (
    <div
      className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5"
      data-testid="ai-trade-horizon-contract"
    >
      <p className="text-[10px] font-medium text-[var(--text)]">本模式下系统将强制执行：</p>
      <ul className="mt-0.5 flex flex-col gap-0.5">
        {lines.map((line) => (
          <li key={line.key} className="text-[10px] leading-relaxed text-[var(--muted)]">
            · {line.text}
          </li>
        ))}
      </ul>
      <p className="mt-1 text-[10px] leading-relaxed text-[var(--muted)]">
        利润保护层有它<strong className="text-[var(--text)]">自己的最短持有期</strong>，与本层无关、
        不受本层影响；上述所有限制也不影响交易所侧止损止盈与会话级止盈止损。
      </p>
    </div>
  );
}

/**
 * 验收门槛预览（ai-trade-horizon-acceptance-loop §4.1）。
 *
 * 让用户在**选模式那一刻**就看见「这套配置将按什么标准被判定」，而不是跑完一周才发现门槛是什么。
 * 数值全部由 `GET /ai-trade/horizon-profiles` 现算下发（与会话跑起来后真正据以判定的是同一处
 * `resolve_thresholds`），本组件**不做任何计算**——前端自己乘一遍就会与后端判定分叉。
 *
 * `custom` 未填目标区间 ⇒ 派生阈为 null ⇒ 如实说「按你填的目标区间派生」，绝不编一个数。
 */
function AcceptancePreview({
  acceptance,
  turnoverGateArmed,
}: {
  acceptance: AiTradeAcceptanceThresholds | null;
  turnoverGateArmed: boolean;
}) {
  if (!acceptance) return null;
  const minAvgHold = acceptance.minAvgHoldSeconds;
  return (
    <div
      className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5"
      data-testid="ai-trade-acceptance-preview"
    >
      <p className="text-[10px] font-medium text-[var(--text)]">
        跑起来后将按以下门槛判定（统计看板的「周期验收」卡）：
      </p>
      <ul className="mt-0.5 flex flex-col gap-0.5 text-[10px] leading-relaxed text-[var(--muted)]">
        <li>
          · 平均持仓时间 ≥{" "}
          {minAvgHold != null ? formatHoldDuration(minAvgHold) : "按你填的目标区间派生"}
          ；落在目标区间的比例 ≥{" "}
          {acceptance.minWithinTargetRate != null
            ? `${(acceptance.minWithinTargetRate * 100).toFixed(0)}%`
            : "按你填的目标区间派生"}
        </li>
        <li>
          · 每日换手次数（墙钟口径）≤{" "}
          {acceptance.maxTurnoverPerDay != null
            ? `${acceptance.maxTurnoverPerDay.toFixed(1)} 次`
            : "按你填的目标区间派生"}
          {/* 自指披露（turnover-budget §2.2.6）：这一行已从「事后判据」变成「运行期约束」，
              因而接近恒真。用户必须知道这件事，否则会把一个恒绿的格子当成通过。 */}
          {turnoverGateArmed ? (
            <span
              className="ml-1 rounded-[3px] border border-[var(--border)] px-1 py-px text-[9px] text-[var(--muted)]"
              data-testid="ai-turnover-gate-badge"
              title={
                "本会话开启了换手预算闸：超出该上限即暂停开新仓（平仓不受影响），" +
                "预算按本会话计、会话重启后重新计算。因此本行接近恒真，请以「平均持仓时间」" +
                "「手续费占毛利润」「资金费后净收益」三行为准。"
              }
            >
              运行期强制
            </span>
          ) : null}
          ；手续费占毛利润 ≤ {(acceptance.maxFeeToGross * 100).toFixed(0)}%
        </li>
        <li>· 扣掉手续费与<strong className="text-[var(--text)]">资金费</strong>后的净收益 &gt; 0</li>
        <li>
          · 满 {acceptance.sampleFloor} 笔完整往返才出结论；不足即显示
          <strong className="text-[var(--text)]">「样本不足」</strong>
          （灰色，不是「快通过了」），此时不宜放大实盘资金。
        </li>
      </ul>
    </div>
  );
}

/**
 * 跨日模式的兜底核实提示（turnover-budget §2.4.1）——**非阻断**。
 *
 * 跨日持有「不必每轮盯盘」这句授权的唯一前提，是交易所侧那两张单真的还挂着。此前系统从未核实过
 * 它，只是把下单时的**本地回执**当成事实。这行提示告诉用户：现在会真的去问，而且问不到时会如实
 * 告诉 AI。
 *
 * [P2-4] 核实周期的分钟数由服务端下发（`backstopPollSeconds`），**前端不得硬抄一个 2 分钟**——
 * 运维改了配置而文案不动，就又造出一句没核实的话，那正是本轮要根除的东西。
 */
function BackstopNotice({
  profile,
  response,
}: {
  profile: AiTradeHorizonProfile | null;
  response: AiTradeHorizonProfilesResponse;
}) {
  const crossesDay = (profile?.targetHoldMaxSeconds ?? 0) >= 86400;
  if (!crossesDay) return null;
  if (response.backstopCheckEnabled === false) {
    return (
      <p
        className="text-[10px] leading-relaxed text-[var(--muted)]"
        data-testid="ai-trade-backstop-notice"
      >
        本会话<strong className="text-[var(--text)]">没有向交易所核实止损的通道</strong>
        （运维已关闭核实）：随单挂出的止盈止损只有本地下单回执可证，
        请不要把「进程退出后仍生效」当作前提。
      </p>
    );
  }
  const minutes = Math.max(1, Math.round((response.backstopPollSeconds ?? 120) / 60));
  return (
    <p
      className="text-[10px] leading-relaxed text-[var(--muted)]"
      data-testid="ai-trade-backstop-notice"
    >
      本会话将每 <strong className="text-[var(--text)]">{minutes}</strong> 分钟向交易所核实一次
      止损是否仍然挂着；核实不到时系统会
      <strong className="text-[var(--text)]">如实告知 AI</strong>
      ，并停止在未覆盖的持仓上继续加仓（平仓与新开仓不受影响）。
    </p>
  );
}

/**
 * 交易所侧兜底自愈的两段式控件（backstop-repair §2.4.1）。
 *
 * 它回答的是一个此前系统「知道却什么都不做」的问题：向交易所核实到「止损单不存在 / 覆盖不足」
 * 之后要不要**自动把那张单补回来**。默认 `plan`（只给方案、一键人工执行）——这是本仓库第一条
 * AI 侧自动下单路径，默认自动执行与默认死码之间的第三条路是「默认给方案 + 一键执行」。
 *
 * 三条 UI 纪律：
 * 1. **整块由后端下发**（`backstopRepair`）：标签、说明、缺省模式、两个上限，前端一个字都不硬编；
 *    整块缺席（运维总闸关）⇒ 控件不渲染。
 * 2. **默认选中项来自 `defaultMode`**，不写死 `"plan"`。
 * 3. 选中「自动补挂」时追加一行 `--accent` 授权确认语——这是本轮唯一一处「系统会自己花钱」的
 *    授权点，必须让用户在点「启动」之前读到。
 */
function BackstopRepairField({
  config,
  mode,
  onChange,
  disabled,
}: {
  config: AiTradeBackstopRepairConfig | null;
  mode: AiTradeBackstopRepairMode | null;
  onChange: (mode: AiTradeBackstopRepairMode) => void;
  disabled: boolean;
}) {
  if (!config) return null;
  const active = mode ?? config.defaultMode;
  const armed = active === "arm";
  return (
    <div className="flex flex-col gap-1" data-testid="ai-trade-backstop-repair">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] text-[var(--muted)]">交易所侧兜底自愈</span>
        <div
          role="radiogroup"
          aria-label="交易所侧兜底自愈"
          className="flex overflow-hidden rounded-[4px] border border-[var(--border)]"
        >
          {config.modes.map((option) => (
            <button
              key={option.id}
              type="button"
              role="radio"
              aria-checked={active === option.id}
              disabled={disabled}
              title={option.detail}
              onClick={() => onChange(option.id)}
              data-testid={`ai-trade-backstop-repair-${option.id}`}
              className={`px-2 py-0.5 text-[11px] ${
                active === option.id
                  ? "bg-[var(--accent)] text-[var(--on-accent)]"
                  : "text-[var(--muted)]"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        {config.modes.find((m) => m.id === active)?.detail}；{config.note}
        单个持仓周期最多 <strong className="text-[var(--text)]">{config.maxPerEpoch}</strong> 次、
        单会话最多 <strong className="text-[var(--text)]">{config.maxPerSession}</strong> 次。
      </p>
      {armed ? (
        <p
          className="text-[10px] leading-relaxed text-[var(--accent-ink)]"
          data-testid="ai-trade-backstop-repair-consent"
        >
          本会话将允许系统在你不在场时向交易所发出止损委托。
        </p>
      ) : null}
    </div>
  );
}

/** 自定义模式的高级参数（全部留空/默认即**逐字等价于不装配本层**——这是本特性的安全退出口）。 */
function CustomFields({
  custom,
  onChange,
  disabled,
}: {
  custom: HorizonCustomFields;
  onChange: (key: HorizonCustomKey, value: string) => void;
  disabled: boolean;
}) {
  return (
    <div className="grid grid-cols-2 gap-2 text-[11px] text-[var(--muted)]">
      <HorizonField
        label="最短主动平仓窗口（秒，0=不约束）"
        value={custom.minHoldRaw}
        onChange={(v) => onChange("minHoldRaw", v)}
        disabled={disabled}
      />
      <HorizonField
        label="离场连续确认轮数（1=不约束）"
        value={custom.confirmRoundsRaw}
        onChange={(v) => onChange("confirmRoundsRaw", v)}
        disabled={disabled}
      />
      <HorizonField
        label="换手迟滞倍数（0=不约束）"
        value={custom.hysteresisRaw}
        onChange={(v) => onChange("hysteresisRaw", v)}
        disabled={disabled}
      />
      <HorizonField
        label="目标持仓下界（小时）"
        value={custom.targetMinHoursRaw}
        onChange={(v) => onChange("targetMinHoursRaw", v)}
        disabled={disabled}
      />
      <HorizonField
        label="目标持仓上界（小时）"
        value={custom.targetMaxHoursRaw}
        onChange={(v) => onChange("targetMaxHoursRaw", v)}
        disabled={disabled}
      />
      <p className="col-span-2 leading-relaxed">
        目标持仓上界还决定<strong className="text-[var(--text)]">成本模型的资金费期数</strong>与
        信号先验的前瞻窗口（不只是文案）；全部留空即不施加任何换手约束、成本与先验窗口逐字回退默认。
      </p>
    </div>
  );
}

function HorizonField({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span>{label}</span>
      <input
        type="text"
        inputMode="decimal"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        aria-label={label}
        className="rounded border border-[var(--border)] bg-[var(--bg)] px-1.5 py-1 text-right text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
      />
    </label>
  );
}

/** 一致性提示：**非阻断**（静默夹紧比不夹更危险——杠杆与周期都是用户的决策）。 */
function CoherenceHints({
  hints,
  onAlign,
  disabled,
}: {
  hints: string[];
  onAlign: () => void;
  disabled: boolean;
}) {
  return (
    <div
      className="flex flex-col gap-1 rounded-md border border-[var(--border)] bg-[var(--surface-2)] px-2.5 py-1.5"
      data-testid="ai-trade-horizon-hints"
    >
      {hints.map((hint) => (
        <p key={hint} className="text-[10px] leading-relaxed text-[var(--muted)]">
          ⚠ {hint}
        </p>
      ))}
      <button
        type="button"
        disabled={disabled}
        onClick={onAlign}
        className="self-start text-[10px] font-medium text-[var(--accent-ink)] hover:underline disabled:opacity-50"
      >
        按模式对齐（重置时间级别与唤醒周期）
      </button>
    </div>
  );
}
