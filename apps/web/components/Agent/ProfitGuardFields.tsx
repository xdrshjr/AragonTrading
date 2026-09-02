"use client";

import { Select } from "@/components/ui";
import { AI_TRADE_GIVEBACK_TIER_PRESETS } from "@/lib/aiTrade";

/**
 * 利润保护整段的**受控**子组件（ai-trade-profit-protection-adaptive §3.3 / P1-8）。
 *
 * 抽出的唯一理由是 CLAUDE.md 的 800 行红线：`AiTradeLaunchModal.tsx` 开工前已 1284 行，本轮再加四行
 * 输入与回测段渲染会推到 ~1400。F0 在后端抽 `ProfitGuardRuntime` 的正当性正是这条红线，同一条红线
 * 不能在前端被无声突破。
 *
 * **值与 setter 全部由父组件传入，本组件内不得写任何 effect**：弹窗的重置逻辑必须留在父组件那个
 * 依赖收敛为 `[open]` 的 effect 里。在子组件里另写一个 reset effect，正是
 * `ai-trade-modal-revert-recurrence` 这条复发性缺陷的根因（非稳定依赖 → 输入约 1 秒后回弹）。
 */
export interface ProfitGuardFieldsProps {
  mode: "live" | "backtest";
  enabled: boolean;
  onEnabledChange: (value: boolean) => void;
  armReturnRaw: string;
  onArmReturnChange: (value: string) => void;
  givebackRaw: string;
  onGivebackChange: (value: string) => void;
  breakevenArmRaw: string;
  onBreakevenArmChange: (value: string) => void;
  minHoldRaw: string;
  onMinHoldChange: (value: string) => void;
  tierPreset: string;
  onTierPresetChange: (value: string) => void;
  atrFloorRaw: string;
  onAtrFloorChange: (value: string) => void;
  atrCapRaw: string;
  onAtrCapChange: (value: string) => void;
  cooldownRaw: string;
  onCooldownChange: (value: string) => void;
  disabled: boolean;
  preview: string;
  /**
   * ai-trade-loss-and-liveness-hardening A-3：关闭时要逐条列出的**后果清单**。
   *
   * 全部取自**已经在本组件上的受控值**（用户此刻填的那几个数）与一条**后端下发**的硬止盈行
   * ——本文件**一个阈值字面量都不许出现**（`G-A5` 源码扫描门禁）。前端复制一份后端阈值，
   * 下次调参时用户看到的就与真正生效的那组数分叉，比不给更坏。
   */
  hardTakeProfitText?: string | null;
}

export default function ProfitGuardFields({
  mode,
  enabled,
  onEnabledChange,
  armReturnRaw,
  onArmReturnChange,
  givebackRaw,
  onGivebackChange,
  breakevenArmRaw,
  onBreakevenArmChange,
  minHoldRaw,
  onMinHoldChange,
  tierPreset,
  onTierPresetChange,
  atrFloorRaw,
  onAtrFloorChange,
  atrCapRaw,
  onAtrCapChange,
  cooldownRaw,
  onCooldownChange,
  disabled,
  preview,
  hardTakeProfitText,
}: ProfitGuardFieldsProps) {
  return (
    <div className="flex flex-col gap-1.5 rounded-md border border-[var(--accent-soft)] bg-[color-mix(in_srgb,var(--accent)_5%,transparent)] px-3 py-2">
      <label className="flex cursor-pointer items-start gap-2 text-xs">
        <input
          type="checkbox"
          checked={enabled}
          disabled={disabled}
          onChange={(e) => onEnabledChange(e.target.checked)}
          className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
          aria-label="利润保护（推荐开启）"
        />
        <span className="flex flex-col gap-0.5">
          <span className="font-medium text-[var(--text)]">利润保护（推荐开启）</span>
          <span className="text-[11px] leading-relaxed text-[var(--muted)]">
            <strong className="text-[var(--text)]">移动止盈 + 保本</strong>：持仓浮盈达阈值后系统记录峰值，
            利润自峰值<strong className="text-[var(--text)]">回吐超比例</strong>或价格
            <strong className="text-[var(--text)]">跌回保本线</strong>时由系统直接市价平仓。
            回吐阈随利润<strong className="text-[var(--text)]">阶梯收紧</strong>，回吐距离再按
            <strong className="text-[var(--text)]">波动率（ATR）</strong>归一。
            {mode === "live" ? (
              <>
                判定只用<strong className="text-[var(--text)]">实时价</strong>，不等 K 线收盘；
                行情异常时保护层会主动不动作（届时仅交易所侧止盈止损生效）。
              </>
            ) : (
              <>
                回测中为 <strong className="text-[var(--text)]">bar 内极值保守近似</strong>
                （同根新高不立即抬线、跳空按开盘价成交、按根推进而非 5 秒盯市），
                触发次数与落袋结果<strong className="text-[var(--text)]">相对实盘偏保守</strong>
                ——不得据此推断实盘一定更好或更差。
              </>
            )}
          </span>
        </span>
      </label>
      {!enabled && (
        // ★ A-3：关闭是用户的权利，但**后果必须被说出来**。
        // 生产实证：193 / 211 条 run 的利润保护层未装配，其中 45 条同时选了承诺「硬止盈」的纪律
        // 预设——用户看得见那个数，它却逐字节不作数（`hard_tp_return_pct` 的唯一消费点在保护层里）。
        // 更要命的是持仓周期层只有**亏损侧**豁免：浮亏到半个止损距离 ⇒ 三闸全开、可以随时割肉；
        // 浮盈任意幅度 ⇒ 三闸全关、在最短持仓之内**没有任何通道**兑现利润。
        <div
          role="alert"
          className="mt-1 flex flex-col gap-1 rounded border border-[var(--short)] bg-[var(--short-soft)] px-2 py-1.5 text-[11px] leading-relaxed text-[var(--text)]"
          data-testid="ai-profit-guard-unguarded-notice"
        >
          <span className="font-medium">关闭后，以下机制在本次会话中全部不生效：</span>
          <ul className="flex list-disc flex-col gap-0.5 pl-4">
            <li>
              回吐棘轮：浮盈达 <strong>{armReturnRaw || "—"}%</strong>{" "}
              后记录峰值，自峰值回吐超{" "}
              <strong>{givebackRaw || "—"}%</strong>（按所选阶梯逐级收紧）即由系统平仓。
            </li>
            <li>
              保本线：浮盈达 <strong>{breakevenArmRaw || "—"}%</strong>{" "}
              后把退出线上移到入场价附近，跌回即离场。
            </li>
            {hardTakeProfitText && (
              <li data-testid="ai-profit-guard-unguarded-hardtp">
                {hardTakeProfitText}
                ——<strong>该项由利润保护层执行，关闭后不生效</strong>。
              </li>
            )}
            <li>峰值浮盈与利润捕获率读数（关掉它，你就没有了「本该拦住多少钱」这个证据）。</li>
            <li>
              保护性平仓后的同向冷却 <strong>{cooldownRaw || "—"}</strong> 秒。
            </li>
          </ul>
          <span className="text-[var(--muted)]">
            若同时开着「持仓周期」模式，服务端会拒绝启动：那种组合下浮盈回吐没有任何自动兑现通道。
          </span>
        </div>
      )}
      {enabled && (
        <div className="mt-1 flex flex-col gap-1.5 text-[11px] text-[var(--muted)]">
          <div className="grid grid-cols-2 gap-2">
            <GuardField
              label="武装阈值（保证金收益 %）"
              value={armReturnRaw}
              onChange={onArmReturnChange}
              disabled={disabled}
            />
            <GuardField
              label="允许回吐（%）"
              value={givebackRaw}
              onChange={onGivebackChange}
              disabled={disabled}
            />
            <GuardField
              label="保本阈值（保证金收益 %，0=关）"
              value={breakevenArmRaw}
              onChange={onBreakevenArmChange}
              disabled={disabled}
            />
            <GuardField
              label="最短持有（秒）"
              value={minHoldRaw}
              onChange={onMinHoldChange}
              disabled={disabled}
            />
            <GuardField
              label="ATR 下界倍数（0=关）"
              value={atrFloorRaw}
              onChange={onAtrFloorChange}
              disabled={disabled}
            />
            <GuardField
              label="ATR 上界倍数（0=关）"
              value={atrCapRaw}
              onChange={onAtrCapChange}
              disabled={disabled}
            />
            <GuardField
              label="平仓后冷却（秒，0=关）"
              value={cooldownRaw}
              onChange={onCooldownChange}
              disabled={disabled}
            />
            <label className="flex flex-col gap-1">
              <span>回吐阶梯</span>
              <Select
                value={tierPreset}
                disabled={disabled}
                onChange={(e) => onTierPresetChange(e.target.value)}
                className="text-xs"
                aria-label="回吐阶梯"
              >
                {AI_TRADE_GIVEBACK_TIER_PRESETS.map((preset) => (
                  <option key={preset.value} value={preset.value}>
                    {preset.label}
                  </option>
                ))}
              </Select>
            </label>
          </div>
          <span className="leading-relaxed">{preview}</span>
          <span className="leading-relaxed">
            阶梯让「赚得越多、允许回吐越少」；ATR 下界避免在低波动标的上被分钟级噪音扫掉，上界避免在
            高波动标的上放任过度回吐。保本阈值填 0 会在高波动下让移动止盈线失去兜底
            （届时该线会如实作废，本轮不平仓）。
          </span>
        </div>
      )}
    </div>
  );
}

/** 利润保护的数值输入框（与「高级参数」同风格的紧凑右对齐输入）。 */
function GuardField({
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
