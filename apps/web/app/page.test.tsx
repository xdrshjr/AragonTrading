import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "@/lib/store";

/**
 * 工作台外壳的门控水合守卫（ai-trade-audit-journal §2.6.1 / 评审 RP1-7）。
 *
 * 被守的那条路径完全现实：**用户上次停在交易日志 → 管理员把 `AI_TRADE_JOURNAL_UI_ENABLED`
 * 关掉 → 重启**。`store.ts` 的 `partialize` 持久化 `activeView`，而门控关闭时 NavRail 不渲染
 * 该项、`page.tsx` 也不渲染该视图——于是老用户开屏看到的是**一屏什么都没有、也不说为什么的
 * 空白工作台**。
 *
 * 判据必须落在**渲染出来的东西**上：只断言 store 里的值会漏掉「主区是不是真的非空」这一半。
 *
 * 重型子组件全部替身：本文件测的是外壳的一条 effect，不是图表 / 因子实验室 / 挖掘工作区。
 */
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
vi.mock("@/components/Journal/JournalView", () => ({
  default: () => <div data-testid="journal-view">journal</div>,
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

describe("工作台外壳 — 交易日志的门控水合守卫", () => {
  it("门控关闭 + 持久化态为 journal ⇒ 落回 trade 且主区非空（不是空白页）", async () => {
    health.mockResolvedValue({ status: "ok", okx: "up", okxLatencyMs: 1, lastOkTs: 1,
      wsConfigured: true, journalEnabled: false });
    useAppStore.setState({ activeView: "journal" });
    renderHome();
    await waitFor(() => expect(useAppStore.getState().activeView).toBe("trade"));
    // 主区必须真的有东西——只断言 store 的值会漏掉「空白页」这一半。
    expect(screen.getByTestId("chart-workspace")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "交易日志" })).toBeNull();
  });

  it("门控开启 ⇒ 停在 journal 且视图被挂载", async () => {
    health.mockResolvedValue({ status: "ok", okx: "up", okxLatencyMs: 1, lastOkTs: 1,
      wsConfigured: true, journalEnabled: true });
    useAppStore.setState({ activeView: "journal" });
    renderHome();
    await waitFor(() => expect(screen.getByTestId("journal-view")).toBeTruthy());
    expect(useAppStore.getState().activeView).toBe("journal");
    expect(screen.getByRole("button", { name: "交易日志" })).toBeTruthy();
  });

  it("老后端不带 journalEnabled 键 ⇒ 按开着处理，绝不把用户踢出当前视图", async () => {
    // 一个短暂多出来的入口，好过一个在慢网 / 老后端下永远不出现的功能。
    health.mockResolvedValue({ status: "ok", okx: "up", okxLatencyMs: 1, lastOkTs: 1,
      wsConfigured: true });
    useAppStore.setState({ activeView: "journal" });
    renderHome();
    await waitFor(() => expect(screen.getByTestId("journal-view")).toBeTruthy());
    expect(useAppStore.getState().activeView).toBe("journal");
  });

  it("门控关闭但用户本来就在交易视图 ⇒ 不做任何多余的视图切换", async () => {
    health.mockResolvedValue({ status: "ok", okx: "up", okxLatencyMs: 1, lastOkTs: 1,
      wsConfigured: true, journalEnabled: false });
    useAppStore.setState({ activeView: "factor" });
    renderHome();
    await waitFor(() => expect(screen.getByTestId("factor-lab")).toBeTruthy());
    expect(useAppStore.getState().activeView).toBe("factor");
  });
});

it("全局操作只在左栏，顶栏保留窗口控制", () => {
  health.mockResolvedValue({ journalEnabled: true });
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
