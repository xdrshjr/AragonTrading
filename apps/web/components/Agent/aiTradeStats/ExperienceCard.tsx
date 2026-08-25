"use client";

import { shortTime } from "@/lib/experience";
import type { AiTradeExperienceStatus } from "@/lib/experienceTypes";

const TIER_TEXT: Record<string, string> = {
  full: "完整（语境已变化）",
  brief: "精简（仅铁律）",
  repeat: "压缩为一行（语境未变）",
};

/**
 * 运行中会话的经验卡（§7.3）。
 *
 * 未装配 ⇒ **整卡不渲染**——绝不在一个没有数据的会话上挂一排「—」。
 *
 * 卡片承载两个**诚实性键**（I-8：后端算了却没有渲染落点的观测量等于死码）：
 * · `harvestGap` —— 「有 K 笔往返没采到」，而它们**恰好偏向亏损样本**，所以这张表偏乐观；
 * · `graphAsOfTs` —— 「该会话用的是哪一刻的经验库快照」，与设置中心预演页那条状态行同一口径。
 */
export default function ExperienceCard({
  experience,
}: {
  experience: AiTradeExperienceStatus | null | undefined;
}) {
  if (!experience || !experience.enabled) return null;
  const modeText = experience.write ? "读写" : "只读（不沉淀）";
  return (
    <div
      className="flex flex-col gap-1 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="ai-experience-card"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-[var(--muted)]">
          经验模式 · {modeText}
          {experience.scope === "global" ? " · 含跨标的" : ""}
        </span>
        <span className="tabular text-[10px] text-[var(--muted)]">
          经验库快照：{shortTime(experience.graphAsOfTs) || "本次启动时"}
        </span>
      </div>

      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        本轮喂入 {experience.l0 + experience.l1} 条（{experience.chars} 字
        {experience.tier ? ` · ${TIER_TEXT[experience.tier] ?? experience.tier}` : ""}）
        · 本次会话已沉淀 {experience.eventsHarvested} 条经验事件
        · 累计注入 {experience.injections} 轮
      </p>

      {experience.harvestGap > 0 && (
        <p
          className="text-[10px] leading-relaxed text-[var(--muted)]"
          data-testid="ai-experience-card-gap"
        >
          另有 {experience.harvestGap} 笔往返因续跑接管 / 暂停期离线平仓未能采集——
          这两条路径不经本会话的成交收口，故拿不到开仓那一刻的语境。它们偏向亏损样本，
          因此经验库对这段时间的判断偏乐观。
        </p>
      )}
    </div>
  );
}
