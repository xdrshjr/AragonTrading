"use client";

import { useEffect, useMemo, useState } from "react";
import { intelApi } from "@/lib/intelApi";
import { relativeTime } from "@/lib/intel";
import type { IntelSettings } from "@/lib/intelTypes";
import type { AiTradeIntelRule } from "@/lib/aiTradeTypes";
import { useAppStore } from "@/lib/store";

/**
 * 启动弹窗的「市场情报研判」卡片（**受控组件**，逐行镜像 `AiTradeExperienceFields`）。
 *
 * 四条必须写在界面上的事实：
 *
 * 1. **默认不勾。** 这与弹窗里其余六张卡（铁律 / 利润保护 / 交易周期 / 纪律 / 经验 / 趋势）
 *    的方向相反，是需求的明文要求：外部情报是一份会改变证据面的可选输入，用户没有明确要求时
 *    不该悄悄进入一次真实资金决策的上下文。
 * 2. **两个字数是真话。** 它们取自 `GET /settings/intel` 的回执，连「上下文开销约 N 倍」这个
 *    比值都是现算的——前端**一个数都不硬编码**（写死一个 480 之后，运维改一次旋钮，这张卡上
 *    的话就成了假话；本仓库已为同一形状栽过一次）。
 * 3. **条数取不到就整句不渲染。** 绝不先显示一个会被当真的 0——「今天没采到情报」与「这个数
 *    我没读到」是两句完全不同的话。
 * 4. **未配源 / 运维关 ⇒ 整卡禁用**并给一个「去设置」入口。显示一个勾上了却毫无作用的复选框
 *    比不显示更坏。
 */
export default function AiTradeIntelFields({
  value,
  onChange,
  instIds,
  disabled = false,
  backtest = false,
}: {
  value: AiTradeIntelRule;
  onChange: (next: AiTradeIntelRule) => void;
  /** 本次要启动的标的（条数逐标的取一次，`Promise.allSettled` 并发）。 */
  instIds: string[];
  disabled?: boolean;
  /** 回测 tab：整卡不渲染（情报层不进回测车道，与 `reasoningDepth` 同款处置）。 */
  backtest?: boolean;
}) {
  const [settings, setSettings] = useState<IntelSettings | null>(null);
  // `null` = 还没读到 / 读失败 ⇒ 整句不渲染；`{ items, lastTs }` = 读到了。
  const [stock, setStock] = useState<{ items: number; lastTs: number | null } | null>(null);
  const setShowSettings = useAppStore((s) => s.setShowSettings);
  const setSettingsSection = useAppStore((s) => s.setSettingsSection);
  const key = instIds.join(",");

  useEffect(() => {
    if (backtest) return;
    let alive = true;
    void (async () => {
      try {
        const wire = await intelApi.settings();
        if (alive) setSettings(wire);
      } catch {
        // 接口失败（路由未注册 / 后端未起）⇒ 只隐藏字数说明，勾选与启动一律不受影响。
        if (alive) setSettings(null);
      }
    })();
    return () => {
      alive = false;
    };
  }, [backtest]);

  useEffect(() => {
    if (backtest || instIds.length === 0) return;
    let alive = true;
    void (async () => {
      // 逐标的一次只读请求，**不新增接口、不新增批量端点**：一次启动最多十个标的，十次并发的
      // 只读请求远低于本仓库既有的任何一处扇出。
      const results = await Promise.allSettled(
        instIds.map((instId) => intelApi.read(instId, { limit: 1 })),
      );
      if (!alive) return;
      // **任何一个失败 ⇒ 整句不渲染**：一个只统计了半数标的的条数，比不给这个数更坏。
      if (results.some((r) => r.status !== "fulfilled")) {
        setStock(null);
        return;
      }
      let items = 0;
      let lastTs: number | null = null;
      for (const r of results) {
        if (r.status !== "fulfilled") continue;
        const { counts, lastCollectTs } = r.value.summary;
        // 口径 = 三层 active 计数，**不含 archived**。文案因此写「在库情报」而不是「有效情报」：
        // 它不等于「会被注入的条数」（注入还要过 refuted 过滤与预算）。
        items += counts.ultra + counts.mid + counts.long;
        if (lastCollectTs && (lastTs === null || lastCollectTs > lastTs)) lastTs = lastCollectTs;
      }
      setStock({ items, lastTs });
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, backtest]);

  const offByOps =
    settings !== null && (!settings.enabled || !settings.sessionOptInEnabled);
  const noProvider = settings !== null && !settings.arm.providerConfigured;
  const locked = disabled || offByOps || noProvider;

  const ratio = useMemo(() => {
    if (!settings || settings.indexMaxChars <= 0) return null;
    return Math.round((settings.promptMaxChars / settings.indexMaxChars) * 10) / 10;
  }, [settings]);

  // 回测车道恒不渲染这张卡（情报层从来就只在实盘车道装配）。
  if (backtest) return null;

  return (
    <section
      className="flex flex-col gap-1.5 rounded-md border border-[var(--border)] px-3 py-2"
      data-testid="ai-trade-intel-fields"
      aria-disabled={locked || undefined}
    >
      <div className="flex items-center justify-between gap-2">
        <label className="flex items-center gap-2 text-[11px] text-[var(--text)]">
          <input
            type="checkbox"
            checked={value.enabled && !locked}
            disabled={locked}
            onChange={(e) => onChange({ ...value, enabled: e.target.checked })}
            aria-label="让 Agent 参考市场情报"
          />
          <span>让 Agent 参考市场情报（默认关闭）</span>
        </label>
        <button
          type="button"
          className="text-[11px] text-[var(--accent-ink)] underline-offset-2 hover:underline"
          onClick={() => {
            setSettingsSection("intel-source");
            setShowSettings(true);
          }}
        >
          {noProvider || offByOps ? "去设置 →" : "查看情报库 →"}
        </button>
      </div>

      {offByOps ? (
        <p className="text-[10px] text-[var(--muted)]" data-testid="ai-trade-intel-ops-off">
          {/* JSX 的文本节点是**纯文本**：写 `反引号` 只会让用户看到两个反引号（本仓库已在
              `**粗体**` 上复发多次）。要呈现环境变量名就用 <code>，不用 markdown 记法。

              ⚠️ **两把闸的后果完全不同，绝不能合成一句话**：情报层总闸关 ⇒ 什么都不会喂；
              而**会话级**开关关 ⇒ 只是这个勾选框失效，喂不喂由服务端那把注入总闸独裁（它
              默认是开的）。一句合并的「本次会话不会把情报喂给 Agent」在后一格里是**反话**
              ——用户会以为自己没开，而模型每一轮都在读情报。 */}
          {!settings?.enabled
            ? "市场情报层已被运维关闭：情报既不采集，也不会喂给 Agent。"
            : `会话级情报开关已被运维关闭，本次是否把情报喂给 Agent 由服务端设置决定（当前：${
                settings?.injectEnabled ? "会喂给 Agent" : "不会喂给 Agent"
              }）。`}
        </p>
      ) : noProvider ? (
        <p className="text-[10px] text-[var(--muted)]" data-testid="ai-trade-intel-no-provider">
          尚未配置情报检索源，本次会话没有情报可以参考。
        </p>
      ) : (
        <>
          {stock !== null && stock.lastTs !== null && (
            <p className="text-[10px] text-[var(--muted)]" data-testid="ai-trade-intel-stock">
              已为本次的 {instIds.length} 个标的采集到 {stock.items} 条在库情报（最近
              {relativeTime(stock.lastTs, Date.now())}）。
            </p>
          )}
          {stock !== null && stock.lastTs === null && (
            <p className="text-[10px] text-[var(--muted)]" data-testid="ai-trade-intel-never">
              这些标的尚未成功采集过情报，本次可能一条都注入不了。
            </p>
          )}

          {value.enabled && (
            <>
              <div className="flex flex-wrap items-center gap-4 text-[11px] text-[var(--muted)]">
                <label className="flex items-center gap-1.5">
                  <input
                    type="radio"
                    name="intel-mode"
                    checked={value.mode === "index"}
                    disabled={disabled}
                    onChange={() => onChange({ ...value, mode: "index" })}
                  />
                  <span>索引档（推荐，省上下文）</span>
                </label>
                <label className="flex items-center gap-1.5">
                  <input
                    type="radio"
                    name="intel-mode"
                    checked={value.mode === "full"}
                    disabled={disabled}
                    onChange={() => onChange({ ...value, mode: "full" })}
                  />
                  <span>全文档</span>
                </label>
              </div>
              {settings && (
                <p className="text-[10px] leading-relaxed text-[var(--muted)]"
                   data-testid="ai-trade-intel-budget">
                  索引档：每轮只给 Agent 情报的标题与性质（约 {settings.indexMaxChars} 字）；
                  {settings.expandEnabled
                    ? "下单前的复核环节会自动展开它真正引用的那几条的完整摘要。"
                    : "（下单前的按需展开已被运维关闭。）"}
                  <br />
                  全文档：每轮给出完整摘要（约 {settings.promptMaxChars} 字）
                  {ratio !== null && `，上下文开销约 ${ratio} 倍`}。
                  {settings.repeatCompressEnabled &&
                    "同一根 K 线内的重复唤醒只列新增情报。"}
                </p>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
