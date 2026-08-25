"use client";

import { useCallback, useEffect, useState } from "react";
import { Button, Field, Select } from "@/components/ui";
import { AXIS_VALUES, axisLabel, budgetText, previewParityText, valueLabel } from "@/lib/experience";
import { experienceApi } from "@/lib/experienceApi";
import type { ExperiencePreview } from "@/lib/experienceTypes";

// 可手选的语境轴（`symbol` / `bar` 另有各自的下拉，故不在此列）。
// `exit` 必须在这里：`run()` 一直在发 `exit: axes.exit`，少了这一行那就是一个**永远为
// undefined 的入参** —— 后端接得住、界面上却没有任何地方能填，而退出来源恰恰是本特性最有
// 价值的一类经验（「由利润保护了结的 41 笔 +54bps」）。
const SEED_AXES = ["regime", "volatility", "event", "side", "wake", "exit", "horizon",
                   "confidence", "cost_regime"] as const;

/**
 * 检索预演 —— **本特性最有说服力的一屏**：把「Agent 到底记住了什么」从黑箱变成可核对的事实。
 *
 * 顶部那条状态行不是装饰。预演读的是**当前经验库**，运行中的会话用的是**启动那一刻**载入内存
 * 的快照（决策轮零 IO 的硬约束使然），两者会因后台重建与其它成员会话的写入而分叉。少了这句，
 * 这一屏在最需要它的时刻（会话正在跑）说的是假话——**一屏假话比没有这一屏更坏**。
 * `diverged` 由服务端判定，前端**不重算**：两处各判一次必然漂移。
 */
export default function ExperienceRetrievalPreview({
  symbols,
  bars,
}: {
  symbols: string[];
  bars: string[];
}) {
  const [instId, setInstId] = useState(symbols[0] ?? "");
  const [bar, setBar] = useState(bars[0] ?? "15m");
  const [axes, setAxes] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<ExperiencePreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!instId && symbols.length) setInstId(symbols[0]);
  }, [symbols, instId]);

  const run = useCallback(async () => {
    if (!instId || !bar) return;
    setLoading(true);
    setError(null);
    try {
      setPreview(await experienceApi.preview({
        instId, bar,
        regime: axes.regime, volatility: axes.volatility, event: axes.event,
        side: axes.side, wake: axes.wake, exit: axes.exit, horizon: axes.horizon,
        confidence: axes.confidence, costRegime: axes.cost_regime,
      }));
    } catch (err) {
      setError((err as Error).message || "预演失败");
    } finally {
      setLoading(false);
    }
  }, [instId, bar, axes]);

  const usedTotal = preview?.chars.total ?? 0;
  const budgetTotal = (preview?.chars.budget.l0 ?? 0) + (preview?.chars.budget.l1 ?? 0);

  return (
    <div className="flex flex-col gap-3" data-testid="experience-preview">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <div className="flex flex-col gap-2">
          <Field label="标的" htmlFor="prev-inst">
            <Select id="prev-inst" value={instId} onChange={(e) => setInstId(e.target.value)}>
              {symbols.length === 0 && <option value="">（暂无标的）</option>}
              {symbols.map((s) => <option key={s} value={s}>{s}</option>)}
            </Select>
          </Field>
          <Field label="周期" htmlFor="prev-bar">
            <Select id="prev-bar" value={bar} onChange={(e) => setBar(e.target.value)}>
              {bars.map((b) => <option key={b} value={b}>{b}</option>)}
            </Select>
          </Field>
          {SEED_AXES.map((axis) => (
            <Field key={axis} label={axisLabel(axis)} htmlFor={`prev-${axis}`}>
              <Select
                id={`prev-${axis}`}
                value={axes[axis] ?? ""}
                onChange={(e) => setAxes((prev) => ({ ...prev, [axis]: e.target.value }))}
              >
                <option value="">不限</option>
                {(AXIS_VALUES[axis] ?? []).map((value) => (
                  <option key={value} value={value}>{valueLabel(value)}</option>
                ))}
              </Select>
            </Field>
          ))}
          <Button variant="primary" size="sm" onClick={run} disabled={!instId || loading}>
            {loading ? "预演中…" : "预演 Agent 下一轮会看到什么"}
          </Button>
        </div>

        <div className="flex min-w-0 flex-col gap-2">
          {error && (
            <p className="rounded-md border border-[var(--short)] px-3 py-2 text-[11px] text-[var(--short-ink)]">
              {error}
            </p>
          )}
          {!preview && !error && (
            <p className="rounded-md border border-dashed border-[var(--border)] px-4 py-10 text-center text-xs text-[var(--muted)]">
              选一组语境，然后点「预演」——右侧会<strong className="text-[var(--text)]">逐字</strong>显示下一轮喂给 Agent 的文本。
            </p>
          )}
          {preview && (
            <>
              <p
                className="rounded-md border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-[11px] leading-relaxed text-[var(--muted)]"
                data-testid="experience-preview-parity"
              >
                {previewParityText(preview.liveSessions)}
              </p>

              <PreviewBlock title={`常驻纲要 L0（${preview.l0.length} 条）`} text={preview.l0Text} />
              <PreviewBlock
                title={`语境展开 L1（${preview.l1.length} 条${preview.tier === "full" ? "" : "，本轮档位下不发送"}）`}
                text={preview.l1Text}
              />

              <div className="flex flex-col gap-1">
                <div className="flex items-center justify-between text-[11px] text-[var(--muted)]">
                  <span>字符预算</span>
                  <span className="tabular">{budgetText(usedTotal, budgetTotal)}</span>
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--surface-2)]">
                  <div
                    className="h-full rounded-full bg-[var(--accent)]"
                    style={{ width: `${budgetTotal ? Math.min(100, (usedTotal / budgetTotal) * 100) : 0}%` }}
                  />
                </div>
              </div>

              {preview.disputed.length > 0 && (
                <p className="text-[11px] leading-relaxed text-[var(--warn)]">
                  有 {preview.disputed.length} 组互相矛盾且势均力敌的结论被挡在纲要之外——
                  它们只会作为「争议」出现在下单前的反思环节。
                </p>
              )}
              {preview.suppressed.length > 0 && (
                <p className="text-[11px] leading-relaxed text-[var(--muted)]">
                  另有 {preview.suppressed.length} 条被更强的反例压制（在这里可见，但不会进 prompt）。
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function PreviewBlock({ title, text }: { title: string; text: string }) {
  return (
    <section className="flex flex-col gap-1">
      <h4 className="text-[11px] font-medium text-[var(--muted)]">{title}</h4>
      {text ? (
        <pre className="max-h-[220px] overflow-auto whitespace-pre-wrap rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-[11px] leading-relaxed text-[var(--text)]">
          {text}
        </pre>
      ) : (
        <p className="rounded-md border border-dashed border-[var(--border)] px-3 py-2 text-[11px] text-[var(--muted)]">
          这一层在该语境下<strong className="text-[var(--text)]">一个字都不会发送</strong>（没有过门槛的经验，或字符预算装不下第一条）。
        </p>
      )}
    </section>
  );
}
