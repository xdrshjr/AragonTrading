"use client";

import { useId, useState } from "react";
import { Button, Field, Input, Modal, Select } from "@/components/ui";
import { AXIS_VALUES, axisLabel, valueLabel } from "@/lib/experience";
import type { ExperienceNode } from "@/lib/experienceTypes";

/** 语境选择器覆盖的轴。`symbol` / `bar` 由调用方按库存下拉给出，其余走闭合词表。 */
const CLOSED_AXES = ["regime", "volatility", "event", "side", "wake", "exit", "horizon",
                     "cost_regime", "confidence"] as const;

/**
 * 手工新增 / 编辑一条经验。
 *
 * **语境选择器受闭合词表约束**（不变式 I-5）：一个用户编出来的 `axis="心情"` 在图上永远匹配不到
 * 任何东西，却会静默占掉一条经验的全部权重。所以这里是下拉而不是自由文本——把不可能成立的
 * 输入在界面上就消除掉，比在后端拒绝再报错更好。
 *
 * 编辑模式下**语境不可改**：语境是 `key` 哈希的输入之一，改了就是另一条经验；允许改会让同一条
 * 经验的历史证据链凭空断掉。要换语境请新建一条，把旧的静音。
 */
export default function ExperienceEditor({
  open,
  editing,
  symbols,
  bars,
  submitting,
  error,
  onSubmit,
  onClose,
}: {
  open: boolean;
  /** 非空 = 编辑既有节点；空 = 新增。 */
  editing: ExperienceNode | null;
  symbols: string[];
  bars: string[];
  submitting: boolean;
  error: string | null;
  onSubmit: (payload: {
    statement: string;
    detail: string;
    context: Record<string, string>;
    polarity: "positive" | "negative";
  }) => void;
  onClose: () => void;
}) {
  const titleId = useId();
  const [statement, setStatement] = useState(editing?.label ?? "");
  const [detail, setDetail] = useState(editing?.detail ?? "");
  const [polarity, setPolarity] = useState<"positive" | "negative">(
    ((editing?.stats as Record<string, unknown> | undefined)?.polarity as
      "positive" | "negative" | undefined) ?? "negative",
  );
  const [context, setContext] = useState<Record<string, string>>(
    (editing?.signature as Record<string, string> | undefined) ?? {},
  );

  const isEdit = editing !== null;
  const hasContext = Object.values(context).some((value) => value);
  const canSubmit = statement.trim().length > 0 && (isEdit || hasContext) && !submitting;

  const setAxis = (axis: string, value: string) =>
    setContext((prev) => {
      const next = { ...prev };
      if (value) next[axis] = value;
      else delete next[axis];
      return next;
    });

  return (
    <Modal open={open} onClose={onClose} labelledBy={titleId} className="max-w-[520px]">
      <div className="flex max-h-[82vh] flex-col gap-3 overflow-y-auto p-5">
        <h2 id={titleId} className="text-sm font-semibold text-[var(--text)]">
          {isEdit ? "编辑经验" : "手工新增一条经验"}
        </h2>
        <p className="text-[11px] leading-relaxed text-[var(--muted)]">
          手工经验的起始档位恒为「候选」——它
          <strong className="text-[var(--text)]">不会</strong>自动进入 Agent 的常驻纲要。
          要让它生效，请在保存后<strong className="text-[var(--text)]">置顶</strong>它
          （那是一次明确的人类背书），或等待它被足够多的真实往返佐证。
        </p>

        <Field label="一句话陈述（≤120 字）" htmlFor="exp-statement">
          <Input
            id="exp-statement"
            value={statement}
            maxLength={120}
            placeholder="例：BTC-USDT-SWAP 15m 高波动+突破做多，净额均值为负，成本吃掉大半毛额"
            onChange={(e) => setStatement(e.target.value)}
          />
        </Field>

        <Field
          label="展开说明（可选，≤600 字）"
          htmlFor="exp-detail"
          hint="写清证据、适用边界与失效条件——这段只在设置中心显示，不进 Agent 的常驻纲要。"
        >
          <textarea
            id="exp-detail"
            value={detail}
            maxLength={600}
            rows={3}
            onChange={(e) => setDetail(e.target.value)}
            className="w-full rounded-[10px] border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--accent)]"
          />
        </Field>

        <fieldset disabled={isEdit} className="flex flex-col gap-2">
          <legend className="text-xs font-medium text-[var(--muted)]">
            适用语境{isEdit ? "（编辑模式下不可修改——改语境等于另一条经验）" : "（至少选一项）"}
          </legend>
          <div className="grid grid-cols-2 gap-2">
            <Field label="标的" htmlFor="exp-ctx-symbol">
              <Select
                id="exp-ctx-symbol"
                value={context.symbol ?? ""}
                onChange={(e) => setAxis("symbol", e.target.value)}
              >
                <option value="">不限</option>
                {symbols.map((s) => <option key={s} value={s}>{s}</option>)}
              </Select>
            </Field>
            <Field label="周期" htmlFor="exp-ctx-bar">
              <Select
                id="exp-ctx-bar"
                value={context.bar ?? ""}
                onChange={(e) => setAxis("bar", e.target.value)}
              >
                <option value="">不限</option>
                {bars.map((b) => <option key={b} value={b}>{b}</option>)}
              </Select>
            </Field>
            {CLOSED_AXES.map((axis) => (
              <Field key={axis} label={axisLabel(axis)} htmlFor={`exp-ctx-${axis}`}>
                <Select
                  id={`exp-ctx-${axis}`}
                  value={context[axis] ?? ""}
                  onChange={(e) => setAxis(axis, e.target.value)}
                >
                  <option value="">不限</option>
                  {(AXIS_VALUES[axis] ?? []).map((value) => (
                    <option key={value} value={value}>{valueLabel(value)}</option>
                  ))}
                </Select>
              </Field>
            ))}
          </div>
        </fieldset>

        <Field label="极性" htmlFor="exp-polarity" className="w-[160px]">
          <Select
            id="exp-polarity"
            value={polarity}
            onChange={(e) => setPolarity(e.target.value as "positive" | "negative")}
          >
            <option value="negative">教训（该避免）</option>
            <option value="positive">经验（该重复）</option>
          </Select>
        </Field>

        {error && (
          <p className="rounded-md border border-[var(--short)] px-3 py-2 text-[11px] text-[var(--short-ink)]">
            {error}
          </p>
        )}

        <div className="mt-1 flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>取消</Button>
          <Button
            variant="primary"
            size="sm"
            disabled={!canSubmit}
            onClick={() => onSubmit({
              statement: statement.trim(), detail: detail.trim(), context, polarity,
            })}
          >
            {submitting ? "保存中…" : "保存"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
