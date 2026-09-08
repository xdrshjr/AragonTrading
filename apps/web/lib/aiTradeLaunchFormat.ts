/**
 * AI 自动交易启动向导的**纯格式化 helper**（ai-trade-guided-launch §4.4）。
 *
 * 本文件是 `components/Agent/AiTradeLaunchModal.tsx` 拆分前 :1561-1707 之间**全部**纯函数的
 * 新家，逐条零改（判据用**区间**而不用名单：名单会漏而区间不会，【v2 · P2-1】）。
 *
 * 它们全部只做「值 → 展示串」或「展示串 → 值」的转换，不读任何 state、不发任何请求，
 * 因而既能被向导的六个步骤共用，也能被纯函数用例直接覆盖。
 */
import { clampPct } from "@/lib/liveAuto";
import { AI_TRADE_GIVEBACK_TIER_PRESETS, marginReturnFromPricePct } from "@/lib/aiTrade";
import type {
  AiTradeGivebackTier,
  AiTradeHorizonProfileId,
  AiTradeHorizonRule,
} from "@/lib/aiTradeTypes";

/** datetime-local → UTC 秒；空/非法 → null。 */
export function fromLocalInput(value: string): number | null {
  if (!value) return null;
  const ms = new Date(value).getTime();
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}

/** UTC 秒 → 本地 datetime-local 字符串（YYYY-MM-DDTHH:mm）。 */
export function toLocalInput(ts: number): string {
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 单仓 OCO 比例预览：留空→「默认」；有值→夹紧到 max 后按百分比展示（如 0.9→「20%」）。 */
export function fmtOcoPct(value: number | null, max: number): string {
  if (value == null) return "默认";
  return `${+(clampPct(value, max) * 100).toFixed(2)}%`;
}

/**
 * 单仓 OCO 的**双口径**渲染（诊断根因 ②/⑤）：价格口径 +「≈保证金 ±X%」。
 * 留空时不再只写「默认」——那正是让用户不知不觉吃下 10%/5% 宽阈值的元凶，改为如实写出生效默认值。
 * 杠杆留空 ⇒ `marginPct` 为 null ⇒ 只出价格口径（绝不猜后端的 live_default_leverage，[P1-9]）。
 */
export function fmtOcoDual(
  value: number | null,
  max: number,
  fallback: number,
  marginPct: number | null,
): string {
  const effective = value == null ? fallback : clampPct(value, max);
  const head = `${+(effective * 100).toFixed(2)}%${value == null ? "（默认）" : ""}`;
  if (marginPct == null) return head;
  return `${head}（≈保证金 ${marginPct >= 0 ? "" : "-"}${+(Math.abs(marginPct) * 100).toFixed(1)}%）`;
}

/** OCO 输入框 placeholder：把「留空＝默认」的隐藏语义写成具体数值（+ 已知杠杆时的保证金口径）。 */
export function ocoPlaceholder(fallback: number, leverage: number | null, sign: string): string {
  const margin = marginReturnFromPricePct(fallback, leverage);
  const base = `留空＝默认 ${+(fallback * 100).toFixed(2)}%`;
  return margin == null ? base : `${base}（≈保证金 ${sign}${+(margin * 100).toFixed(0)}%）`;
}

/** 百分数输入串 → fraction（空串 / 非数 → NaN，交由 validateProfitGuard 报错，不静默吞）。 */
export function rawPctToFraction(raw: string): number {
  const n = Number(raw);
  return Number.isFinite(n) ? n / 100 : Number.NaN;
}

/** fraction → 百分数输入串（缺省回落给定默认，保证输入框永不出现空白/NaN）。 */
export function pctToRaw100(value: number | null | undefined, fallback: number): string {
  const n = value ?? fallback;
  return String(+(n * 100).toFixed(4));
}

/** fraction → 单仓 OCO 输入串（缺省 ⇒ 空串＝沿用服务端默认，保持「留空」这一合法退出口）。 */
export function pctToRaw(value: number | null | undefined): string {
  return value == null ? "" : String(value);
}

/** 利润保护实时预览（纯前端派生，无请求）；杠杆未知时诚实省略标的涨跌幅换算（[P1-9]）。 */
export function guardPreview(
  armRaw: string,
  givebackRaw: string,
  armPriceMovePct: number | null,
): string {
  const arm = Number(armRaw);
  const giveback = Number(givebackRaw);
  if (!Number.isFinite(arm) || !Number.isFinite(giveback)) return "";
  const scope =
    armPriceMovePct == null
      ? `浮盈达 ${+arm.toFixed(2)}%（保证金口径）`
      : `浮盈达 ${+arm.toFixed(2)}%（≈标的 ${+(armPriceMovePct * 100).toFixed(2)}%）`;
  return `${scope}即武装 → 自峰值回吐 ${+giveback.toFixed(2)}% 或跌回保本线即由系统平仓。`;
}

/**
 * 交易周期「自定义」输入的回填种子（holding-horizon §2.11）。
 *
 * **全部留空**是本特性的安全退出口：空串 ⇒ 提交体不带该键 ⇒ 服务端按 `custom` 预设装配
 * （0 / 1 / 0 + expectedHoldBars=None）⇒ 逐字等价于本层未装配。绝不在此填一组「看起来合理」的
 * 默认数字——那会让一个本应零副作用的选择静默改写成本模型的资金费期数（[P1-1]）。
 */
export function seedHorizonCustom(rule: AiTradeHorizonRule | undefined) {
  return {
    minHoldRaw: numToRaw(rule?.minHoldSeconds),
    confirmRoundsRaw: numToRaw(rule?.exitConfirmRounds),
    hysteresisRaw: numToRaw(rule?.turnoverHysteresisK),
    targetMinHoursRaw: secondsToHoursRaw(rule?.targetHoldMinSeconds),
    targetMaxHoursRaw: secondsToHoursRaw(rule?.targetHoldMaxSeconds),
  };
}

export type HorizonCustomSeed = ReturnType<typeof seedHorizonCustom>;

export function rawToNumber(raw: string): number | null {
  // 留空 ⇒ `null` ⇒ 整字段不发 ⇒ **服务端回填默认**（前端不存任何默认数值，唯一真源在 config.py）。
  // 非数字串同样回 `null`：输入中途的半截串不该被当成一个真值提交，也不该拦住启动。
  const text = raw.trim();
  if (!text) return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : null;
}

export function numToRaw(value: number | null | undefined): string {
  return value == null ? "" : String(value);
}

export function secondsToHoursRaw(value: number | null | undefined): string {
  return value == null ? "" : String(+(value / 3600).toFixed(4));
}

/**
 * 「自定义」模式的逐项覆盖（非 custom 模式**一个覆盖都不发**）。
 *
 * 非 custom 模式的数字全部以服务端预设为准：前端多发一份等于把那张 11 列的表抄了一遍，而本仓库
 * 已经为「前后端各存一份口径」付过学费（R-3）。空串 ⇒ 该键缺席 ⇒ 服务端按预设回填。
 */
export function horizonOverrides(
  profileId: AiTradeHorizonProfileId,
  custom: HorizonCustomSeed,
) {
  if (profileId !== "custom") return {};
  const out: Partial<AiTradeHorizonRule> = {};
  if (custom.minHoldRaw.trim()) out.minHoldSeconds = Number(custom.minHoldRaw);
  if (custom.confirmRoundsRaw.trim()) out.exitConfirmRounds = Number(custom.confirmRoundsRaw);
  if (custom.hysteresisRaw.trim()) out.turnoverHysteresisK = Number(custom.hysteresisRaw);
  if (custom.targetMinHoursRaw.trim()) {
    out.targetHoldMinSeconds = Number(custom.targetMinHoursRaw) * 3600;
  }
  if (custom.targetMaxHoursRaw.trim()) {
    out.targetHoldMaxSeconds = Number(custom.targetMaxHoursRaw) * 3600;
  }
  return out;
}

/** 阶梯预设 → 阶梯数组（未知 key 回落标准预设，绝不静默提交一个空阶梯）。 */
export function tiersForPreset(value: string): AiTradeGivebackTier[] {
  const hit = AI_TRADE_GIVEBACK_TIER_PRESETS.find((preset) => preset.value === value);
  return (hit ?? AI_TRADE_GIVEBACK_TIER_PRESETS[0]).tiers;
}

/** 阶梯数组 → 预设 key（回填上次偏好；无匹配预设时回落标准，UI 不显示一个不存在的选项）。 */
export function matchTierPreset(tiers: AiTradeGivebackTier[] | null | undefined): string {
  if (tiers == null) return AI_TRADE_GIVEBACK_TIER_PRESETS[0].value;
  const key = (list: AiTradeGivebackTier[]) =>
    list.map((t) => `${t.peakReturnPct}:${t.givebackPct}`).join(",");
  const hit = AI_TRADE_GIVEBACK_TIER_PRESETS.find((preset) => key(preset.tiers) === key(tiers));
  return (hit ?? AI_TRADE_GIVEBACK_TIER_PRESETS[0]).value;
}
