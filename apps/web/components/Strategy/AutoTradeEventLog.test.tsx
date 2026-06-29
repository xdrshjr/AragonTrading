import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { LiveAutoEvent } from "@/lib/liveAutoTypes";
import { useAppStore } from "@/lib/store";
import AutoTradeEventLog from "./AutoTradeEventLog";

function setEvents(events: LiveAutoEvent[]) {
  useAppStore.setState({ liveAutoEvents: events });
}

beforeEach(() => {
  useAppStore.setState({ liveAutoSessionId: "liveauto_x", liveAutoEvents: [] });
});
afterEach(cleanup);

describe("AutoTradeEventLog (hardening H6 / R-8)", () => {
  it("renders nothing when there are no events", () => {
    const { container } = render(<AutoTradeEventLog />);
    expect(container.firstChild).toBeNull();
  });

  it("surfaces an error event as a red alert pinned to the top", () => {
    setEvents([
      { ts: 1, level: "info", message: "已开多仓" },
      { ts: 2, level: "error", code: "51008", message: "下单失败：余额不足" },
    ]);
    render(<AutoTradeEventLog />);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toMatch(/下单失败/);
    // 错误置顶：事件流第一个条目即错误条目（error 项 role=alert 覆盖了隐式 listitem）。
    const list = screen.getByRole("list");
    expect(list.firstElementChild?.textContent).toMatch(/下单失败/);
  });

  it("renders warn (degraded) and info messages at their own level tone", () => {
    setEvents([
      { ts: 1, level: "warn", message: "行情连接异常，自动交易暂停响应 K 线" },
      { ts: 2, level: "info", message: "OCO 已触发平仓" },
    ]);
    render(<AutoTradeEventLog />);
    const warn = screen.getByText("行情连接异常，自动交易暂停响应 K 线");
    const info = screen.getByText("OCO 已触发平仓");
    expect(warn.className).toMatch(/--accent/);
    expect(info.className).toMatch(/--muted/);
    // 非错误条目不带 alert role。
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("ring buffer trims to 50 newest events (oldest dropped)", () => {
    const push = useAppStore.getState().pushLiveAutoEvent;
    for (let i = 0; i < 60; i++) {
      push({ ts: i, level: "info", message: `事件 ${i}` });
    }
    const events = useAppStore.getState().liveAutoEvents;
    expect(events.length).toBe(50);
    expect(events[0].message).toBe("事件 10"); // 最旧 0..9 被裁
    expect(events[events.length - 1].message).toBe("事件 59");
  });
});
