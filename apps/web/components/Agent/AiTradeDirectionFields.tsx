"use client";

import type { AiTradeDirectionRule, AiTradeTrendRule } from "@/lib/aiTradeTypes";

/**
 * 启动弹窗的「方向对称 · 逆势通路」整段（**受控**子组件，镜像 `AiTradeExperienceFields` /
 * `ProfitGuardFields` / `AiTradeHorizonFields`）。
 *
 * **值与 setter 全部由父组件传入，本组件内不得写任何 effect**：弹窗的重置逻辑必须留在父组件
 * 那个依赖收敛为 `[open]` 的 effect 里。在子组件里另写一个 reset effect，正是
 * `ai-trade-modal-revert-recurrence` 这条复发性缺陷的根因（非稳定依赖 → 输入约 1 秒后回弹）。
 *
 * **本组件不硬编码任何默认数值**：三个输入留空即用服务端默认（唯一真源在 `config.py`）。
 * 前端存一份默认值，下次后端调参时这块面板就会与真实生效值静默分叉——而它恰恰是用户按下启动键
 * 之前唯一能看到的依据。
 */
export interface AiTradeDirectionFieldsProps {
  value: AiTradeDirectionRule;
  onChange: (next: AiTradeDirectionRule) => void;
  /** 三个数值输入的 raw 串（避免输入中途被 Number 归一，与既有金额输入同风格）。 */
  confidenceRaw: string;
  quotaRaw: string;
  sizeMultRaw: string;
  onRawChange: (key: "confidence" | "quota" | "sizeMult", raw: string) => void;
  disabled?: boolean;
  /** 校验错误文案（父组件用 `validateDirectionRule` 现算）；`null` = 合法。 */
  error?: string | null;
  // ---- 执行周期趋势基调（execution-trend-verdict §5.1 / 评审 RP1-12）----
  // RK-1 的缓解④「用户能立刻关掉逆势闸」此前**只有一个运维 env、没有任何接口**：一个只能靠
  // 改环境变量回退的安全阀，对桌面端用户等于不存在。这四个受控值与上面三个 raw 串同款——
  // **全部由父组件持有**，本组件内不得写任何 effect（那正是 ai-trade-modal-revert-recurrence
  // 这条复发性缺陷的根因）。
  trend: AiTradeTrendRule;
  onTrendChange: (next: AiTradeTrendRule) => void;
  trendConfidenceRaw: string;
  trendSizeMultRaw: string;
  onTrendRawChange: (key: "confidence" | "sizeMult", raw: string) => void;
  trendError?: string | null;
}

export default function AiTradeDirectionFields({
  value,
  onChange,
  confidenceRaw,
  quotaRaw,
  sizeMultRaw,
  onRawChange,
  disabled = false,
  error = null,
  trend,
  onTrendChange,
  trendConfidenceRaw,
  trendSizeMultRaw,
  onTrendRawChange,
  trendError = null,
}: AiTradeDirectionFieldsProps) {
  const enabled = value.contrarianEnabled;
  return (
    <section
      className="flex flex-col gap-1.5 rounded-md border border-[var(--border)] px-3 py-2"
      data-testid="ai-trade-direction-fields"
      aria-disabled={disabled || undefined}
    >
      <label className="flex items-center gap-2 text-[11px] text-[var(--text)]">
        <input
          type="checkbox"
          checked={enabled}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, contrarianEnabled: e.target.checked })}
          aria-label="启用逆势通路"
        />
        <span>启用逆势通路（推荐）</span>
      </label>

      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        允许 Agent 在有充分证据时逆信号板方向开仓，仓位强制减半。关闭后，Agent
        <strong className="text-[var(--text)]">只能顺着信号板的方向开仓</strong>
        ——单边行情里这等于只做一个方向。
      </p>

      {enabled && (
        <>
          <div className="grid grid-cols-3 gap-2">
            <NumberField
              label="置信度下界"
              hint="0~1"
              value={confidenceRaw}
              disabled={disabled}
              onChange={(raw) => onRawChange("confidence", raw)}
              testId="ai-direction-confidence"
            />
            <NumberField
              label="会话配额"
              hint="次"
              value={quotaRaw}
              disabled={disabled}
              onChange={(raw) => onRawChange("quota", raw)}
              testId="ai-direction-quota"
            />
            <NumberField
              label="降档系数"
              hint="0~1"
              value={sizeMultRaw}
              disabled={disabled}
              onChange={(raw) => onRawChange("sizeMult", raw)}
              testId="ai-direction-size-mult"
            />
          </div>
          <p className="text-[10px] leading-relaxed text-[var(--muted)]">
            三项留空即用服务端默认。配额
            <strong className="text-[var(--text)]">不落库</strong>
            ：重启会话会重新计数。
          </p>
        </>
      )}

      {error && (
        <p
          role="alert"
          className="text-[10px] leading-relaxed text-[var(--short-ink)]"
          data-testid="ai-direction-error"
        >
          {error}
        </p>
      )}

      <TrendGroup
        value={trend}
        onChange={onTrendChange}
        confidenceRaw={trendConfidenceRaw}
        sizeMultRaw={trendSizeMultRaw}
        onRawChange={onTrendRawChange}
        disabled={disabled}
        error={trendError}
      />
    </section>
  );
}

/**
 * 《趋势基调》分组（execution-trend-verdict §5.1）。
 *
 * 紧接「逆势通路」——两块讲的是同一件事的两半：那一块问「信号板一边倒时还能不能开反向」，
 * 这一块问「本级别的方向基调是什么、逆着它开要付什么代价」。
 *
 * **三把子闸的极性刻意分开列**（§6.3）：`counterGateEnabled` 是唯一会**减少**成交的那一把，
 * 另两把是唯二会**增加**成交的通路。绑在一起，用户就无法只回退「变宽」的那一半。
 * 两个数值输入留空即用服务端默认——前端不存任何默认数值（唯一真源在 `config.py`）。
 */
function TrendGroup({
  value,
  onChange,
  confidenceRaw,
  sizeMultRaw,
  onRawChange,
  disabled,
  error,
}: {
  value: AiTradeTrendRule;
  onChange: (next: AiTradeTrendRule) => void;
  confidenceRaw: string;
  sizeMultRaw: string;
  onRawChange: (key: "confidence" | "sizeMult", raw: string) => void;
  disabled: boolean;
  error: string | null;
}) {
  const armed = value.enabled;
  return (
    <div
      className="mt-1 flex flex-col gap-1.5 border-t border-[var(--border)] pt-2"
      data-testid="ai-trade-trend-fields"
    >
      <label className="flex items-center gap-2 text-[11px] text-[var(--text)]">
        <input
          type="checkbox"
          checked={armed}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, enabled: e.target.checked })}
          aria-label="启用执行周期趋势基调"
        />
        <span>启用执行周期趋势基调（推荐）</span>
      </label>

      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        每轮先在
        <strong className="text-[var(--text)]">你所选的 K 线级别</strong>
        上判一次涨跌基调，把它作为研判的第一步；更高周期只作权重受限的调制项——它可以削弱基调
        的强度，
        <strong className="text-[var(--text)]">不能替 Agent 决定这个级别的方向</strong>。
        关闭后，研判的第一步会退回「先读日线」。
      </p>

      {armed && (
        <>
          <label className="flex items-center gap-2 text-[11px] text-[var(--text)]">
            <input
              type="checkbox"
              checked={value.counterGateEnabled !== false}
              disabled={disabled}
              onChange={(e) => onChange({ ...value, counterGateEnabled: e.target.checked })}
              aria-label="启用逆势降档闸"
            />
            <span>逆势开仓需更高置信度并强制降档（收紧）</span>
          </label>
          <div className="grid grid-cols-2 gap-2">
            <TrendNumberField
              label="逆势置信度下界"
              hint="0~1"
              value={confidenceRaw}
              disabled={disabled || value.counterGateEnabled === false}
              onChange={(raw) => onRawChange("confidence", raw)}
              testId="ai-trend-confidence"
            />
            <TrendNumberField
              label="逆势降档系数"
              hint="0~1"
              value={sizeMultRaw}
              disabled={disabled || value.counterGateEnabled === false}
              onChange={(raw) => onRawChange("sizeMult", raw)}
              testId="ai-trend-size-mult"
            />
          </div>
          <label className="flex items-center gap-2 text-[11px] text-[var(--text)]">
            <input
              type="checkbox"
              checked={value.consensusVoteEnabled !== false}
              disabled={disabled}
              onChange={(e) => onChange({ ...value, consensusVoteEnabled: e.target.checked })}
              aria-label="启用顺势追加一票"
            />
            <span>顺势且基调强时为共识门槛追加一票（放宽）</span>
          </label>
          <label className="flex items-center gap-2 text-[11px] text-[var(--text)]">
            <input
              type="checkbox"
              checked={value.contrarianAllowanceEnabled !== false}
              disabled={disabled}
              onChange={(e) =>
                onChange({ ...value, contrarianAllowanceEnabled: e.target.checked })
              }
              aria-label="启用顺势放宽反向票上限"
            />
            <span>顺势且基调强时把逆势通路的反向票上限抬高 1（放宽，不发票）</span>
          </label>
          <p className="text-[10px] leading-relaxed text-[var(--muted)]">
            两个数值留空即用服务端默认。三项开关的
            <strong className="text-[var(--text)]">极性不同</strong>
            ：第一项只会
            <strong className="text-[var(--text)]">减少</strong>
            成交，后两项只会
            <strong className="text-[var(--text)]">增加</strong>
            成交，可分别回退。
          </p>
        </>
      )}

      {error && (
        <p
          role="alert"
          className="text-[10px] leading-relaxed text-[var(--short-ink)]"
          data-testid="ai-trend-error"
        >
          {error}
        </p>
      )}
    </div>
  );
}

function TrendNumberField({
  label,
  hint,
  value,
  disabled,
  onChange,
  testId,
}: {
  label: string;
  hint: string;
  value: string;
  disabled: boolean;
  onChange: (raw: string) => void;
  testId: string;
}) {
  return (
    <label className="flex flex-col gap-0.5 text-[10px] text-[var(--muted)]">
      <span>
        {label}
        <span className="ml-1 opacity-70">（{hint}）</span>
      </span>
      <input
        type="text"
        inputMode="decimal"
        value={value}
        disabled={disabled}
        placeholder="服务端默认"
        onChange={(e) => onChange(e.target.value)}
        aria-label={label}
        data-testid={testId}
        className="w-full rounded border border-[var(--border)] bg-[var(--surface-2)] px-1.5 py-1 text-[11px] text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
      />
    </label>
  );
}

function NumberField({
  label,
  hint,
  value,
  disabled,
  onChange,
  testId,
}: {
  label: string;
  hint: string;
  value: string;
  disabled: boolean;
  onChange: (raw: string) => void;
  testId: string;
}) {
  return (
    <label className="flex flex-col gap-0.5 text-[10px] text-[var(--muted)]">
      <span>
        {label}
        <span className="ml-1 opacity-70">（{hint}）</span>
      </span>
      <input
        type="text"
        inputMode="decimal"
        value={value}
        disabled={disabled}
        placeholder="服务端默认"
        onChange={(e) => onChange(e.target.value)}
        aria-label={label}
        data-testid={testId}
        className="w-full rounded border border-[var(--border)] bg-[var(--surface-2)] px-1.5 py-1 text-[11px] text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
      />
    </label>
  );
}
