// 轮动参数的共享文案常量（R2）：自 ComboView 抽出，供内联 PolicyEditor 与 ComboDetailModal
// 同源消费，杜绝复制粘贴与文案漂移。仅静态文案，不含决策逻辑（决策真源恒在后端，C6）。

// 每项轮动参数的释义（解释「为什么」：抗过拟合 / 抗未来函数 / 抗频繁切换）。
export const POLICY_HINTS: Record<string, string> = {
  rebalanceBars: "每隔多少根重新决策一次活跃成员。间隔越大切换越少、越稳健。",
  lookbackBars: "评估成员近端表现的回看窗口。宜覆盖多个再平衡周期，过短易过拟合。",
  selectionMetric: "在候选成员中选优的指标：收益率 / 夏普(风险调整) / 卡玛(收益/回撤)。",
  minDwellBars: "选中成员的最短持有根数，抑制频繁来回切换(whipsaw)。",
  switchMarginPct: "切换迟滞：挑战者须超出在任者该相对优势才替换，过滤噪声切换。",
  regimeGating: "仅在与当前体制匹配的候选成员内选优(注入领域先验，抗过拟合)。",
  turnoverBps: "每次切换扣的换手成本(基点)，近似建仓代价，避免高估收益。",
};

// 轮动字段中文标签（镜像后端 regime_rotation 模板 params_schema.label）。
// 详情弹窗优先取各 Strategy 自带的 paramsSchema.label，缺失时回退本表（不留空白标签）。
export const POLICY_LABELS: Record<string, string> = {
  rebalanceBars: "再平衡间隔(根)",
  lookbackBars: "表现回看(根)",
  selectionMetric: "选优指标",
  minDwellBars: "最短持有(根)",
  switchMarginPct: "切换迟滞%",
  regimeGating: "按体制限定候选",
  turnoverBps: "换手成本(bps)",
};

// 轮动参数展示顺序（与 POLICY_LABELS 对齐，详情弹窗按此遍历）。
export const POLICY_FIELD_ORDER = [
  "rebalanceBars",
  "lookbackBars",
  "selectionMetric",
  "minDwellBars",
  "switchMarginPct",
  "regimeGating",
  "turnoverBps",
] as const;

// 选优指标 key → 中文短标签（概览 / 摘要文案复用）。
export const SELECTION_METRIC_LABELS: Record<string, string> = {
  return: "收益率",
  sharpe: "夏普",
  calmar: "卡玛",
};
