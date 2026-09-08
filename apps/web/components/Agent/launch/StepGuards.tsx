"use client";

import { Select } from "@/components/ui";
import { AI_TRADE_BAND_TOLERANCE_OPTIONS } from "@/lib/aiTrade";
import { risksForStep } from "@/lib/aiTradeLaunchRisk";
import { guardPreview } from "@/lib/aiTradeLaunchFormat";
import AiTradeDirectionFields from "../AiTradeDirectionFields";
import AiTradeTradabilityFields from "../AiTradeTradabilityFields";
import ProfitGuardFields from "../ProfitGuardFields";
import DisciplinePresetFields from "./DisciplinePresetFields";
import { InlineRiskNote } from "./LaunchPrimitives";
import type { UseAiTradeLaunchForm } from "./useAiTradeLaunchForm";

/**
 * 第 5 步：**什么时候不开单、浮盈怎么兑现**（ai-trade-guided-launch §4.2.1）。
 *
 * 承载今天弹窗里的：交易纪律预设、基础策略铁律、方向对称 · 趋势基调、可交易性预检、利润保护层。
 * 它排在 `review` 之前是刻意的——本轮轴 A 的主体正是「利润保护」那一格，用户关掉它之后
 * **下一屏**就会看到后果，而不是翻回去找。
 */
/**
 * 预检结论由预检卡的 `ai-tradability-advice` 就地说出口；失效阈值那一行由只读阈值表的删除线 +
 * 「未装配，不生效」尾注就地说出口。剩下的 `profitGuardOff` 走通用条目——它是本轮从 422 降级
 * 下来的那条披露，必须在产地这一步就说一次，而不是等到第 6 步。
 */
const ALREADY_INLINE = new Set(["tradabilityInfeasible", "inertThresholds"]);

export default function StepGuards({
  form,
  submitting,
}: {
  form: UseAiTradeLaunchForm;
  submitting: boolean;
}) {
  const {
    mode, disciplinePreset, setDisciplinePreset, disciplineQ, inertThresholdKeys,
    hardTakeProfitText, baseRuleEnabled, setBaseRuleEnabled, baseRulePeriodRaw,
    setBaseRulePeriodRaw, baseRuleStdRaw, setBaseRuleStdRaw, baseRuleTolerance,
    setBaseRuleTolerance, baseRuleDirectional, setBaseRuleDirectional,
    directionPayload, setDirectionEnabled, directionConfidenceRaw, setDirectionConfidenceRaw,
    directionQuotaRaw, setDirectionQuotaRaw, directionSizeMultRaw, setDirectionSizeMultRaw,
    directionError, trendPayload, setTrendEnabled, setTrendCounterGate, setTrendVoteEnabled,
    setTrendAllowanceEnabled, trendConfidenceRaw, setTrendConfidenceRaw, trendSizeMultRaw,
    setTrendSizeMultRaw, trendError,
    tradabilityView, tradabilityQ, tradabilityConfirmed, setTradabilityConfirmed, setBar,
    profitGuardEnabled, setProfitGuardEnabled, armReturnRaw, setArmReturnRaw, givebackRaw,
    setGivebackRaw, breakevenArmRaw, setBreakevenArmRaw, minHoldRaw, setMinHoldRaw,
    tierPreset, setTierPreset, atrFloorRaw, setAtrFloorRaw, atrCapRaw, setAtrCapRaw,
    cooldownRaw, setCooldownRaw, armPriceMovePct, interlockArmed, risks,
  } = form;
  const stepRisks = risksForStep(risks, "guards");

  return (
    <div className="flex flex-col gap-3">
      {/* 交易纪律预设：**实盘与回测两段都渲染**（两侧走同一个服务端解析点）。 */}
      <DisciplinePresetFields
        value={disciplinePreset}
        onChange={setDisciplinePreset}
        disabled={submitting}
        table={disciplineQ.data ?? null}
        inertKeys={inertThresholdKeys}
      />

      {/* 【基础策略】铁律区块：它不是策略库里的第四个条目，而是凌驾于所选全部策略之上的开单纪律。 */}
      <div className="flex flex-col gap-1.5 rounded-md border border-[var(--accent-soft)] bg-[color-mix(in_srgb,var(--accent)_5%,transparent)] px-3 py-2">
        <label className="flex cursor-pointer items-start gap-2 text-xs">
          <input
            type="checkbox"
            checked={baseRuleEnabled}
            disabled={submitting}
            onChange={(e) => setBaseRuleEnabled(e.target.checked)}
            className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
            aria-label="基础策略（铁律 · 默认开启）"
          />
          <span className="flex flex-col gap-0.5">
            <span className="font-medium text-[var(--text)]">基础策略（铁律 · 默认开启）</span>
            <span className="text-[11px] leading-relaxed text-[var(--muted)]">
              <strong className="text-[var(--text)]">MA 通道开单约束</strong>：无论选了哪些交易策略，
              开多 / 开空只允许在所选时间级别 MA 通道的
              <strong className="text-[var(--text)]">上轨、中轨或下轨附近</strong>发生，
              不满足即由系统直接降级为观望（限价委托按
              <strong className="text-[var(--text)]">委托价</strong>判定）。 平仓与撤单不受限制。
            </span>
          </span>
        </label>
        {baseRuleEnabled && (
          <details className="text-[11px] text-[var(--muted)]">
            <summary className="cursor-pointer select-none text-[var(--accent-ink)]">高级参数</summary>
            <div className="mt-1.5 flex flex-col gap-1.5">
              <div className="flex items-center gap-2">
                <label className="flex flex-1 items-center gap-1.5">
                  <span className="shrink-0">均线周期</span>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={baseRulePeriodRaw}
                    disabled={submitting}
                    onChange={(e) => setBaseRulePeriodRaw(e.target.value)}
                    aria-label="基础策略均线周期"
                    className="w-full rounded border border-[var(--border)] bg-[var(--bg)] px-1.5 py-1 text-right text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
                  />
                </label>
                <label className="flex flex-1 items-center gap-1.5">
                  <span className="shrink-0">带宽倍数 σ</span>
                  <input
                    type="text"
                    inputMode="decimal"
                    value={baseRuleStdRaw}
                    disabled={submitting}
                    onChange={(e) => setBaseRuleStdRaw(e.target.value)}
                    aria-label="基础策略带宽倍数"
                    className="w-full rounded border border-[var(--border)] bg-[var(--bg)] px-1.5 py-1 text-right text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
                  />
                </label>
              </div>
              <label className="flex flex-col gap-1">
                <span>「附近」的容差（带宽归一，跨周期同义）</span>
                <Select
                  value={String(baseRuleTolerance)}
                  disabled={submitting}
                  onChange={(e) => setBaseRuleTolerance(Number(e.target.value))}
                  className="text-xs"
                  aria-label="基础策略容差"
                >
                  {AI_TRADE_BAND_TOLERANCE_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </label>
              <label className="flex cursor-pointer items-start gap-2">
                <input
                  type="checkbox"
                  checked={baseRuleDirectional}
                  disabled={submitting}
                  onChange={(e) => setBaseRuleDirectional(e.target.checked)}
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
                  aria-label="基础策略方向性模式"
                />
                <span className="leading-relaxed">
                  方向性（做多只许下轨 / 中轨，做空只许上轨 / 中轨）——
                  <strong className="text-[var(--text)]">保守模式，趋势突破类策略慎用</strong>。
                </span>
              </label>
            </div>
          </details>
        )}
      </div>

      {/* 方向对称 · 逆势通路：紧接「交易纪律预设」——那张阈值表里的「同向共识门槛」正是本块要
          放开的那一道闸，两块不相邻就读不懂。 */}
      <AiTradeDirectionFields
        value={directionPayload}
        onChange={(next) => setDirectionEnabled(next.contrarianEnabled)}
        confidenceRaw={directionConfidenceRaw}
        quotaRaw={directionQuotaRaw}
        sizeMultRaw={directionSizeMultRaw}
        onRawChange={(key, raw) => {
          if (key === "confidence") setDirectionConfidenceRaw(raw);
          else if (key === "quota") setDirectionQuotaRaw(raw);
          else setDirectionSizeMultRaw(raw);
        }}
        disabled={submitting}
        error={directionError}
        trend={trendPayload}
        onTrendChange={(next) => {
          setTrendEnabled(next.enabled);
          setTrendCounterGate(next.counterGateEnabled !== false);
          setTrendVoteEnabled(next.consensusVoteEnabled !== false);
          setTrendAllowanceEnabled(next.contrarianAllowanceEnabled !== false);
        }}
        trendConfidenceRaw={trendConfidenceRaw}
        trendSizeMultRaw={trendSizeMultRaw}
        onTrendRawChange={(key, raw) => {
          if (key === "confidence") setTrendConfidenceRaw(raw);
          else setTrendSizeMultRaw(raw);
        }}
        trendError={trendError}
      />

      {/* 可交易性预检：挂在「交易纪律」正下方——它算的正是「这组纪律阈值在这个周期上会拦掉多少」。
          取数已上提到 hook（【v2 · P0-2】），本组件降为**纯展示**：即便用户从不来第 5 步，探针
          照样在跑、结论照样进第 6 步的风险清单。 */}
      <AiTradeTradabilityFields
        data={tradabilityQ.data ?? null}
        loading={tradabilityQ.isPending && tradabilityQ.fetchStatus !== "idle"}
        failed={tradabilityQ.isError}
        onPickBar={(next) => setBar(next as never)}
        confirmed={tradabilityConfirmed}
        onConfirmedChange={setTradabilityConfirmed}
        disabled={submitting}
      />

      {/* 利润保护层（**实盘与回测两段都渲染**）：本轮轴 A 的主体。 */}
      <ProfitGuardFields
        mode={mode}
        enabled={profitGuardEnabled}
        onEnabledChange={setProfitGuardEnabled}
        armReturnRaw={armReturnRaw}
        onArmReturnChange={setArmReturnRaw}
        givebackRaw={givebackRaw}
        onGivebackChange={setGivebackRaw}
        breakevenArmRaw={breakevenArmRaw}
        onBreakevenArmChange={setBreakevenArmRaw}
        minHoldRaw={minHoldRaw}
        onMinHoldChange={setMinHoldRaw}
        tierPreset={tierPreset}
        onTierPresetChange={setTierPreset}
        atrFloorRaw={atrFloorRaw}
        onAtrFloorChange={setAtrFloorRaw}
        atrCapRaw={atrCapRaw}
        onAtrCapChange={setAtrCapRaw}
        cooldownRaw={cooldownRaw}
        onCooldownChange={setCooldownRaw}
        disabled={submitting}
        preview={guardPreview(armReturnRaw, givebackRaw, armPriceMovePct)}
        hardTakeProfitText={hardTakeProfitText}
        interlockArmed={interlockArmed}
      />

      {/* 本步**已由自己的控件就地说出口**的风险（见 ALREADY_INLINE）不再走下面这条通用条目，
          否则同一句话会在同一屏上出现两次——§4.2.5 渲染规则 1 要的是「就地说一次」，
          不是「说两次」。清单外的风险由通用条目兜底：新加一条风险时二选一，别两边都不管。 */}
      {stepRisks
        .filter((risk) => !ALREADY_INLINE.has(risk.id))
        .map((risk) => (
          <InlineRiskNote key={risk.id} tone={risk.tone} title={risk.title} detail={risk.detail} />
        ))}
    </div>
  );
}
