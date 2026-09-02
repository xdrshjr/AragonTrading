"use client";

import { useEffect, useState } from "react";
import { Button, Card, Field, Input, Select, Switch } from "@/components/ui";
import IntelReaderBanner from "@/components/Intel/IntelReaderBanner";
import { relativeTime } from "@/lib/intel";
import { intelApi } from "@/lib/intelApi";
import {
  bytesLabel,
  describeEmptyBodyCache,
  describeReaderHealth,
  quoteHitRateLabel,
  strictReadiness,
} from "@/lib/intelEvidence";
import type { IntelDocumentHealth, IntelSettings } from "@/lib/intelTypes";

const QUOTE_MODES: { value: IntelSettings["quoteMode"]; label: string }[] = [
  { value: "off", label: "不要求（只用搜索摘要判真伪）" },
  { value: "soft", label: "记录但不强制（推荐）" },
  { value: "strict", label: "必须附可核对引文" },
];

// 解析策略（silent-degradation-repair 轴 B-1）。文案刻意不出现「SSRF」这类词：这一屏的读者
// 是交易员，他要判断的是「我这台机器上情报到底能不能抓」，不是一个安全术语。
const RESOLVERS: { value: IntelSettings["readerResolver"]; label: string }[] = [
  { value: "auto", label: "自动识别（兼容代理 / 加速器，推荐）" },
  { value: "strict", label: "严格（本机解析必须是公网地址）" },
];

/**
 * 设置中心「原文取证」组（ai-trade-intel-evidence-grounding §3.8）。
 *
 * 这一屏要回答三个问题：
 * 1. **要不要出网抓原文**——关掉之后情报仍会采集，但只能依据搜索摘要。这是一个默认开启的
 *    **出网能力**，它的关闭入口必须与功能同批交付（一个默认开启且当轮无法关闭的出网能力，
 *    是这一轮唯一不可接受的交付形态）。
 * 2. **要不要强制引文**——三选一，第三项下方常驻当前命中率与那条「低于 70% 不建议开启」。
 *    一条只写在文档里的运维判据等于没有判据。
 * 3. **占了多少盘**——`bodyBytes` 是后端算的**真实字节**，不是前端拿字符数乘出来的。
 *
 * 「每轮抓取篇数」走**草稿 + 显式保存**（与邻居的节律面板同形）：逐次敲键就 PUT 会发出一串
 * 中间态，而每一次成功的回读又会把输入框的值拽回去——那正是本仓库已经复发过的「输入自己回退」。
 */
export default function IntelReaderSettings({
  settings,
  busy,
  onSave,
}: {
  settings: IntelSettings;
  busy: boolean;
  onSave: (patch: Partial<IntelSettings>) => void;
}) {
  const [health, setHealth] = useState<IntelDocumentHealth | null>(null);
  const [topK, setTopK] = useState(String(settings.readerTopK));
  const [nowMs] = useState(() => Date.now());

  useEffect(() => {
    setTopK(String(settings.readerTopK));
  }, [settings.readerTopK]);

  useEffect(() => {
    intelApi
      .documentHealth()
      .then(setHealth)
      .catch(() => undefined);
  }, [settings.readerEnabled]);

  const dirty = topK.trim() !== "" && Number(topK) !== settings.readerTopK;
  const hitRate = quoteHitRateLabel(settings.quoteHitRate);
  // 抓取健康（silent-degradation-repair 轴 B-2）：文案在 `lib/intelEvidence` 单点装配，
  // 本组件只负责把它显示出来——两处各写一遍必然与后端真正的判据分叉。
  // 总闸 `ai_trade_intel_reader_health_visible` 由后端下发生效值：关掉时抓取健康行与横幅
  // **都不渲染**（读数仍照算——它们零成本），逐字节回到本轮之前。
  const healthVisible = settings.readerHealthVisible !== false;
  const reader = healthVisible ? describeReaderHealth(health) : null;
  const emptyBody = healthVisible ? describeEmptyBodyCache(health) : null;

  return (
    <Card className="flex flex-col gap-3 p-4" data-testid="intel-reader-settings">
      <span className="text-sm font-semibold text-[var(--text)]">原文取证</span>
      <span className="text-[11px] leading-relaxed text-[var(--muted)]">
        开启后，后端会第一方把被引用的网页抓回来，让情报 Agent 看到正文而不只是搜索摘要，
        并要求它从正文里逐字复制一段原文；服务端逐字比对通过的那一段会随情报一起留存，
        你随时可以点开读到它——即使那个网页日后失效。抓取只去检索源返回的地址，
        不带任何本地信息，抓回的正文一个字都不会进入交易决策。
      </span>

      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="text-[12px] text-[var(--text)]">抓取原文</span>
          <span className="text-[11px] leading-relaxed text-[var(--muted)]">
            关闭后情报仍会采集，但只能依据搜索摘要——最值钱的那类情报几乎必然带数字，
            而数字出现在摘要里是碰运气。
          </span>
        </div>
        <Switch
          checked={settings.readerEnabled}
          onChange={(checked) => {
            if (!busy) onSave({ readerEnabled: checked });
          }}
          label="抓取原文"
          className={busy ? "pointer-events-none opacity-50" : undefined}
        />
      </div>

      <span className="text-[11px] leading-relaxed text-[var(--muted)]">
        如果你在用透明代理 / 网络加速器（Clash、sing-box、Surge 一类的 fake-IP 模式），本机会把
        每一个公网域名都解析成一个 198.18.x 的占位地址——「严格」策略会因此把每一篇正文都
        拦下来，而日志里看起来只是「已拦截」。「自动识别」把这类占位地址与真正的内网地址分开，
        并改用连接建立后的真实对端地址复核，防护强度不降反升。
      </span>

      <div className="grid grid-cols-2 gap-3">
        <Field label="每轮抓取篇数" htmlFor="intel-reader-topk">
          <Input
            id="intel-reader-topk"
            type="number"
            min={1}
            max={12}
            value={topK}
            disabled={busy || !settings.readerEnabled}
            onChange={(e) => setTopK(e.target.value)}
          />
        </Field>
        <Field label="解析策略" htmlFor="intel-reader-resolver">
          <Select
            id="intel-reader-resolver"
            value={settings.readerResolver ?? "auto"}
            disabled={busy || !settings.readerEnabled}
            onChange={(e) =>
              onSave({ readerResolver: e.target.value as IntelSettings["readerResolver"] })}
          >
            {RESOLVERS.map((mode) => (
              <option key={mode.value} value={mode.value}>
                {mode.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="引文要求" htmlFor="intel-quote-mode">
          <Select
            id="intel-quote-mode"
            value={settings.quoteMode}
            disabled={busy}
            onChange={(e) =>
              onSave({ quoteMode: e.target.value as IntelSettings["quoteMode"] })}
          >
            {QUOTE_MODES.map((mode) => (
              <option
                key={mode.value}
                value={mode.value}
                // 关着抓取时「必须附可核对引文」只能靠搜索摘要命中，准入率会系统性归零，
                // 而那在界面上与「今天真的没新闻」分不出来。后端在生效值上也会把它降回
                // soft（`resolve_quote_mode`）——这里只是不让用户先走进那一步。
                disabled={mode.value === "strict" && !settings.readerEnabled}
              >
                {mode.label}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <div className="flex items-center justify-end">
        <Button
          variant="primary"
          size="sm"
          disabled={busy || !dirty}
          onClick={() => onSave({ readerTopK: Number(topK) })}
        >
          {busy ? "保存中…" : "保存篇数"}
        </Button>
      </div>

      <span data-testid="intel-strict-hint" className="text-[11px] text-[var(--muted)]">
        {settings.readerEnabled
          ? `${hitRate ? `${hitRate}。` : ""}${strictReadiness(settings.quoteHitRate)}`
          : "抓取原文已关闭，「必须附可核对引文」随之不可用——没有正文时它只会把准入率压到零。"}
      </span>

      {/* 零命中横幅（轴 B-2）：连续 N 轮「尝试了但一篇都没抓到」才出现。这一条排在读数**之上**
          ——它回答的是「为什么下面那两个数字是 0」，先看到结论才读得懂读数。 */}
      {healthVisible && (
        <IntelReaderBanner health={health} streakWarn={settings.readerZeroStreakWarn} />
      )}

      {reader && (
        <span
          data-testid="intel-reader-health"
          className={
            reader.tone === "danger"
              ? "text-[11px] leading-relaxed text-[var(--short-ink)]"
              : "text-[11px] leading-relaxed text-[var(--muted)]"
          }
        >
          {reader.text}
          {reader.hint ? ` · ${reader.hint}` : ""}
        </span>
      )}

      {health && (
        <span data-testid="intel-doc-cache" className="text-[11px] text-[var(--muted)]">
          原文缓存：{health.cachedDocs} 条 / 约 {bytesLabel(health.bodyBytes)}
          {health.lastPrunedTs > 0
            ? `，最近清理 ${relativeTime(health.lastPrunedTs, nowMs)}`
            : "，尚未清理过"}
          {/* 「缓存 29 篇 / 正文 0 字节」在健康安装上是**定义上不可能**的组合。这个事实三个星期
              来一直显示在这一行上，只是没有任何一处把它翻译成一句人能读懂的结论。 */}
          {emptyBody ? ` ${emptyBody}` : ""}
        </span>
      )}
    </Card>
  );
}
