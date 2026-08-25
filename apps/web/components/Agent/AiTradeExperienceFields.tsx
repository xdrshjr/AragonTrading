"use client";

import { useEffect, useState } from "react";
import { experienceApi } from "@/lib/experienceApi";
import type { AiTradeExperienceRule, ExperienceOverview } from "@/lib/experienceTypes";
import { useAppStore } from "@/lib/store";

/**
 * 启动弹窗的「经验模式」卡片（**受控组件**，镜像 `ProfitGuardFields` / `AiTradeHorizonFields`）。
 *
 * 三条必须写在界面上的事实：
 *
 * 1. **取消勾选 = 既不读也不写**。`enabled:false` 时后端整层不装配，而装配是采集的前提——
 *    关掉读的同时也关掉了收据。这张卡的形状（父复选框 + 一个「写回」子复选框）看起来像是
 *    只关读，不写这一行，用户会以为收据还在攒，而经验库最贵的东西就是那些收据。
 *    只想「用但不沉淀」的用户走子复选框。
 * 2. **运维总闸关闭时整卡禁用**。显示一个勾上了却毫无作用的复选框比不显示更坏。
 * 3. **接口失败只隐藏那两行数字，绝不影响勾选与启动**——一个读不到历史的增强读数没有资格
 *    挡住真实资金会话的启动。
 */
export default function AiTradeExperienceFields({
  value,
  onChange,
  disabled = false,
  backtest = false,
}: {
  value: AiTradeExperienceRule;
  onChange: (next: AiTradeExperienceRule) => void;
  disabled?: boolean;
  /** 回测 tab：`write` 恒关且不可勾，`enabled` v1 亦恒关（回测样本不写入经验库）。 */
  backtest?: boolean;
}) {
  const [overview, setOverview] = useState<ExperienceOverview | null>(null);
  const [opsEnabled, setOpsEnabled] = useState<boolean | null>(null);
  const setShowSettings = useAppStore((s) => s.setShowSettings);
  const setSettingsSection = useAppStore((s) => s.setSettingsSection);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const [head, settings] = await Promise.all([
          experienceApi.overview(),
          experienceApi.settings(),
        ]);
        if (!alive) return;
        setOverview(head);
        setOpsEnabled(settings.enabled);
      } catch {
        // 接口失败（路由未注册 / 后端未起）⇒ 只隐藏数字，勾选与启动一律不受影响。
        if (alive) setOpsEnabled(null);
      }
    })();
    return () => { alive = false; };
  }, []);

  const offByOps = opsEnabled === false;
  const locked = disabled || offByOps || backtest;
  const lessons = overview
    ? overview.lessons.iron + overview.lessons.normal + overview.lessons.candidate
    : null;

  return (
    <section
      className="flex flex-col gap-1.5 rounded-md border border-[var(--border)] px-3 py-2"
      data-testid="ai-trade-experience-fields"
      aria-disabled={locked || undefined}
    >
      <div className="flex items-center justify-between gap-2">
        <label className="flex items-center gap-2 text-[11px] text-[var(--text)]">
          <input
            type="checkbox"
            checked={value.enabled && !backtest}
            disabled={locked}
            onChange={(e) => onChange({ ...value, enabled: e.target.checked })}
            aria-label="启用经验模式"
          />
          <span>启用经验模式（推荐）</span>
        </label>
        <button
          type="button"
          className="text-[11px] text-[var(--accent-ink)] underline-offset-2 hover:underline"
          onClick={() => { setSettingsSection("experience"); setShowSettings(true); }}
        >
          查看经验库 →
        </button>
      </div>

      {offByOps ? (
        <p className="text-[10px] text-[var(--muted)]" data-testid="ai-trade-experience-ops-off">
          {/* JSX 的文本节点是**纯文本**：写 `反引号` 只会让用户看到两个反引号（本仓库已在
              `**粗体**` 上复发两次）。要呈现环境变量名就用 <code>，不用 markdown 记法。 */}
          经验模式已被运维关闭（
          <code className="text-[var(--text)]">AI_TRADE_EXPERIENCE_ENABLED=false</code>
          ），本次会话不会读取也不会沉淀经验。
        </p>
      ) : backtest ? (
        <p className="text-[10px] text-[var(--muted)]">
          回测不读取也不写入经验库——回测的成本口径与实盘不同，混用会让两边的结论都不可比。
        </p>
      ) : (
        <>
          {overview && lessons !== null && (
            <p className="text-[10px] text-[var(--muted)]">
              让 Agent 参考过往 {overview.sessions} 组有成交记录的自动交易沉淀的 {lessons} 条经验
              （其中铁律 {overview.lessons.iron} 条）。
            </p>
          )}

          {value.enabled ? (
            <>
              <div className="flex flex-wrap items-center gap-4 text-[11px] text-[var(--muted)]">
                <label className="flex items-center gap-1.5">
                  <input
                    type="radio"
                    name="experience-scope"
                    checked={value.scope === "symbol"}
                    disabled={disabled}
                    onChange={() => onChange({ ...value, scope: "symbol" })}
                  />
                  <span>仅本标的经验（推荐）</span>
                </label>
                <label className="flex items-center gap-1.5">
                  <input
                    type="radio"
                    name="experience-scope"
                    checked={value.scope === "global"}
                    disabled={disabled}
                    onChange={() => onChange({ ...value, scope: "global" })}
                  />
                  <span>含跨标的通用经验</span>
                </label>
              </div>
              <label className="flex items-center gap-2 text-[11px] text-[var(--muted)]">
                <input
                  type="checkbox"
                  checked={value.write}
                  disabled={disabled}
                  onChange={(e) => onChange({ ...value, write: e.target.checked })}
                />
                <span>把本次会话的经验写回经验库</span>
              </label>
              {!value.write && (
                <p className="text-[10px] text-[var(--muted)]">
                  只读模式：Agent 会用经验，但这次交易的收据不会进入经验库。
                </p>
              )}
            </>
          ) : (
            <p className="text-[10px] leading-relaxed text-[var(--warn)]"
               data-testid="ai-trade-experience-off-note">
              本次会话
              <strong className="text-[var(--warn)]">既不读取也不沉淀</strong>
              经验——这段时间的交易不会进入经验库。只想「用但不沉淀」的话，请保持勾选并取消下面的
              「写回经验库」。
            </p>
          )}
        </>
      )}
    </section>
  );
}
