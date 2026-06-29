import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { accountApi } from "@/lib/accountApi";
import { useAppStore } from "@/lib/store";
import AccountModeSwitch from "./AccountModeSwitch";

// 仅桩掉本测试不关心的网络副作用：账户列表查询是被测三态的输入；模拟盘资金字段无关。
vi.mock("@/lib/accountApi", () => ({ accountApi: { accounts: { list: vi.fn() } } }));
vi.mock("./SimBalanceField", () => ({ default: () => null }));

const READY = { accounts: [{ id: 1, hasSecret: true, hasPassphrase: true }], defaultId: 1 };
const NO_DEFAULT = { accounts: [], defaultId: null };
const MISSING_CREDS = { accounts: [{ id: 1, hasSecret: false, hasPassphrase: true }], defaultId: 1 };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function renderSwitch() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={qc}>
      <AccountModeSwitch />
    </QueryClientProvider>,
  );
  return { qc, ...utils };
}

const liveButton = () => screen.getByRole("radio", { name: /实盘/ });
const simButton = () => screen.getByRole("radio", { name: "模拟盘" });
const queryStatus = (qc: QueryClient) => qc.getQueryState(["okx", "accounts"])?.status;

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({
    accountMode: "sim",
    mode: "paper",
    showSettings: false,
    settingsSection: "ai-models",
  });
});
afterEach(cleanup);

describe("AccountModeSwitch — 实盘 switch gating across the three account-query states", () => {
  it("state ④ ready: switches to live when a ready default account exists", async () => {
    vi.mocked(accountApi.accounts.list).mockResolvedValue(READY as never);
    const { qc } = renderSwitch();
    await waitFor(() => expect(queryStatus(qc)).toBe("success"));

    fireEvent.click(liveButton());

    expect(useAppStore.getState().accountMode).toBe("live");
    expect(useAppStore.getState().showSettings).toBe(false);
  });

  it("state ③ no-default: does not switch, opens settings with a visible reason", async () => {
    vi.mocked(accountApi.accounts.list).mockResolvedValue(NO_DEFAULT as never);
    const { qc } = renderSwitch();
    await waitFor(() => expect(queryStatus(qc)).toBe("success"));

    fireEvent.click(liveButton());

    expect(useAppStore.getState().accountMode).toBe("sim");
    expect(useAppStore.getState().showSettings).toBe(true);
    expect(useAppStore.getState().settingsSection).toBe("okx-accounts");
    expect(screen.getByText("请先在设置中配置默认实盘账户。")).toBeTruthy();
  });

  it("state ③ missing-creds: shows the credentials-incomplete reason, not the no-account one", async () => {
    vi.mocked(accountApi.accounts.list).mockResolvedValue(MISSING_CREDS as never);
    const { qc } = renderSwitch();
    await waitFor(() => expect(queryStatus(qc)).toBe("success"));

    fireEvent.click(liveButton());

    expect(useAppStore.getState().accountMode).toBe("sim");
    expect(screen.getByText(/默认账户凭证不全/)).toBeTruthy();
  });

  it("state ① pending: does NOT enter live nor open settings, shows a checking hint", () => {
    vi.mocked(accountApi.accounts.list).mockReturnValue(deferred<typeof READY>().promise as never);
    renderSwitch();

    fireEvent.click(liveButton());

    expect(useAppStore.getState().accountMode).toBe("sim");
    expect(useAppStore.getState().showSettings).toBe(false);
    expect(screen.getByText(/正在校验账户/)).toBeTruthy();
  });

  it("state ② error: shows an in-place error and retries (refetch) instead of opening settings", async () => {
    vi.mocked(accountApi.accounts.list).mockRejectedValue(new Error("backend down"));
    const { qc } = renderSwitch();
    await waitFor(() => expect(queryStatus(qc)).toBe("error"));
    // 等组件提交到 error 渲染（tooltip 是已提交渲染的可靠信号），再点击，避免读到 pending 时的旧闭包。
    await waitFor(() => expect(liveButton().getAttribute("title")).toMatch(/账户校验失败/));
    expect(accountApi.accounts.list).toHaveBeenCalledTimes(1);

    fireEvent.click(liveButton());

    expect(useAppStore.getState().accountMode).toBe("sim");
    expect(useAppStore.getState().showSettings).toBe(false);
    expect(screen.getByText(/账户校验失败/)).toBeTruthy();
    await waitFor(() => expect(accountApi.accounts.list).toHaveBeenCalledTimes(2));
  });

  it("race: a click during pending never enters live; after success lands the next click switches", async () => {
    const d = deferred<typeof READY>();
    vi.mocked(accountApi.accounts.list).mockReturnValue(d.promise as never);
    const { qc } = renderSwitch();

    fireEvent.click(liveButton());
    expect(useAppStore.getState().accountMode).toBe("sim");
    expect(screen.getByText(/正在校验账户/)).toBeTruthy();

    await act(async () => {
      d.resolve(READY);
      await d.promise;
    });
    await waitFor(() => expect(queryStatus(qc)).toBe("success"));
    await waitFor(() => expect(screen.queryByText(/正在校验账户/)).toBeNull());

    fireEvent.click(liveButton());
    expect(useAppStore.getState().accountMode).toBe("live");
  });

  it("模拟盘 always switches back to sim and restores paper mode", async () => {
    vi.mocked(accountApi.accounts.list).mockResolvedValue(READY as never);
    const { qc } = renderSwitch();
    await waitFor(() => expect(queryStatus(qc)).toBe("success"));

    fireEvent.click(liveButton());
    expect(useAppStore.getState().accountMode).toBe("live");

    fireEvent.click(simButton());
    expect(useAppStore.getState().accountMode).toBe("sim");
    expect(useAppStore.getState().mode).toBe("paper");
  });
});
