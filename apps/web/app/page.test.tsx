import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useAppStore } from "@/lib/store";

// 重型子组件替身只隔离外壳布局，账户行为由组件用例验证。
vi.mock("@/components/Chart/ChartWorkspace", () => ({
  default: () => <div data-testid="chart-workspace">chart</div>,
}));
vi.mock("@/components/Chart/AiBacktestModal", () => ({ default: () => null }));
vi.mock("@/components/Desktop/WindowControls", () => ({
  default: () => <button aria-label="窗口控制" />,
}));
vi.mock("@/components/Header/AccountModeSwitch", () => ({
  default: ({ placement }: { placement?: string }) =>
    <button aria-label="账户模式" data-placement={placement} />,
}));
vi.mock("@/components/Discovery/DiscoveryLab", () => ({
  default: () => <div data-testid="discovery-lab">discovery</div>,
}));
vi.mock("@/components/Factor/FactorLab", () => ({
  default: () => <div data-testid="factor-lab">factor</div>,
}));
vi.mock("@/components/RightPanel/RightPanel", () => ({ default: () => null }));
vi.mock("@/components/Settings/SettingsGearButton", () => ({
  default: () => <button aria-label="设置" />,
}));
vi.mock("@/components/Settings/SettingsPanel", () => ({ default: () => null }));
vi.mock("@/lib/useLiveAutoGroupStream", () => ({ useLiveAutoGroupStream: () => undefined }));
vi.mock("@/lib/useAiTradeGroupStream", () => ({ useAiTradeGroupStream: () => undefined }));
vi.mock("@/lib/useWorkspaceSync", () => ({ useWorkspaceSync: () => undefined }));
vi.mock("@/lib/liveAutoApi", () => ({
  // 替身必须**真的返回 Promise**：外壳的孤儿会话重发现 effect 直接 `.then(...)` 它。
  liveAutoApi: {
    status: vi.fn(async () => null),
    groupStatus: vi.fn(async () => null),
    snapshot: vi.fn(async () => null),
  },
  liveAutoWS: { connect: vi.fn(() => ({ close: vi.fn() })) },
}));

const health = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    health: (...args: unknown[]) => health(...args),
    okxConnectivity: vi.fn(async () => null),
    okxProbe: vi.fn(async () => null),
    candles: vi.fn(async () => []),
    candlesWithMeta: vi.fn(async () => ({ candles: [], source: "live" })),
    run: vi.fn(async () => null),
    equity: vi.fn(async () => []),
    trades: vi.fn(async () => []),
  },
  ApiRequestError: class extends Error {},
  toApiError: (e: unknown) => e,
}));

import Home from "./page";

function renderHome() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Home />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  health.mockReset();
  useAppStore.setState({ activeView: "trade", showSettings: false });
});
afterEach(cleanup);

it("全局操作只在左栏，顶栏保留窗口控制", () => {
  health.mockResolvedValue({ status: "ok", okx: "up" });
  const { container } = renderHome();
  const header = container.querySelector("header")!;
  expect(header.contains(screen.getByRole("button", { name: "窗口控制" }))).toBe(true);
  const nav = screen.getByRole("navigation");
  for (const name of ["账户模式", "设置", /切换到.*主题/]) {
    const button = screen.getByRole("button", { name });
    expect(nav.contains(button)).toBe(true);
    expect(header.contains(button)).toBe(false);
  }
  expect(screen.getByRole("button", { name: "账户模式" })).toHaveAttribute(
    "data-placement", "rail",
  );
});
