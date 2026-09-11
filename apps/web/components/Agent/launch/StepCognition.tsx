"use client";

import { Select } from "@/components/ui";
import { REASONING_DEPTH_HINTS, REASONING_DEPTH_LABELS } from "@/lib/aiTrade";
import { risksForStep } from "@/lib/aiTradeLaunchRisk";
import type { AiTradeReasoningDepth } from "@/lib/aiTradeTypes";
import AiTradeExperienceFields from "../AiTradeExperienceFields";
import AiTradeIntelFields from "../AiTradeIntelFields";
import AiTradeTrajectoryFields from "../AiTradeTrajectoryFields";
import { InlineRiskNote } from "./LaunchPrimitives";
import type { UseAiTradeLaunchForm } from "./useAiTradeLaunchForm";

/**
 * 第 4 步：**让 Agent 想多深、拿什么当证据**（ai-trade-guided-launch §4.2.1）。
 *
 * 承载今天弹窗里的：研判深度（实盘；回测为只读说明）、经验模式卡、市场情报卡（回测整卡不渲染）、
 * 后训练数据采集卡（回测整卡替换为一行说明，ai-trade-trajectory-capture §2.13）。
 * 本步**只产生一种阻断错误**：用户勾选了数据采集、而目录被服务端判定不可用
 * （`trajectoryDirError`，原因就地渲染在采集卡里）。研判深度 / 经验 / 情报三张卡仍只有档位
 * 选择，没有可填错的数值。
 */
/** 经验整关那一条由经验卡自己的 `ai-trade-experience-off-note` 就地说出口。 */
const ALREADY_INLINE = new Set(["experienceOff"]);

export default function StepCognition({
  form,
  submitting,
}: {
  form: UseAiTradeLaunchForm;
  submitting: boolean;
}) {
  const {
    mode, reasoningDepth, setReasoningDepth, experience, setExperience, intel, setIntel,
    members, instId, risks, trajectory, setTrajectory, setTrajectorySkipOnce,
    trajectoryCapture,
  } = form;
  const stepRisks = risksForStep(risks, "cognition");

  return (
    <div className="flex flex-col gap-3">
      {/* 研判深度：**仅实盘段露出**。回测档由服务端独立取 `AI_TRADE_REASONING_DEPTH_BACKTEST`。 */}
      {mode === "live" ? (
        <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          <span>研判深度（思维链）</span>
          <Select
            value={reasoningDepth}
            disabled={submitting}
            onChange={(e) => setReasoningDepth(e.target.value as AiTradeReasoningDepth)}
            className="text-xs"
            aria-label="研判深度"
          >
            {(["off", "standard", "deep", "exhaustive"] as const).map((d) => (
              <option key={d} value={d}>
                {REASONING_DEPTH_LABELS[d]}
              </option>
            ))}
          </Select>
          <span className="leading-relaxed text-[var(--muted)]">
            {REASONING_DEPTH_HINTS[reasoningDepth]}
          </span>
        </label>
      ) : (
        <p className="rounded-md border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1.5 text-[11px] leading-relaxed text-[var(--muted)]">
          回测使用<strong className="text-[var(--text)]">独立</strong>的研判深度档（默认「标准」），
          以免一次 500 轮回测变成一笔意外的高额 token 账单；如需调整请到设置中心改{" "}
          <code>AI_TRADE_REASONING_DEPTH_BACKTEST</code>。
        </p>
      )}

      {/* 经验模式（experience-context-graph §7.2）。 */}
      <AiTradeExperienceFields
        value={experience}
        onChange={setExperience}
        disabled={submitting}
        backtest={mode === "backtest"}
      />

      {/* 市场情报研判（intel-session-binding 轴 A）：与经验模式同为「给 Agent 的额外证据」，
          一内生一外生，放在一起用户才不必跨段对照。回测 tab 由组件自身整卡不渲染。 */}
      <AiTradeIntelFields
        value={intel}
        onChange={setIntel}
        instIds={mode === "backtest" ? [instId] : members.map((m) => m.instId)}
        disabled={submitting}
        backtest={mode === "backtest"}
      />

      {/* 后训练数据采集（trajectory-capture §2.13）：默认不勾；目录不可用是本步唯一的阻断错误，
          原因就地渲染在卡片里，第 6 步的兜底行指回这里。 */}
      <AiTradeTrajectoryFields
        value={trajectory}
        onChange={(next) => {
          // 在卡片上重新表态 ⇒ 撤销第 6 步那次一次性的「本次不采集数据」。
          setTrajectorySkipOnce(false);
          setTrajectory(next);
        }}
        capture={trajectoryCapture}
        disabled={submitting}
        backtest={mode === "backtest"}
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
