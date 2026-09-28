"use client";

import { environmentLabel, tradingTime,
  type AccountSnapshot } from "@/lib/agentTradingTypes";

const LABELS: Record<string, string> = {
  instId: "交易标的", direction: "方向", posSide: "持仓方向",
  side: "买卖方向", pos: "持仓（张）", sizeContracts: "数量（张）",
  sz: "数量（张）", availPos: "可平仓（张）", avgPx: "均价（USDT）",
  upl: "未实现盈亏（USDT）", totalEqUsd: "总权益（USD）",
  availEq: "可用权益（USDT）", availableUsdt: "可用余额（USDT）",
  availUsdt: "可用余额（USDT）", usdtAvailable: "可用余额（USDT）",
  availableBalanceUsdt: "可用余额（USDT）",
  availableMarginUsdt: "可用保证金（USDT）", frozenBal: "冻结余额",
  eqUsd: "权益（USD）", markPx: "标记价格（USDT）", margin: "保证金（USDT）",
  fillPnl: "成交盈亏（USDT）", tpTriggerPx: "止盈触发价", slTriggerPx: "止损触发价",
  ordId: "订单号", clOrdId: "客户订单号", algoId: "保护委托号",
  state: "交易所状态", exchangeState: "交易所状态", accFillSz: "累计成交（张）",
  fillSz: "成交（张）", fillPx: "成交价（USDT）", fee: "费用",
  feeCcy: "费用币种", ccy: "币种", eq: "权益", cashBal: "现金余额",
  availBal: "可用余额", label: "账户", id: "编号", lever: "现有杠杆",
  mgnMode: "保证金模式", px: "价格（USDT）", ordType: "订单类型",
  fillTime: "成交时间", ts: "时间", tradeId: "成交编号",
};
const VALUES: Record<string, string> = {
  long: "多", short: "空", net: "净仓", buy: "买", sell: "卖",
  cross: "全仓", isolated: "逐仓", live: "未成交",
  partially_filled: "部分成交", filled: "已成交", canceled: "已撤销",
  market: "市价", limit: "限价",
};

function scalar(value: unknown): string {
  if (value == null) return "未知";
  if (typeof value === "boolean") return value ? "是" : "否";
  return VALUES[String(value)] ?? String(value);
}

function fieldLabel(key: string, row: Record<string, unknown>): string {
  const units = row.fieldUnits as Record<string, string | null> | undefined;
  if (!units || !(key in units)) return LABELS[key];
  const label = LABELS[key].replace(/（[^）]*）$/, "");
  const unit = units[key] === "contracts" ? "张" : units[key];
  return `${label}（${unit || "单位未知"}）`;
}

/** 白名单字段展示事实，不把缺失数值变成零，不把算法委托变成可撤交易。 */
export default function AccountSnapshotCard({ snapshot, toolName }: {
  snapshot: AccountSnapshot; toolName?: string;
}) {
  const raw = snapshot.data;
  const nested = !Array.isArray(raw)
    ? [raw.items, raw.positions, raw.orders, raw.fills, raw.accounts]
      .find(Array.isArray) : null;
  const rows = Array.isArray(raw) ? raw
    : nested ? nested as Record<string, unknown>[] : [raw];
  return <section aria-label="账户查询结果"
    className="min-w-0 rounded-[10px] border border-[var(--border)] bg-[var(--surface)] p-3 text-xs">
    <p className="font-semibold">{snapshot.accountLabel || "交易账户目录"}
      {" · "}{environmentLabel(snapshot.environment)}</p>
    <p className="mt-1 text-[var(--muted)]">
      上次查询：{tradingTime(snapshot.asOfTs)} · 来源：{snapshot.source}</p>
    {snapshot.coverage.status === "partial" && <p role="status"
      className="mt-2 text-[var(--short-ink)]">仅取得部分数据，不能代表全部账户。</p>}
    {snapshot.truncated && <p role="status">结果已截断，仅展示 {snapshot.returnedCount} 条。</p>}
    {rows.length === 0 && snapshot.coverage.status === "complete"
      && <p role="status" className="mt-2">
      {toolName === "get_account_positions" ? "本次成功查询：当前无持仓。"
        : "本次成功查询：当前范围内无记录。"}</p>}
    <div className="space-y-2">
      {rows.map((row, index) => <div key={String(row.rowKey ?? row.ordId ?? index)}
        className="mt-2 rounded-md bg-[var(--surface-2)] p-2">
        {row.kind && row.kind !== "ordinary" && row.kind !== "normal"
          ? <p className="mb-1">保护委托，只读</p> : null}
        <dl className="grid grid-cols-2 gap-2">
          {Object.entries(row).filter(([key, value]) => LABELS[key]
            && (value == null || typeof value !== "object"))
            .map(([key, value]) => <div key={key} className="min-w-0">
              <dt className="text-[var(--muted)]">{fieldLabel(key, row)}</dt>
              <dd className="break-all font-mono">{scalar(value)}</dd>
            </div>)}
        </dl>
      </div>)}
    </div>
    {!Array.isArray(raw) && Array.isArray(raw.details) && <details className="mt-2">
      <summary className="cursor-pointer">币种明细</summary>
      {(raw.details as Record<string, unknown>[]).map((row, index) => <dl
        key={index} className="mt-2 grid grid-cols-2 gap-2">
        {Object.entries(row).filter(([key]) => LABELS[key]).map(([key, value]) =>
          <div key={key}><dt className="text-[var(--muted)]">{fieldLabel(key, row)}</dt>
            <dd className="break-all">{scalar(value)}</dd></div>)}
      </dl>)}
    </details>}
    {snapshot.hasMore && <p className="mt-2">还有更多记录，请在对话中继续查询。</p>}
    {!!snapshot.coverage.excludedKinds?.length && <p className="mt-2">
      未覆盖：{snapshot.coverage.excludedKinds.join("、")}</p>}
    {snapshot.warnings.map((warning, index) => <p className="mt-1"
      role="status" key={index}>{warning}</p>)}
  </section>;
}
