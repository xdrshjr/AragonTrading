import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useAppStore } from "@/lib/store";
import NavRailControls from "./NavRailControls";

vi.mock("@/lib/accountApi", () => ({ accountApi: {
  accounts: { list: vi.fn().mockResolvedValue({ accounts: [], defaultId: null }) },
} }));
afterEach(cleanup);

it("按账户、主题、设置排列；主题与设置往返保留主视图", () => {
  useAppStore.setState({ theme: "light", accountMode: "sim",
    activeView: "factor", showSettings: false });
  render(<QueryClientProvider client={new QueryClient()}>
    <NavRailControls />
  </QueryClientProvider>);
  const group = screen.getByRole("group", { name: "工作台设置" });
  expect(within(group).getAllByRole("button").map(
    (button) => button.getAttribute("aria-label"),
  )).toEqual(["账户模式：模拟盘", "切换到暗色主题", "设置"]);
  fireEvent.click(screen.getByRole("button", { name: "切换到暗色主题" }));
  expect(useAppStore.getState().theme).toBe("dark");
  fireEvent.click(screen.getByRole("button", { name: "切换到亮色主题" }));
  expect(useAppStore.getState().theme).toBe("light");
  const settings = screen.getByRole("button", { name: "设置" });
  fireEvent.click(settings);
  expect(settings).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(settings);
  expect(useAppStore.getState().showSettings).toBe(false);
  expect(useAppStore.getState().activeView).toBe("factor");
  expect(useAppStore.getState().accountMode).toBe("sim");
});
