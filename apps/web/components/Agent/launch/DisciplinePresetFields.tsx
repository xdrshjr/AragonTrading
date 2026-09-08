"use client";

import { useState } from "react";
import {
  DEFAULT_DISCIPLINE_PRESET,
  DISCIPLINE_COPY,
  DISCIPLINE_PRESETS,
} from "@/lib/aiTradeDiscipline";
import type { AiTradeDisciplinePresetsResponse } from "@/lib/aiTradeTypes";

/**
 * 交易纪律预设分区：4 选 1 卡片 + 每张卡的**一句话后果说明**。
 *
 * 为什么必须明示：本轮之前，从这个弹窗启动的会话拿到的是「持有全额预算、没有波动率定标、没有回撤
 * 刹车、且明知手续费吃得掉预期收益也照开」的 LLM；本轮起默认套上「均衡」预设。这是唯一一处行为
 * 默认值的改变，静默生效会让用户在下一次「AI 怎么开单变少了」时无从对账。
 *
 * 刻意**不在这里显示任何阈值数字**：阈值的唯一真源是后端那张冻结表，前端抄一份必漂移；用户要核对
 * 具体数值时，启动回执的 `discipline.thresholds` 与统计页的纪律卡都会如实给出**生效值**。
 */
export default function DisciplinePresetFields({
  value,
  onChange,
  disabled,
  table,
  inertKeys = [],
}: {
  value: (typeof DISCIPLINE_PRESETS)[number];
  onChange: (next: (typeof DISCIPLINE_PRESETS)[number]) => void;
  disabled: boolean;
  table: AiTradeDisciplinePresetsResponse | null;
  /**
   * ★ 轴 A-4（loss-and-liveness-hardening）：在本次启动中「其消费层未装配因而**不作数**」的
   * 阈值键。判据的**权威真源是后端** `discipline.inert_thresholds()`（其产物随 `config_json`
   * 与 `group_status().discipline.inert` 下发）；启动**之前**服务端还没被问过，故这里按同一条
   * 判据在本地现算一次，`G-A6` 用例钉死两侧同值。
   */
  inertKeys?: string[];
}) {
  const [open, setOpen] = useState(false);
  const rows = table?.presets.find((p) => p.id === value)?.rows ?? [];
  return (
    <div className="flex flex-col gap-1.5" data-testid="ai-discipline-preset">
      <span className="text-[11px] text-[var(--muted)]">交易纪律</span>
      <div className="grid grid-cols-4 gap-1.5">
        {DISCIPLINE_PRESETS.map((preset) => (
          <button
            key={preset}
            type="button"
            disabled={disabled}
            aria-pressed={value === preset}
            onClick={() => onChange(preset)}
            className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1 text-[11px] text-[var(--text)] transition-colors hover:bg-[var(--surface-2)] aria-[pressed=true]:border-[var(--accent)] aria-[pressed=true]:text-[var(--accent-ink)] disabled:opacity-50"
          >
            {DISCIPLINE_COPY[preset].label}
            {preset === DEFAULT_DISCIPLINE_PRESET && (
              <span className="block text-[9px] text-[var(--muted)]">服务端默认</span>
            )}
          </button>
        ))}
      </div>
      <span className="text-[10px] leading-relaxed text-[var(--muted)]">
        {DISCIPLINE_COPY[value].detail}
      </span>
      {table && !table.enabled && (
        <span className="text-[10px] leading-relaxed text-[var(--short-ink)]">
          本机已由运维总闸整轴关闭（AI_TRADE_DISCIPLINE_ENABLED=false）：无论这里选哪一档，本次启动
          都不会装配纪律层。
        </span>
      )}
      {rows.length > 0 && (
        <>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="self-start text-[10px] text-[var(--accent-ink)] underline-offset-2 hover:underline"
          >
            {open ? "收起阈值" : "查看这一档的阈值"}
          </button>
          {open && (
            <div
              className="grid grid-cols-2 gap-x-3 gap-y-0.5 rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5"
              data-testid="ai-discipline-threshold-table"
            >
              {rows.map((r) => {
                // ★ A-4：失效的那一行加删除线 + 尾注。**「显示了但不作数」是本轮要消灭的形态**
                // ——用户选了承诺硬止盈的预设，却因为另一个复选框而拿到一个没有执行者的承诺。
                const inert = inertKeys.includes(r.key);
                return (
                  <span
                    key={r.key}
                    className="flex items-center justify-between gap-2 text-[10px]"
                    data-testid={inert ? `ai-discipline-threshold-inert-${r.key}` : undefined}
                  >
                    <span className="truncate text-[var(--muted)]">
                      {r.label}
                      {inert && <span className="ml-1 text-[9px]">（未装配，不生效）</span>}
                    </span>
                    <span
                      className="tabular shrink-0"
                      style={{
                        color: r.off || inert ? "var(--muted)" : "var(--text)",
                        textDecoration: inert ? "line-through" : undefined,
                      }}
                    >
                      {r.off ? "关闭" : r.value}
                    </span>
                  </span>
                );
              })}
            </div>
          )}
        </>
      )}
      <span className="text-[10px] leading-relaxed text-[var(--muted)]">
        这组阈值控制仓位大小、开仓门槛、回撤刹车与连亏熔断，
        <strong className="text-[var(--text)]">尚未经回测校准</strong>
        ；启动后可在「统计 → 当前」的纪律卡里逐条核对生效值与本会话的拦截次数。
      </span>
    </div>
  );
}
