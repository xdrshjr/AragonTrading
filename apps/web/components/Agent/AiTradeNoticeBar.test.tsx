import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mintAiTradeNotice } from "@/lib/aiTradeNotice";
import type { AiTradeNoticeEpoch, AiTradeNoticeKind } from "@/lib/aiTradeNotice";
import { useAppStore } from "@/lib/store";
import AiTradeNoticeBar from "./AiTradeNoticeBar";

/**
 * 通知条的渲染侧门禁（ai-trade-notice-state-sync §8.2，C-1 ~ C-5 / C-9）。
 *
 * 抽成独立组件的价值在这里兑现：这些用例**直接渲染它**，不必拖起整个 AgentPanel。
 */

function epoch(patch: Partial<AiTradeNoticeEpoch> = {}): AiTradeNoticeEpoch {
  return { groupId: "g1", lifecycle: "paused", resumedCount: 0, reconciledAtMs: 0, ...patch };
}

/** 直接把一条铸好的提示塞进 store（绕开写点，好让用例只考察渲染侧）。 */
function seed(kind: AiTradeNoticeKind, text: string, at: AiTradeNoticeEpoch, ts = Date.now()) {
  const notice = mintAiTradeNotice(kind, text, at, ts);
  useAppStore.setState({ aiTradeNotice: notice });
  return notice!;
}

function seedGroup(state: string, resumedCount: number) {
  useAppStore.setState({
    aiTradeGroup: { groupId: "g1", state, resumedCount, members: {} } as never,
  });
}

describe("AiTradeNoticeBar", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({
      aiTradeNotice: null,
      aiTradeGroup: null,
      aiTradeResumable: null,
      aiTradeReconciledAtMs: 0,
    });
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("C-1：用户报告的那条 —— paused → running 之后整条不在文档里", () => {
    seedGroup("paused", 0);
    seed("paused", "已暂停：不再开新仓，但盯市、保护性平仓与交易所侧兜底核实照常运行。", epoch());
    render(<AiTradeNoticeBar />);
    expect(screen.getByTestId("ai-trade-notice-bar")).toBeInTheDocument();

    // 点【继续】成功之后 `setAiTradeGroup` 把态推到 running 且 resumedCount +1，对账随即落地。
    // **不调 `rerender`**：这一步的重渲染只能来自组件自己的 store 订阅（§4.8-6 的四个订阅源）。
    act(() => {
      seedGroup("running", 1);
      useAppStore.setState({ aiTradeReconciledAtMs: Date.now() + 1_000 });
    });
    expect(screen.queryByTestId("ai-trade-notice-bar")).not.toBeInTheDocument();
  });

  it("C-2：ended 在 aiTradeGroup 为 null 时仍渲染，role=alert 且按钮文案是「知道了」", () => {
    seed("ended", "已结束并归档。以下持仓仍留在交易所上，从此不再由任何自动化管理…", epoch({
      groupId: null,
      lifecycle: "idle",
    }));
    render(<AiTradeNoticeBar />);
    const bar = screen.getByTestId("ai-trade-notice-bar");
    expect(bar).toBeInTheDocument();
    expect(bar).toHaveAttribute("role", "alert");
    // 可访问名以可见文案开头（WCAG 2.5.3 Label in Name）：语音控制用户念屏幕上那三个字要点得动它。
    expect(screen.getByRole("button", { name: "知道了，关闭这条提示" })).toHaveTextContent("知道了");
  });

  it("C-3：resumed 的 12 秒 TTL 真的有一个时钟在走（不是等下一次外部事件）", () => {
    vi.useFakeTimers();
    const base = Date.now();
    seedGroup("running", 1);
    seed("resumed", "已继续：3 个标的重新武装完成…", epoch({ lifecycle: "running", resumedCount: 1 }),
         base);
    render(<AiTradeNoticeBar />);
    expect(screen.getByTestId("ai-trade-notice-bar")).toBeInTheDocument();

    // **只推时间，不动 store 里任何一个字段、也不 rerender**：能消失就只可能是组件自己起了表。
    act(() => {
      vi.setSystemTime(base + 12_500);
      vi.advanceTimersByTime(12_500);
    });
    expect(screen.queryByTestId("ai-trade-notice-bar")).not.toBeInTheDocument();
  });

  it("C-4：长文默认折叠两行，「展开」按钮的 aria-expanded 随点击翻转", () => {
    seedGroup("paused", 0);
    const long = "已暂停：不再开新仓，但盯市、保护性平仓与交易所侧兜底核实照常运行。"
      + " BTC-USDT-SWAP：无持仓。 ETH-USDT-SWAP：无持仓。 SOL-USDT-SWAP：无持仓。"
      + " DOGE-USDT-SWAP：无持仓。 SATS-USDT-SWAP：无持仓。";
    seed("paused", long, epoch());
    render(<AiTradeNoticeBar />);

    const body = screen.getByTitle(long);
    expect(body.className).toContain("line-clamp-2");
    const toggle = screen.getByRole("button", { name: "展开" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "收起" })).toHaveAttribute("aria-expanded", "true");
    // 折叠是纯呈现：全文始终在 DOM 里，展开只是把裁剪去掉。
    expect(screen.getByTitle(long).className).not.toContain("line-clamp-2");
    expect(screen.getByTitle(long)).toHaveTextContent("SATS-USDT-SWAP");
  });

  it("C-5：五种 kind 的分形前缀都落到了 DOM 上（「绝不只靠颜色」不是一句口号）", () => {
    const cases: [AiTradeNoticeKind, string, AiTradeNoticeEpoch][] = [
      ["paused", "‖", epoch()],
      ["resumed", "▶", epoch({ lifecycle: "running", resumedCount: 0 })],
      ["ended", "!", epoch({ groupId: null, lifecycle: "idle" })],
      ["backend_ended", "!", epoch({ groupId: null, lifecycle: "idle" })],
      ["action_unknown", "?", epoch()],
    ];
    for (const [kind, glyph, at] of cases) {
      useAppStore.setState({ aiTradeGroup: null, aiTradeReconciledAtMs: 0 });
      if (at.lifecycle !== "idle") seedGroup(at.lifecycle === "paused" ? "paused" : "running", 0);
      seed(kind, `${kind} 的文案`, at);
      const view = render(<AiTradeNoticeBar />);
      expect(screen.getByTestId("ai-trade-notice-bar")).toHaveTextContent(glyph);
      view.unmount();
    }
  });

  it("C-9：未落地宽限 + 组件确实订阅了 aiTradeReconciledAtMs", () => {
    // ① pause 回执刚落地：本地态还是 running（pauseM 不调 setAiTradeGroup），对账未落地。
    //    照 v1 的谓词，validIn:["paused"] 会当场判死这条刚铸出来的话。
    seedGroup("running", 0);
    const notice = seed("paused", "已暂停，但平仓没有完成：请立即到下方实盘面板手动处理这些持仓。",
                        epoch({ lifecycle: "running" }));
    render(<AiTradeNoticeBar />);
    expect(screen.getByTestId("ai-trade-notice-bar")).toBeInTheDocument();

    // ② **只改 `aiTradeReconciledAtMs` 一个字段、且不 rerender**（RC-3）：这同时是「组件真的
    //    订阅了它」的唯一证明 —— 不订阅的话它根本不会重渲染，这条必红。
    act(() => useAppStore.setState({ aiTradeReconciledAtMs: notice.ts + 1 }));
    expect(screen.queryByTestId("ai-trade-notice-bar")).not.toBeInTheDocument();
  });

  it("手动关闭永远可用（用户能主动收起任何一条）", () => {
    seedGroup("paused", 0);
    seed("paused", "已暂停…", epoch());
    render(<AiTradeNoticeBar />);
    fireEvent.click(screen.getByRole("button", { name: /关闭这条提示/ }));
    expect(useAppStore.getState().aiTradeNotice).toBeNull();
    expect(screen.queryByTestId("ai-trade-notice-bar")).not.toBeInTheDocument();
  });

  it("短文不给「展开」：一个点了什么都不变的按钮比没有按钮更坏（§4.8-2 说的是**长文**折叠）", () => {
    // C-4 的反面。少了这一条，「展开」会挂在每一条一行就说完的提示下面 —— 点它既不换行数也
    // 不换文案，而 `line-clamp-2` 对它本来就不生效。
    seedGroup("paused", 0);
    seed("paused", "已继续：限制已解除。", epoch());
    render(<AiTradeNoticeBar />);
    expect(screen.getByTestId("ai-trade-notice-bar")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "展开" })).not.toBeInTheDocument();
    // 裁剪类也一并不上：不给展开出口就绝不许裁，否则后半句会被无声吞掉。
    expect(screen.getByTitle("已继续：限制已解除。").className).not.toContain("line-clamp-2");
  });

  it("进场动效真的会翻一次（终态写死 = 三个 transition 工具类是摆设）", async () => {
    // §4.8-3 要的是 `opacity-0 translate-y-[-2px]` → `opacity-100 translate-y-0`。类名若直接写死
    // 成终态，浏览器从头到尾只看到一个值，动画整段不播 —— 那是「声明了但从没被走到」的形状。
    seedGroup("paused", 0);
    seed("paused", "已暂停…", epoch());
    render(<AiTradeNoticeBar />);
    expect(screen.getByTestId("ai-trade-notice-bar").className).toContain("opacity-0");
    await waitFor(() =>
      expect(screen.getByTestId("ai-trade-notice-bar").className).toContain("opacity-100"),
    );
  });
});
