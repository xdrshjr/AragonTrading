// 未结委托纯计算（无 IO，可测）：查询键工厂 / 分类 / 分组 / 颜色映射 / 涨跌幅 / 图表价格线构造。
// 与后端 fetch_live_open_orders 归一形态对齐；图表与持仓子行共用，便于单测与组件复用（spec §2.3b）。
import type { LiveOpenOrder, LivePosition, OpenOrderKind } from "./tradingTypes";

// 唯一查询键工厂（C2）：图表与持仓表必须经此取键，杜绝任一方硬编码 null 致键分裂为两次轮询。
// undefined / null 归一为同键，保证 React Query 去重命中。
export function openOrdersKey(accountId?: number) {
  return ["trading", "openOrders", accountId ?? null] as const;
}

// 持仓相关委托（持仓子行展示）：过滤掉 limit_open 开仓挂单（开仓挂单不属于任何持仓）。
const POSITION_KINDS: readonly OpenOrderKind[] = ["limit_close", "tp", "sl"];
export function isPositionOrder(kind: OpenOrderKind): boolean {
  return POSITION_KINDS.includes(kind);
}

// 图表价格线委托（F-6）：在持仓相关委托之上纳入 limit_open——AI 挂出的开仓限价单在成交前
// 没有对应持仓，但其价位线是「挂单可见」的核心诉求；持仓子行语义不变（仍走 POSITION_KINDS）。
export const CHART_LINE_KINDS: readonly OpenOrderKind[] = ["limit_close", "tp", "sl", "limit_open"];
export function isChartLineOrder(kind: OpenOrderKind): boolean {
  return CHART_LINE_KINDS.includes(kind);
}

// kind → 委托列表页签的中文标签（F-5）。不复用 orderKindMeta.label：那套是图表线短标签
// （挂单/平仓/止盈/止损），语境不同，改它会波及既有图表 / 持仓子行文案。
export function openOrderKindLabel(kind: OpenOrderKind): string {
  switch (kind) {
    case "limit_open":
      return "限价开仓";
    case "limit_close":
      return "限价平仓";
    case "tp":
      return "止盈委托";
    case "sl":
      return "止损委托";
    default:
      return kind;
  }
}

// 持仓 ↔ 委托关联键：(instId, posSide)；net 模式 posSide 统一为 "net"。
export function positionKey(instId: string, posSide: string): string {
  return `${instId}::${posSide}`;
}

// 按 (instId, posSide) 把持仓相关委托归到对应持仓；仅保留命中当前持仓的委托（无对应持仓的委托不展示）。
export function groupOrdersByPosition(
  orders: LiveOpenOrder[],
  positions: LivePosition[],
): Map<string, LiveOpenOrder[]> {
  const valid = new Set(positions.map((p) => positionKey(p.instId, p.posSide)));
  const map = new Map<string, LiveOpenOrder[]>();
  for (const order of orders) {
    if (!isPositionOrder(order.kind)) continue;
    const key = positionKey(order.instId, order.posSide);
    if (!valid.has(key)) continue;
    const list = map.get(key);
    if (list) list.push(order);
    else map.set(key, [order]);
  }
  return map;
}

export interface OrderKindMeta {
  label: string; // 平仓 | 止盈 | 止损 | 挂单
  colorVar: string; // CSS 主题变量名（图表层 resolve 为真实色，组件层 var(...) 引用）
  lineStyle: number; // lightweight-charts LineStyle：2 虚线 / 3 长虚线
}

// kind → 展示元数据（颜色互不相同，满足「不同色彩虚线」）。
// limit_open 用既有 --muted（评审 R-3：--accent-2 在 globals.css 明暗两套主题均不存在，指名会写出
// 解析失败的 var()）——与 tp --long / sl --short / limit_close --accent 三色互异，label + 虚线再区分。
export function orderKindMeta(kind: OpenOrderKind): OrderKindMeta {
  switch (kind) {
    case "tp":
      return { label: "止盈", colorVar: "--long", lineStyle: 3 };
    case "sl":
      return { label: "止损", colorVar: "--short", lineStyle: 3 };
    case "limit_close":
      return { label: "平仓", colorVar: "--accent", lineStyle: 2 };
    case "limit_open":
      return { label: "开仓挂单", colorVar: "--muted", lineStyle: 2 };
    default:
      return { label: "挂单", colorVar: "--muted", lineStyle: 2 };
  }
}

// 相对参考价的涨跌幅：refPx>0 才有意义，否则 null（参考价由调用方注入）。
export function pctChange(price: number, refPx: number | null | undefined): number | null {
  return refPx != null && refPx > 0 ? (price - refPx) / refPx : null;
}

// 图表价格线视图模型：reconcile 主键 id 用 `${ordId ?? algoId}:${kind}`，OCO 的 tp/sl 同 algoId 不撞。
export interface OrderPriceLine {
  id: string;
  price: number; // triggerPx ?? px
  kind: OpenOrderKind;
  colorVar: string;
  lineStyle: number;
  label: string;
  changePct: number | null; // 相对 refPx（图表线取 ChartWorkspace markPrice）
}

// 构造当前 instId 的图表价格线：仅取该标的的图表线委托（F-6 起含 limit_open），price = triggerPx ?? px。
export function buildOrderPriceLines(
  orders: LiveOpenOrder[],
  instId: string | undefined,
  refPx: number | null,
): OrderPriceLine[] {
  const lines: OrderPriceLine[] = [];
  for (const order of orders) {
    if (!isChartLineOrder(order.kind)) continue;
    if (instId && order.instId !== instId) continue;
    const price = order.triggerPx ?? order.px;
    if (price == null) continue;
    const meta = orderKindMeta(order.kind);
    lines.push({
      id: `${order.ordId ?? order.algoId}:${order.kind}`,
      price,
      kind: order.kind,
      colorVar: meta.colorVar,
      lineStyle: meta.lineStyle,
      label: meta.label,
      changePct: pctChange(price, refPx),
    });
  }
  return lines;
}
