"use client";

import {
  TREND_BUCKET_LABEL,
  TREND_DIRECTION_LABEL,
  trendSampleText,
} from "@/lib/aiTradeTrend";
import type { AiTradeTrendStats } from "@/lib/aiTradeTypes";

/**
 * 顺势 / 逆势卡（ai-trade-execution-trend-verdict §3.7.5）。
 *
 * 紧跟在方向分布卡下方，因为两张卡是同一问题的两个切面：一张答「多空意愿比」，一张答
 * 「顺势逆势比与各自的成绩」。合入前后者在产品里完全不可观测——「基调到底判过多少轮偏空」
 * 与「逆势闸拦了多少次」这两件事既不可见、也不可证伪。
 *
 * 四条呈现纪律：
 * - **`unknown` 单列一格**，绝不并进任何一侧（归属不明的成交来自接管仓位 / 离线平仓 /
 *   重启后续跑——把它摊进两侧就是编数据）。
 * - **样本不足不下结论**：低于后端下发的 `minSamples` 时写「样本 a/b，尚不足以下结论」，
 *   **不画趋势、不写百分比**（沿用信源先验那轮的成文口径）。
 * - **放行不是拦截**：逆势放行的次数与被拦次数分开写——把放行塞进拦截计数，会让闸计数把一次
 *   成功开仓记成一次被拦。
 * - **两条放宽通路各自可见**：若长期只有一侧被点亮，用户在同一张卡上就能看出来并关掉对应
 *   总闸（这是 RK-1 缓解④ 的读点）。
 */
export default function TrendAlignmentCard({
  stats,
}: {
  stats: AiTradeTrendStats | null | undefined;
}) {
  // 未装配 / 零样本 / 老记录 ⇒ 整卡不渲染（绝不在没有数据的会话上挂一排 0）。
  if (!stats || !stats.armed) return null;
  const rounds = stats.verdictRounds ?? {};
  const totalRounds = Object.values(rounds).reduce((sum, n) => sum + (n || 0), 0);
  if (totalRounds <= 0) return null;
  const intents = stats.intents ?? { aligned: 0, counter: 0, unknown: 0 };
  const blocked = stats.counterBlocked ?? { confidence: 0, size: 0 };
  const lanes = stats.bonusLanes ?? {};
  const sample = trendSampleText(stats);

  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="ai-trend-alignment-card"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-[var(--muted)]">趋势基调 · 顺势 / 逆势</span>
        <span className="tabular text-[10px] text-[var(--muted)]">
          判定级别 {stats.bar ?? "—"} · 共 {totalRounds} 轮
        </span>
      </div>

      {/* 第 1 行：本会话基调分布 */}
      <Row
        label="本会话基调分布"
        testId="ai-trend-rounds"
        text={(["up", "down", "range", "unknown"] as const)
          .map((key) => `${TREND_DIRECTION_LABEL[key]} ${rounds[key] ?? 0} 轮`)
          .join(" · ")}
      />

      {/* 第 2 行：开仓意愿（`unknown` 单列，绝不猜方向） */}
      <Row
        label="开仓意愿"
        testId="ai-trend-intents"
        text={(["aligned", "counter", "unknown"] as const)
          .map((key) => `${TREND_BUCKET_LABEL[key]} ${intents[key] ?? 0}`)
          .join(" · ")}
      />

      {/* 第 3 行：逆势门槛（拦 vs 放行）+ 两条顺势放宽通路各自的点亮次数 */}
      <Row
        label="逆势门槛 / 顺势放宽"
        testId="ai-trend-gate"
        text={
          `拦下 ${(blocked.confidence ?? 0) + (blocked.size ?? 0)} 次` +
          `（置信度 ${blocked.confidence ?? 0} / 降档 ${blocked.size ?? 0}）` +
          `，放行 ${stats.counterPassed ?? 0} 次` +
          `；追加票 ${lanes.voteApplied ?? 0} 次 · 放宽反向票上限 ${lanes.allowanceApplied ?? 0} 次`
        }
      />

      {/* 第 4 行：成绩（alignment 取自**开仓委托发出时定格**的认领表，不是当轮基调） */}
      {sample ? (
        <p className="text-[10px] leading-relaxed text-[var(--muted)]" data-testid="ai-trend-sample">
          成绩：{sample}（顺势 {stats.filled?.aligned ?? 0} 笔 / 逆势{" "}
          {stats.filled?.counter ?? 0} 笔 / 归属不明 {stats.filled?.unknown ?? 0} 笔）。
        </p>
      ) : (
        <Row
          label="成绩（已实现净额）"
          testId="ai-trend-pnl"
          text={(["aligned", "counter", "unknown"] as const)
            .map(
              (key) =>
                `${TREND_BUCKET_LABEL[key]} ${stats.filled?.[key] ?? 0} 笔 ` +
                `${fmtUsd(stats.netPnlUsd?.[key])}`,
            )
            .join(" · ")}
        />
      )}

      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        基调判定在
        <strong className="text-[var(--text)]">你所选的执行周期</strong>
        上，高周期只作权重受限的调制项——它可以削弱基调的强度，不能替你决定这个级别的方向。
        「归属不明」是接管仓位 / 离线平仓 / 重启后续跑的那些成交，它
        <strong className="text-[var(--text)]">不并进任何一侧</strong>。
      </p>
    </div>
  );
}

function Row({ label, text, testId }: { label: string; text: string; testId: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2 text-[10px]" data-testid={testId}>
      <span className="shrink-0 text-[var(--text)]">{label}</span>
      <span className="tabular text-right text-[var(--muted)]">{text}</span>
    </div>
  );
}

/** 净额盈亏；`0` 与「没有这个数」在这里是同一个显示，因为后端恒下发三格实数（缺席即 0 笔）。 */
function fmtUsd(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)} USDT`;
}
