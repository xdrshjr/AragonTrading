// Agent 工具展示层单一真源：工具名 → 友好中文名 + 图标，分类 key → 中文标题 / 描述与顺序。
// 后端只回 name/description/category(key)/isUiAction/paramsSchema/enabled；中文 chrome 集中于此，
// 消除 ToolCallCard 与设置面板两处漂移（spec §2.6）。未知 name 回落原名 + 🔧。
import type { AgentToolCategory } from "./agentTypes";

// 工具名 → 友好中文标签 + 图标（缺省回落原 name）。
export const TOOL_LABELS: Record<string, { label: string; icon: string }> = {
  list_instruments: { label: "查询标的列表", icon: "📋" },
  get_market_summary: { label: "行情快照", icon: "📈" },
  get_candles: { label: "拉取 K 线", icon: "🕯️" },
  show_chart: { label: "切换主图", icon: "🖼️" },
  list_strategies: { label: "策略列表", icon: "🧩" },
  run_backtest: { label: "运行回测", icon: "⚙️" },
  compare_strategies: { label: "策略对比", icon: "⚖️" },
  label_market_regime: { label: "市场体制分析", icon: "📍" },
  test_signal_forward_returns: { label: "信号前瞻收益", icon: "🔬" },
  list_runs: { label: "运行历史", icon: "🗂️" },
  get_run_metrics: { label: "绩效指标", icon: "📊" },
  get_run_trades: { label: "成交明细", icon: "🧾" },
  get_run_equity: { label: "净值曲线", icon: "💹" },
};

export function labelFor(name: string): { label: string; icon: string } {
  return TOOL_LABELS[name] ?? { label: name, icon: "🔧" };
}

// 分类 key → 中文标题 + 一句话描述（与后端 Tool.category 枚举对齐）。
export const TOOL_CATEGORIES: Record<AgentToolCategory, { title: string; desc: string }> = {
  market: { title: "行情数据", desc: "查询标的、快照与 K 线等实时行情" },
  ui: { title: "界面控制", desc: "驱动左侧主图等工作台界面动作" },
  strategy: { title: "策略与回测", desc: "列出策略、运行回测与横向对比" },
  analysis: { title: "智能分析", desc: "市场体制识别与信号前瞻收益统计" },
  runs: { title: "运行历史", desc: "查询历史 run 的绩效、成交与净值" },
  general: { title: "其他工具", desc: "未归类的通用工具" },
};

// 分组展示顺序（界面自上而下）。
export const CATEGORY_ORDER: AgentToolCategory[] = [
  "market",
  "ui",
  "strategy",
  "analysis",
  "runs",
  "general",
];
