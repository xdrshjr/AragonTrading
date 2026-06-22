// 设置中心 · Agent 工具管理的纯函数（按分类分组、启用计数），便于单测与组件复用（spec §2.8）。
import { CATEGORY_ORDER } from "./agentToolMeta";
import type { AgentTool, AgentToolCategory } from "./agentTypes";

/** 按 CATEGORY_ORDER 输出非空分组，组内保持后端返回顺序（未知分类回落 "general"）。 */
export function groupToolsByCategory(
  tools: AgentTool[],
): { category: AgentToolCategory; tools: AgentTool[] }[] {
  const buckets = new Map<AgentToolCategory, AgentTool[]>();
  for (const tool of tools) {
    const category: AgentToolCategory = CATEGORY_ORDER.includes(tool.category)
      ? tool.category
      : "general";
    const bucket = buckets.get(category);
    if (bucket) bucket.push(tool);
    else buckets.set(category, [tool]);
  }
  return CATEGORY_ORDER.filter((category) => buckets.has(category)).map((category) => ({
    category,
    tools: buckets.get(category)!,
  }));
}

/** 启用计数概览（前端兜底重算，与后端 enabledCount 一致）。 */
export function enabledSummary(tools: AgentTool[]): { enabled: number; total: number } {
  return {
    enabled: tools.reduce((acc, tool) => acc + (tool.enabled ? 1 : 0), 0),
    total: tools.length,
  };
}
