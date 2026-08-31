"use client";

import { useEffect, useState } from "react";
import { Button, Card, Field, Input, Select, Switch } from "@/components/ui";
import { intelApi } from "@/lib/intelApi";
import type { IntelProbeResult, IntelProviderHealth, IntelSettings } from "@/lib/intelTypes";
import IntelArmStatusCard from "./IntelArmStatusCard";
import IntelReaderSettings from "./IntelReaderSettings";

// 三个适配器都已实现（intel-closed-loop 轴 D 兑现了 v1 登记的那条待办），故三项全部列出——
// v1 时代「故意不列出未实现的选项」的理由（一个选了就不工作的选项比没有这个选项更坏）在这里
// 已经不成立。**仍是单活**：选一个，不做多源扇出（§十 非目标 3）。
const PROVIDERS = [
  { value: "none", label: "不使用（关闭情报采集）", apply: "" },
  { value: "tavily", label: "Tavily（api.tavily.com）", apply: "https://tavily.com" },
  { value: "brave", label: "Brave Search（api.search.brave.com）", apply: "https://brave.com/search/api" },
  { value: "serper", label: "Serper（google.serper.dev）", apply: "https://serper.dev" },
];

/**
 * 申请地址**必须跟着当前选中的源走**。三个源各自发各自的 key，把一个选了 Brave 的用户指去
 * tavily.com 不是排版瑕疵——那是一条会让他白跑一趟并断定「这功能没做完」的错误指引。
 */
function applyLinkFor(provider: string): string {
  return PROVIDERS.find((p) => p.value === provider)?.apply ?? "";
}

/** 节律三项的草稿（字符串：用户把框清空的那一瞬不该变成 `0`）。 */
type CadenceDraft = { collectIntervalMin: string; curateEveryNCollects: string; dailyCap: string };

function cadenceOf(settings: IntelSettings | null): CadenceDraft {
  return {
    collectIntervalMin: String(settings?.collectIntervalMin ?? ""),
    curateEveryNCollects: String(settings?.curateEveryNCollects ?? ""),
    dailyCap: String(settings?.dailyCap ?? ""),
  };
}

/** 告警两项的草稿（同上：走草稿 + 显式保存，逐次敲键就 PUT 会发出一串越界的中间态）。 */
type AlertDraft = { alertCooldownMin: string; alertDailyCap: string };

function alertOf(settings: IntelSettings | null): AlertDraft {
  return {
    alertCooldownMin: String(settings?.alertCooldownMin ?? ""),
    alertDailyCap: String(settings?.alertDailyCap ?? ""),
  };
}

/**
 * 设置中心「情报检索源」卡片（ai-trade-market-intel §3.8.5）。
 *
 * 这一屏要回答三个问题，缺一不可：
 * 1. **接没接上**——「测试连接」跑一次真实检索并回一条样例标题（服务端总耗时硬封顶 12 秒）。
 * 2. **要花多少钱**——底部的成本估算由后端按当前设置算好下发，在按下开关**之前**就看得见。
 * 3. **喂不喂给交易 Agent**——`injectEnabled` 是独立开关：采了不喂等于做了个只读面板，而
 *    「从没成功采过就先改 prompt」是纯粹的无谓风险，两件事因此分开控制。
 */
export default function IntelSourceSettings() {
  const [settings, setSettings] = useState<IntelSettings | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [probe, setProbe] = useState<IntelProbeResult | null>(null);
  const [draft, setDraft] = useState<CadenceDraft>(() => cadenceOf(null));
  const [alertDraft, setAlertDraft] = useState<AlertDraft>(() => alertOf(null));
  const [health, setHealth] = useState<IntelProviderHealth | null>(null);

  useEffect(() => {
    intelApi
      .settings()
      .then((next) => {
        setSettings(next);
        setDraft(cadenceOf(next));
        setAlertDraft(alertOf(next));
      })
      .catch((err) => setError(err instanceof Error ? err.message : "读取设置失败"));
    // 每源健康是**观测**（closed-loop §3.4.4）：不新建任何表、不新建任何计数器，纯读
    // `ai_intel_rounds` 的聚合 + 进程内 breaker 剩余秒。取不到时整行不渲染，不显示一个假的 0。
    intelApi
      .providerHealth()
      .then(setHealth)
      .catch(() => undefined);
  }, []);

  // “改过”才能保存：一个永远亮着的保存按钮说不清自己到底有没有事情可做。
  const dirty =
    settings !== null &&
    JSON.stringify(draft) !== JSON.stringify(cadenceOf(settings));
  const alertDirty =
    settings !== null &&
    JSON.stringify(alertDraft) !== JSON.stringify(alertOf(settings));

  const save = async (patch: Partial<IntelSettings> & { apiKey?: string }) => {
    setBusy(true);
    setError("");
    try {
      const next = await intelApi.saveSettings({
        enabled: patch.enabled,
        injectEnabled: patch.injectEnabled,
        provider: patch.provider,
        apiKey: patch.apiKey,
        collectIntervalMin: patch.collectIntervalMin,
        curateEveryNCollects: patch.curateEveryNCollects,
        dailyCap: patch.dailyCap,
        // closed-loop 的三把安全单键。**逐个透传**而不是 `...patch`：那会把
        // `hasKey` / `keyPreview` / `estimate` 这些只读键一起 PUT 出去（422），而
        // 「保存失败」在一块开关面板上会被读成「这个开关坏了」。
        citeEnabled: patch.citeEnabled,
        attributionEnabled: patch.attributionEnabled,
        verifyEnabled: patch.verifyEnabled,
        // evidence-grounding：轴 A / 轴 B 的三把单键，同样**逐个透传**。
        readerEnabled: patch.readerEnabled,
        readerTopK: patch.readerTopK,
        quoteMode: patch.quoteMode,
        // actionability：三把总闸 + 三个告警旋钮，同样**逐个透传**（`...patch` 会把
        // `hasKey` / `estimate` 这些只读键一起 PUT 出去并 422）。
        alertEnabled: patch.alertEnabled,
        alertMinImpact: patch.alertMinImpact,
        alertCooldownMin: patch.alertCooldownMin,
        alertDailyCap: patch.alertDailyCap,
        efficacyEnabled: patch.efficacyEnabled,
        conflictEnabled: patch.conflictEnabled,
      });
      setSettings(next);
      setDraft(cadenceOf(next));
      setAlertDraft(alertOf(next));
      if (patch.apiKey) setApiKey("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  /**
   * 手动重新装配。**回执与 `save()` 走同一条路**（整份 `IntelSettings`，含 `arm`），故这张卡
   * 上的状态在点完之后立刻说新话，不需要刷新页面。
   */
  const rearm = async () => {
    setBusy(true);
    setError("");
    try {
      setSettings(await intelApi.rearm());
    } catch (err) {
      setError(err instanceof Error ? err.message : "重新装配失败");
    } finally {
      setBusy(false);
    }
  };

  const runProbe = async () => {
    setBusy(true);
    setProbe(null);
    setError("");
    try {
      setProbe(await intelApi.probe(apiKey.trim() ? { apiKey: apiKey.trim() } : {}));
    } catch (err) {
      setError(err instanceof Error ? err.message : "测试连接失败");
    } finally {
      setBusy(false);
    }
  };

  if (!settings) {
    return (
      <Card className="p-4 text-[11px] text-[var(--muted)]">
        {error || "正在读取情报检索源设置…"}
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4" data-testid="intel-source-settings">
      <Card className="flex flex-col gap-3 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex flex-col gap-0.5">
            <span className="text-sm font-semibold text-[var(--text)]">情报检索源</span>
            <span className="text-[11px] leading-relaxed text-[var(--muted)]">
              开启后，一个与交易 Agent 完全分开的情报 Agent 会为每个正在自动交易的标的定期检索
              公开信源，把结果摘要成几句话的短情报并逐条校验来源与数字，再存进该标的的情报库。
              未配置检索源时本系统一条情报都不会产出，也绝不让模型凭空生成。
            </span>
          </div>
          <Switch
            checked={settings.enabled}
            onChange={(checked) => void save({ enabled: checked })}
            label="启用市场情报采集"
          />
        </div>

        <Field label="检索源" htmlFor="intel-provider">
          <Select
            id="intel-provider"
            value={settings.provider}
            disabled={busy}
            onChange={(e) => void save({ provider: e.target.value })}
          >
            {PROVIDERS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </Select>
        </Field>

        <Field
          label="API Key"
          htmlFor="intel-api-key"
          hint={
            settings.hasKey
              ? `已保存（${settings.keyPreview}）。留空即保持不变；填入新值即覆盖。`
              : applyLinkFor(settings.provider)
                ? `尚未保存。可在 ${applyLinkFor(settings.provider)} 申请，免费额度足够日常使用。`
                : "尚未保存。先在上面选一个检索源，再来填它的 API Key。"
          }
        >
          <div className="flex items-center gap-1.5">
            <Input
              id="intel-api-key"
              type="password"
              value={apiKey}
              autoComplete="off"
              placeholder={settings.hasKey ? "••••••••" : "粘贴 API Key"}
              onChange={(e) => setApiKey(e.target.value)}
              className="min-w-0 flex-1"
            />
            <Button
              variant="default"
              size="sm"
              disabled={busy || !apiKey.trim()}
              onClick={() => void save({ apiKey: apiKey.trim() })}
            >
              保存
            </Button>
            {settings.hasKey && (
              <Button
                variant="danger"
                size="sm"
                disabled={busy}
                onClick={() => void save({ apiKey: "__clear__" })}
              >
                清除
              </Button>
            )}
          </div>
        </Field>

        <div className="flex items-center gap-2">
          <Button variant="default" size="sm" disabled={busy} onClick={runProbe}>
            测试连接
          </Button>
          {probe && (
            <span
              data-testid="intel-probe-result"
              className={
                probe.ok
                  ? "text-[11px] text-[var(--long-ink)]"
                  : "text-[11px] text-[var(--short-ink)]"
              }
            >
              {probe.ok
                ? `连接正常（${probe.latencyMs}ms）｜样例：${probe.sampleTitle}`
                : probe.message}
            </span>
          )}
        </div>
        {error && <span className="text-[11px] text-[var(--short-ink)]">{error}</span>}
      </Card>

      {/* 生效状态卡挂在「情报检索源」卡**正下方**：用户刚存完 key，视线就在那里。
          「保存成功」与「真的生效了」不是一回事，而这块面板此前连一条读路径都没有。 */}
      <IntelArmStatusCard arm={settings.arm} busy={busy} onRearm={() => void rearm()} />

      <Card className="flex flex-col gap-3 p-4">
        <span className="text-sm font-semibold text-[var(--text)]">采集节律与额度</span>
        {/* 三个数字框走**草稿 + 显式保存**（与邻居 `ExperienceSection` 的阈值面板同形）。
            逐次敲键就 PUT 会发出一串中间态：把「30」改成「120」的路上先发出一个 1（< 下限 5，422），
            而每一次成功的回读又会把输入框的值拽回去——那正是本仓库已经复发过的「输入自己回退」。 */}
        <div className="grid grid-cols-3 gap-3">
          <Field label="采集间隔（分钟）" htmlFor="intel-interval">
            <Input
              id="intel-interval"
              type="number"
              min={5}
              max={1440}
              value={draft.collectIntervalMin}
              disabled={busy}
              onChange={(e) =>
                setDraft((prev) => ({ ...prev, collectIntervalMin: e.target.value }))}
            />
          </Field>
          <Field label="整理频率（每 N 轮）" htmlFor="intel-curate">
            <Input
              id="intel-curate"
              type="number"
              min={1}
              max={24}
              value={draft.curateEveryNCollects}
              disabled={busy}
              onChange={(e) =>
                setDraft((prev) => ({ ...prev, curateEveryNCollects: e.target.value }))}
            />
          </Field>
          <Field label="每标的每日轮数上限" htmlFor="intel-cap">
            <Input
              id="intel-cap"
              type="number"
              min={1}
              max={1000}
              value={draft.dailyCap}
              disabled={busy}
              onChange={(e) => setDraft((prev) => ({ ...prev, dailyCap: e.target.value }))}
            />
          </Field>
        </div>
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" disabled={busy || !dirty}
                  onClick={() => setDraft(cadenceOf(settings))}>
            还原
          </Button>
          <Button variant="primary" size="sm" disabled={busy || !dirty}
                  onClick={() => void save({
                    collectIntervalMin: Number(draft.collectIntervalMin),
                    curateEveryNCollects: Number(draft.curateEveryNCollects),
                    dailyCap: Number(draft.dailyCap),
                  })}>
            {busy ? "保存中…" : "保存节律"}
          </Button>
        </div>
        <span className="text-[11px] text-[var(--muted)]">
          按当前设置，约 {settings.estimate.roundsPerDay} 轮/天、约{" "}
          {(settings.estimate.inputTokensPerDay / 10000).toFixed(1)} 万 input token/天。
          触顶后当日不再采集，UTC 0 点重置。
        </span>
      </Card>

      <Card className="flex flex-col gap-3 p-4" data-testid="intel-closed-loop-switches">
        <span className="text-sm font-semibold text-[var(--text)]">情报闭环</span>
        <span className="text-[11px] leading-relaxed text-[var(--muted)]">
          让每一条情报可被引用、可被结算、可被证伪。三把开关逐层收窄：关掉「引用编号」，
          注入行与决策工具会逐字节回到没有本功能的样子；关掉「引用归因」，引用照记但不结算；
          关掉「定期核查」，不再有任何自动的证实 / 证伪判定。已有的统计与判定一律原样保留。
        </span>
        <ToggleRow
          label="给情报编号，让模型能点名引用"
          hint="注入行前面加 [I01] 这样的编号；模型可在决策里写下它实际采信了哪几条。纯观测，不参与任何风控判定。"
          checked={settings.citeEnabled}
          disabled={busy}
          onChange={(checked) => void save({ citeEnabled: checked })}
        />
        <ToggleRow
          label="按往返净额结算引用战绩"
          hint="平仓时把这笔往返的净额回写到当轮引用过的每一条情报上。这些数字是共现统计，不是因果归因。"
          checked={settings.attributionEnabled}
          disabled={busy}
          onChange={(checked) => void save({ attributionEnabled: checked })}
        />
        <ToggleRow
          label="定期核查已入库的情报"
          hint={`每 ${settings.verifyEveryNCurates} 次整理轮跑一次核查，单轮最多 ${settings.verifyMaxItems} 条、每条检索 ${settings.verifyDocsPerItem} 篇。首次判「已证伪」只停止注入、不改层级。`}
          checked={settings.verifyEnabled}
          disabled={busy}
          onChange={(checked) => void save({ verifyEnabled: checked })}
        />
        {health && health.rounds > 0 && (
          <span data-testid="intel-provider-health" className="text-[11px] text-[var(--muted)]">
            最近 {health.rounds} 轮：{health.provider || "未知源"} {health.ok} 成功 /{" "}
            {health.failed + health.degraded} 失败或降级，熔断剩余 {health.breakerRemainingS} 秒
          </span>
        )}
      </Card>

      <IntelReaderSettings settings={settings} busy={busy} onSave={(patch) => void save(patch)} />

      <Card className="flex flex-col gap-3 p-4" data-testid="intel-alert-settings">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 flex-col gap-0.5">
            <span className="text-sm font-semibold text-[var(--text)]">
              高危情报当场唤醒一次决策
            </span>
            <span className="text-[11px] leading-relaxed text-[var(--muted)]">
              一条够格的高危情报（影响力达标 · 类别在范围内 · 有逐字原文引证 · 至少两个独立来源 ·
              置信度达标 · 事件足够新）会让该标的多问一次 AI——走的是同一个决策轮，
              共识门禁、风控铁律、成本门禁、仓位层、下单前复核全部照跑，一道不绕。
              它既不下单，也不改仓位。每一次判定（含「没告警」）都写进情报弹窗的「动态」页签。
            </span>
          </div>
          <Switch
            checked={settings.alertEnabled}
            onChange={(checked) => void save({ alertEnabled: checked })}
            label="启用情报告警唤醒"
          />
        </div>

        <div className="grid grid-cols-3 gap-3">
          <Field label="影响力门槛" htmlFor="intel-alert-impact">
            <Select
              id="intel-alert-impact"
              value={settings.alertMinImpact}
              disabled={busy || !settings.alertEnabled}
              onChange={(e) =>
                void save({ alertMinImpact: e.target.value as "medium" | "high" })}
            >
              <option value="high">只有「高」才唤醒（推荐）</option>
              <option value="medium">「中」及以上都唤醒</option>
            </Select>
          </Field>
          <Field label="冷却（分钟）" htmlFor="intel-alert-cooldown">
            <Input
              id="intel-alert-cooldown"
              type="number"
              min={1}
              max={720}
              value={alertDraft.alertCooldownMin}
              disabled={busy || !settings.alertEnabled}
              onChange={(e) =>
                setAlertDraft((prev) => ({ ...prev, alertCooldownMin: e.target.value }))}
            />
          </Field>
          <Field label="每标的每日上限" htmlFor="intel-alert-cap">
            <Input
              id="intel-alert-cap"
              type="number"
              min={1}
              max={48}
              value={alertDraft.alertDailyCap}
              disabled={busy || !settings.alertEnabled}
              onChange={(e) =>
                setAlertDraft((prev) => ({ ...prev, alertDailyCap: e.target.value }))}
            />
          </Field>
        </div>
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" disabled={busy || !alertDirty}
                  onClick={() => setAlertDraft(alertOf(settings))}>
            还原
          </Button>
          <Button variant="primary" size="sm" disabled={busy || !alertDirty}
                  onClick={() => void save({
                    alertCooldownMin: Number(alertDraft.alertCooldownMin),
                    alertDailyCap: Number(alertDraft.alertDailyCap),
                  })}>
            {busy ? "保存中…" : "保存告警阈值"}
          </Button>
        </div>

        {/* 「须有逐字原文引证」是一个**只读的生效值**：`quoteMode="off"` 或抓取关闭时它被系统
            降级为「不要求」，而用户存下来的偏好一个字节没动。不说清楚这件事，「要求引文 +
            引文关闭」这个从面板就能走进来的组合会让告警**一次都不发生且没有任何错误**。 */}
        <span
          data-testid="intel-alert-require-quote"
          className={
            settings.alertRequireQuote
              ? "text-[11px] text-[var(--muted)]"
              : "text-[11px] text-[var(--warn)]"
          }
        >
          {settings.alertRequireQuote
            ? "当前要求：够格的情报必须带一段可核对的原文引证。"
            : "当前不要求原文引证——因为上面的「引文要求」是「不要求」或原文抓取已关闭。" +
              "打开其中任一项即自动恢复（你的偏好没有被改写）。"}
        </span>
      </Card>

      <Card className="flex flex-col gap-3 p-4" data-testid="intel-actionability-switches">
        <span className="text-sm font-semibold text-[var(--text)]">情报的战绩与去噪</span>
        <span className="text-[11px] leading-relaxed text-[var(--muted)]">
          两把开关都只改变排序与提示，不改变任何一条情报能不能入库、能不能被注入。
          关掉之后，注入顺序与整理轮的工具定义会逐字节回到没有本功能的样子。
        </span>
        <ToggleRow
          label="用历史战绩给情报排序"
          hint="按类别聚合「采信之后赚没赚到钱」与「事后被证实还是被证伪」，样本不足时权重恒为 0。它只影响排序与「升长期要写多少理由」，不是门禁。"
          checked={settings.efficacyEnabled}
          disabled={busy}
          onChange={(checked) => void save({ efficacyEnabled: checked })}
        />
        <ToggleRow
          label="标出互相矛盾的情报"
          hint="同类别、方向相反、主题高度重叠的两条会成对出现在决策 prompt 里，并要求整理轮给出裁决。系统不会自动删除任何一方——哪一条为真，确定性规则判断不了。"
          checked={settings.conflictEnabled}
          disabled={busy}
          onChange={(checked) => void save({ conflictEnabled: checked })}
        />
      </Card>

      <Card className="flex items-start justify-between gap-3 p-4">
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-semibold text-[var(--text)]">喂给交易 Agent</span>
          <span className="text-[11px] leading-relaxed text-[var(--muted)]">
            关闭后，情报照常采集、照常在弹窗里可读可管，但一个字都不会进入决策 prompt。
            情报只作为参考素材出现，不参与任何确定性门禁、也不改变任何阈值。
            该标的完成第一轮成功采集之前，本项即便开着也不会注入。
          </span>
        </div>
        <Switch
          checked={settings.injectEnabled}
          onChange={(checked) => void save({ injectEnabled: checked })}
          label="把情报注入交易决策"
        />
      </Card>
    </div>
  );
}

/** 一行开关（标题 + 一句「关掉会怎样」+ Switch）。三把安全单键共用同一形状。 */
function ToggleRow({
  label,
  hint,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  hint: string;
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="text-[12px] text-[var(--text)]">{label}</span>
        <span className="text-[11px] leading-relaxed text-[var(--muted)]">{hint}</span>
      </div>
      {/* `ui/Switch` 是一个 `role="switch"` 的 `<button>`，但它**没有透出 `disabled`**。保存在飞
          时因此只能靠回调忽略这一次点击；那必须配一个看得见的失效态，否则就成了「看起来能点、
          点了什么也不发生」——本轮要修的正是这一类沉默。为一处调用点去改共用原子组件的公开面，
          代价大于收益。 */}
      <Switch
        checked={checked}
        onChange={(next) => {
          if (!disabled) onChange(next);
        }}
        label={label}
        className={disabled ? "pointer-events-none opacity-50" : undefined}
      />
    </div>
  );
}
