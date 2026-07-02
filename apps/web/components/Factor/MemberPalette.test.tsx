import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { addMembersCapped, libraryItemToMember, toggleMember } from "@/lib/factor";
import { factorApi } from "@/lib/factorApi";
import type { FactorLibraryItem, FactorMember, FrozenDirection } from "@/lib/factorTypes";
import MemberPalette from "./MemberPalette";

vi.mock("@/lib/factorApi", () => ({
  factorApi: { list: vi.fn() },
}));

const LIBRARY: FactorLibraryItem[] = [
  { key: "mom_roc", cn: "动量ROC", category: "momentum", directionHint: "long", paramsSchema: {}, intradayOnly: false },
  { key: "ema_slope", cn: "EMA斜率", category: "trend", directionHint: "long", paramsSchema: {}, intradayOnly: false },
  { key: "rev_rsi", cn: "RSI反转", category: "reversal", directionHint: "short", paramsSchema: {}, intradayOnly: true },
];

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

type Handlers = Pick<
  React.ComponentProps<typeof MemberPalette>,
  "onToggle" | "onAddMany" | "onClear" | "onRemove" | "onDirection"
>;

function renderPalette(over: Partial<React.ComponentProps<typeof MemberPalette>> & { members: FactorMember[] }) {
  const handlers: Handlers = {
    onToggle: vi.fn(),
    onAddMany: vi.fn(),
    onClear: vi.fn(),
    onRemove: vi.fn(),
    onDirection: vi.fn(),
  };
  render(
    <QueryClientProvider client={makeClient()}>
      <MemberPalette library={LIBRARY} {...handlers} {...over} />
    </QueryClientProvider>,
  );
  return handlers;
}

// 受控宿主：用真实纯函数把回调接回 members，复刻 FactorSetBuilder 的装配以做集成断言。
function Harness({ initial }: { initial?: FactorMember[] }) {
  const [client] = useState(makeClient);
  const [members, setMembers] = useState<FactorMember[]>(initial ?? []);
  return (
    <QueryClientProvider client={client}>
      <MemberPalette
        library={LIBRARY}
        members={members}
        maxMembers={12}
        onToggle={(m) => setMembers((prev) => toggleMember(prev, m, 12).next)}
        onAddMany={(cs) => setMembers((prev) => addMembersCapped(prev, cs, 12).next)}
        onClear={() => setMembers([])}
        onRemove={(i) => setMembers((prev) => prev.filter((_, idx) => idx !== i))}
        onDirection={(i, d: FrozenDirection) =>
          setMembers((prev) => prev.map((m, idx) => (idx === i ? { ...m, direction: d } : m)))
        }
      />
    </QueryClientProvider>
  );
}

describe("MemberPalette", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (factorApi.list as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  });
  afterEach(cleanup);

  it("renders library candidates and toggles one on click (no dropdown, single click)", () => {
    const { onToggle } = renderPalette({ members: [] });
    const chip = screen.getByRole("button", { name: /动量ROC/ });
    fireEvent.click(chip);
    expect(onToggle).toHaveBeenCalledWith(
      expect.objectContaining({ factorKey: "mom_roc", direction: "long", weight: 0 }),
    );
  });

  it("reflects selected state via aria-pressed and survives a basket direction flip (R1)", () => {
    render(<Harness initial={[{ factorKey: "mom_roc", params: {}, weight: 0, direction: "long" }]} />);
    expect(screen.getByRole("button", { name: /动量ROC/ }).getAttribute("aria-pressed")).toBe("true");

    fireEvent.change(screen.getByDisplayValue("看多"), { target: { value: "short" } });
    // 翻转方向后候选卡仍判定为已选（candidateIdentity 忽略 direction）。
    expect(screen.getByRole("button", { name: /动量ROC/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("toggles a candidate off when its already-selected chip is clicked again", () => {
    render(<Harness initial={[libraryItemToMember(LIBRARY[0])]} />);
    const chip = () => screen.getByRole("button", { name: /动量ROC/ });
    expect(chip().getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(chip());
    expect(chip().getAttribute("aria-pressed")).toBe("false");
  });

  it("filters candidates by search and shows the empty hint when nothing matches", async () => {
    renderPalette({ members: [] });
    expect(screen.getByRole("button", { name: /动量ROC/ })).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText(/搜索/), { target: { value: "RSI" } });
    await waitFor(() => expect(screen.queryByRole("button", { name: /动量ROC/ })).toBeNull());
    expect(screen.getByRole("button", { name: /RSI反转/ })).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText(/搜索/), { target: { value: "zzz" } });
    await waitFor(() => expect(screen.getByText(/无匹配候选/)).toBeTruthy());
  });

  it("'select all current results' passes the visible candidates to onAddMany", () => {
    const { onAddMany } = renderPalette({ members: [] });
    fireEvent.click(screen.getByRole("button", { name: /全选当前结果/ }));
    expect(onAddMany).toHaveBeenCalledTimes(1);
    expect((onAddMany as ReturnType<typeof vi.fn>).mock.calls[0][0]).toHaveLength(LIBRARY.length);
  });

  it("disables unselected candidates once the member cap is reached", () => {
    const members = [libraryItemToMember(LIBRARY[0]), libraryItemToMember(LIBRARY[1])];
    renderPalette({ members, maxMembers: 2 });
    expect((screen.getByRole("button", { name: /RSI反转/ }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: /动量ROC/ }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("wires clear / remove / direction on the selected basket", () => {
    const members = [libraryItemToMember(LIBRARY[0])];
    const { onClear, onRemove, onDirection } = renderPalette({ members });
    fireEvent.click(screen.getByRole("button", { name: "清空已选" }));
    expect(onClear).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "移除" }));
    expect(onRemove).toHaveBeenCalledWith(0);
    fireEvent.change(screen.getByDisplayValue("看多"), { target: { value: "short" } });
    expect(onDirection).toHaveBeenCalledWith(0, "short");
  });

  it("shows an error + retry when 'my factors' fails to load", async () => {
    (factorApi.list as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("boom"));
    renderPalette({ members: [] });
    fireEvent.click(screen.getByRole("button", { name: "我的因子" }));
    await waitFor(() => expect(screen.getByText(/加载因子失败/)).toBeTruthy());
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
  });

  it("guides the user when there are no enabled factors", async () => {
    (factorApi.list as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    renderPalette({ members: [] });
    fireEvent.click(screen.getByRole("button", { name: "我的因子" }));
    await waitFor(() => expect(screen.getByText(/暂无激活因子/)).toBeTruthy());
  });

  it("renders 探索训练 and fires onExplore when there are ≥2 members (factor-combo-exploration §2.7)", () => {
    const onExplore = vi.fn();
    const members = [libraryItemToMember(LIBRARY[0]), libraryItemToMember(LIBRARY[1])];
    renderPalette({ members, onExplore });
    const btn = screen.getByRole("button", { name: "探索训练" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    expect(onExplore).toHaveBeenCalledTimes(1);
  });

  it("disables 探索训练 when fewer than 2 members", () => {
    renderPalette({ members: [libraryItemToMember(LIBRARY[0])], onExplore: vi.fn() });
    expect((screen.getByRole("button", { name: "探索训练" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("omits 探索训练 entirely when onExplore is not provided", () => {
    renderPalette({ members: [libraryItemToMember(LIBRARY[0]), libraryItemToMember(LIBRARY[1])] });
    expect(screen.queryByRole("button", { name: "探索训练" })).toBeNull();
  });
});
