"use client";

import {
  blockedRows,
  formatConversion,
  funnelStages,
  hasFunnelSamples,
  isFunnelClosed,
} from "@/lib/aiTradeFunnel";
import type { AiTradeOpenFunnel } from "@/lib/aiTradeTypes";

/**
 * 开仓漏斗卡（ai-trade-tradability-and-open-funnel 轴 C）。
 *
 * 补上的是全仓此前**缺失的分母**：没有任何一处统计「模型这一轮到底想不想开仓」。后果是当 AI
 * 一单不开时，面板只能说「N 条纪律可能过严」，说不出**是哪一条在拦**、也说不出模型有没有提出过
 * 开仓——而这两件事对应完全相反的补救动作（放宽纪律 vs 检查信号板）。
 *
 * 三条呈现纪律：
 * - **零样本绝不造百分比**：`intents == 0` ⇒ 显式写「样本不足，不下结论」，绝不把 0/0 画成 0%；
 * - **口径词必须写出来**：本卡是**全程累计**，与归因账本的滚动窗口口径不同，两张卡并排时极易串；
 * - **「首拦截层」不是「该层的总拦截能力」**：按决策链顺序只归给第一个拦下它的闸。
 */
export default function OpenFunnelCard({
  funnel,
}: {
  funnel: AiTradeOpenFunnel | null | undefined;
}) {
  // 老会话 / 后端未上线该键 ⇒ 整卡不渲染（渲染门控：绝不在没有数据的会话上挂一排 0）。
  if (!funnel) return null;
  const enough = hasFunnelSamples(funnel);
  const stages = funnelStages(funnel);
  const rows = blockedRows(funnel);
  const conversion = formatConversion(funnel);

  return (
    <div
      className="flex flex-col gap-1.5 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="ai-open-funnel-card"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-[var(--muted)]">开仓漏斗</span>
        <span className="tabular text-[10px] text-[var(--muted)]">
          {funnel.scope || "本会话全程累计"}
          {conversion ? ` · 意愿→成交 ${conversion}` : ""}
        </span>
      </div>

      {!enough ? (
        <p
          className="text-[10px] leading-relaxed text-[var(--muted)]"
          data-testid="ai-open-funnel-empty"
        >
          样本不足，不下结论：模型
          <strong className="text-[var(--text)]">尚未提出过任何开仓意图</strong>
          {funnel.llmDegraded > 0 ? `（另有 ${funnel.llmDegraded} 轮因模型调用失败而降级）` : ""}
          。这时放宽纪律没有任何作用——一个从未被触发过的闸拦不了任何东西。
        </p>
      ) : (
        <>
          <div className="flex flex-col gap-0.5" data-testid="ai-open-funnel-stages">
            {stages.map((stage) => (
              <div key={stage.key} className="flex items-center gap-2 text-[11px]">
                <span className="w-[72px] shrink-0 truncate text-[var(--muted)]">{stage.label}</span>
                <span className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-[var(--surface-2)]">
                  {stage.ratio != null && (
                    <span
                      className="block h-full"
                      style={{
                        width: `${(stage.ratio * 100).toFixed(2)}%`,
                        backgroundColor: toneVar(stage.tone),
                      }}
                    />
                  )}
                </span>
                <span className="tabular w-[36px] shrink-0 text-right text-[var(--text)]">
                  {stage.count}
                </span>
              </div>
            ))}
          </div>

          {rows.length > 0 && (
            <div className="flex flex-col gap-0.5" data-testid="ai-open-funnel-blockers">
              {rows.map((row) => (
                <div
                  key={row.key}
                  className="flex items-center justify-between gap-2 text-[10px]"
                  data-testid={`ai-open-funnel-blocker-${row.key}`}
                >
                  <span className="truncate text-[var(--text)]">{row.label}</span>
                  <span className="tabular shrink-0 text-[var(--muted)]">
                    {row.count} 次{row.ratio != null ? ` · ${(row.ratio * 100).toFixed(0)}%` : ""}
                  </span>
                </div>
              ))}
            </div>
          )}

          {!isFunnelClosed(funnel) && (
            <p
              className="text-[10px] leading-relaxed text-[var(--short-ink)]"
              data-testid="ai-open-funnel-broken"
            >
              ⚠ 漏斗数据不闭合（各层拦截数之和超过了开仓意愿数），请把这条现象反馈给开发者
              ——这说明后端的归类口径出了问题，本卡上的比例不可信。
            </p>
          )}
        </>
      )}

      {(funnel.insufficientBalance ?? 0) > 0 && (
        // E-8（**纯观测**）：仓位求解结果超过可用保证金时今天以报错收场（实测 53 次），
        // 事后既不进任何计数、也无从回答「到底差多少」。本轮先让它可见；逐档降档挂在
        // `AI_TRADE_SIZING_MARGIN_PRECHECK`（默认关），下一轮才有数据回答「降一档够不够」。
        <p
          className="text-[10px] leading-relaxed text-[var(--short-ink)]"
          data-testid="ai-open-funnel-insufficient-balance"
        >
          另有 <strong>{funnel.insufficientBalance}</strong>{" "}
          次委托被交易所判「可用余额 / 保证金不足」而未成交。这不是任何一道纪律拦的——是解算出的
          张数超过了账户此刻的可用保证金。
        </p>
      )}

      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        「开仓意愿」= 模型第一阶段真的给出了开多 / 开空；各层拦截
        <strong className="text-[var(--text)]">按决策链顺序只归给第一个拦下它的闸</strong>
        （不是该层的总拦截能力）。「仓位解到 0」不是一道闸，是求解结果。
        <strong className="text-[var(--text)]">平仓侧</strong>
        被持仓周期 / 兜底拦下的轮次一律不进本图。
        {funnel.expired != null && (
          <>
            {" "}
            「挂单未成交即撤」与「真的成交」
            <strong className="text-[var(--text)]">互斥</strong>
            ：两者都发生在委托之后，前者是模型想开、闸也放行了，只是价格没到。
          </>
        )}
      </p>
    </div>
  );
}

function toneVar(tone: string): string {
  if (tone === "long") return "var(--long)";
  if (tone === "short") return "var(--short)";
  if (tone === "accent") return "var(--accent)";
  return "var(--muted)";
}
