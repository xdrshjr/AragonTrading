"use client";

import type { AiTradeDirectionRule } from "@/lib/aiTradeTypes";

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
    </section>
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
