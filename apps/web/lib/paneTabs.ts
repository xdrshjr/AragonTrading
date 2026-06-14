// 副图页签当前选中态派生（纯逻辑 + 同名 paneTabs.test.ts，约定同 ohlc.ts / position.ts）。
// 不引入 React / DOM，可在 jsdom 外纯逻辑单测（spec chart-indicator-pane-tabs §3.1 / §5.3）。
import type { IndicatorKey } from "./indicators";

/**
 * 由「启用的副图指标键（注册表顺序）」与「用户上次选中的页签」派生当前应显示的副图键。
 * @param enabled 启用的副图指标键，按注册表顺序（决定回落时取哪个为首个）
 * @param paneTab 用户上次显式选中的页签；可能已被关闭或为 null
 * @returns 当前应显示的副图键；无启用项时为 null
 */
export function selectActivePane(
  enabled: IndicatorKey[],
  paneTab: IndicatorKey | null,
): IndicatorKey | null {
  if (enabled.length === 0) return null;
  if (paneTab !== null && enabled.includes(paneTab)) return paneTab;
  return enabled[0];
}
