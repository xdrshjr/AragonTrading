import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  AiTradeTrajectoryReceipt,
  AiTradeTrajectoryState,
} from "@/lib/trajectoryTypes";
import AiTradeTrajectoryStatus from "./AiTradeTrajectoryStatus";

/**
 * 运行态「后训练数据采集」状态条（ai-trade-trajectory-capture §6.2 F-3）。
 *
 * 渲染与否由 `AiTradeToggle` 按 `requested` 判（F-7 守那一条）；本文件守条本身：六个状态的
 * 语气与文案、单行不折行契约、打开失败时照样把原因说出来（【v2 · RV2】）、桌面端按钮。
 */

function receipt(over: Partial<AiTradeTrajectoryReceipt> = {}): AiTradeTrajectoryReceipt {
  return {
    enabled: true,
    requested: true,
    source: "request",
    state: "recording",
    root: "D:\\data",
    directory: "D:\\data\\20260910-aitradegrp_1c8f",
    isDefaultRoot: false,
    counters: {
      records: 1204, llmCalls: 388, rounds: 130, bytesWritten: 12_345_678, segments: 1,
      dropped: 0, writeErrors: 2, serializeErrors: 0, redactions: 0, lateCalls: 0,
      manifestWriteErrors: 0,
    },
    pendingBytes: 0,
    freeBytes: 128_400_000_000,
    lastError: { code: "WRITE_FAILED", message: "写入目录已被移除", ts: 1_755_500_000 },
    schemaVersion: 1,
    ...over,
  };
}

function setBridge(bridge: Record<string, unknown> | undefined) {
  (window as unknown as { aragon?: unknown }).aragon = bridge;
}

afterEach(() => {
  cleanup();
  setBridge(undefined);
});

const ROWS: { state: AiTradeTrajectoryState; paused: boolean; tone: string; text: string }[] = [
  { state: "recording", paused: false, tone: "accent", text: "数据采集中 · 1204 条 · 11.8 MB" },
  { state: "recording", paused: true, tone: "muted", text: "数据采集待命（交易已暂停）· 1204 条" },
  {
    state: "degraded", paused: false, tone: "warn",
    text: "数据采集有写入失败（2 次），仍在继续 · 交易不受影响",
  },
  {
    state: "paused_low_disk", paused: false, tone: "warn",
    text: "数据采集已暂停：可用空间低于下限 · 交易不受影响",
  },
  {
    state: "failed", paused: false, tone: "danger",
    text: "数据采集已停止：写入目录已被移除 · 交易不受影响",
  },
  { state: "closed", paused: false, tone: "muted", text: "数据采集已结束 · 1204 条" },
  { state: "off", paused: false, tone: "muted", text: "数据采集未在运行" },
];

describe("AiTradeTrajectoryStatus", () => {
  it.each(ROWS)("$state（暂停=$paused）⇒ data-tone=$tone，文案逐字", ({ state, paused, tone, text }) => {
    render(<AiTradeTrajectoryStatus receipt={receipt({ state })} paused={paused} />);
    const bar = screen.getByTestId("ai-trade-trajectory-status");
    expect(bar).toHaveAttribute("data-tone", tone);
    expect(bar).toHaveAttribute("data-state", state);
    expect(screen.getByTestId("ai-trade-trajectory-status-text").textContent).toBe(text);
  });

  it("单行不折行：文案 min-w-0 truncate，全文进 title；行容器 min-w-0", () => {
    render(<AiTradeTrajectoryStatus receipt={receipt()} paused={false} />);
    const text = screen.getByTestId("ai-trade-trajectory-status-text");
    expect(text).toHaveClass("min-w-0", "truncate");
    expect(text).toHaveAttribute("title", "数据采集中 · 1204 条 · 11.8 MB");
    expect(screen.getByTestId("ai-trade-trajectory-status")).toHaveClass("min-w-0");
  });

  it("dropped > 0 ⇒ 追加「· 缺口 N 条」", () => {
    const withGap = receipt();
    withGap.counters = { ...withGap.counters, dropped: 7 };
    render(<AiTradeTrajectoryStatus receipt={withGap} paused={false} />);
    expect(screen.getByTestId("ai-trade-trajectory-status-text")).toHaveTextContent(
      "数据采集中 · 1204 条 · 11.8 MB · 缺口 7 条",
    );
  });

  it("【RV2】requested=true、enabled=false、state=failed ⇒ 照样渲染并说出 lastError.message", () => {
    render(
      <AiTradeTrajectoryStatus
        receipt={receipt({
          enabled: false,
          state: "failed",
          directory: null,
          lastError: { code: "OPEN_FAILED", message: "无法创建分组目录：拒绝访问", ts: 1 },
        })}
        paused={false}
      />,
    );
    const bar = screen.getByTestId("ai-trade-trajectory-status");
    expect(bar).toHaveAttribute("data-tone", "danger");
    expect(bar).toHaveTextContent("⚠");
    expect(bar).toHaveTextContent("数据采集已停止：无法创建分组目录：拒绝访问 · 交易不受影响");
  });

  it("桌面端「打开目录」调用 openPath(receipt.directory)；失败时原地改口「无法打开」、原因进 title", async () => {
    const openPath = vi.fn().mockResolvedValue("not_found");
    setBridge({ openPath });
    render(<AiTradeTrajectoryStatus receipt={receipt()} paused={false} />);
    fireEvent.click(screen.getByRole("button", { name: /打开目录/ }));
    await waitFor(() => expect(openPath).toHaveBeenCalledWith(receipt().directory));
    const button = await screen.findByRole("button", { name: /无法打开/ });
    expect(button).toHaveAttribute("title", "无法打开：目录不存在（可能已被移动或删除）");
  });

  it("浏览器（无桥）/ 回执没有分组目录 ⇒ 不渲染按钮", () => {
    render(<AiTradeTrajectoryStatus receipt={receipt()} paused={false} />);
    expect(screen.queryByRole("button")).toBeNull();
    cleanup();
    setBridge({ openPath: vi.fn() });
    render(<AiTradeTrajectoryStatus receipt={receipt({ directory: null })} paused={false} />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("按钮的可访问名包含可见文字（WCAG 2.5.3 Label in Name）", () => {
    setBridge({ openPath: vi.fn() });
    render(<AiTradeTrajectoryStatus receipt={receipt()} paused={false} />);
    const button = screen.getByRole("button");
    const visible = (button.textContent ?? "").trim();
    expect(visible).toBe("打开目录");
    expect(button.getAttribute("aria-label")).toContain(visible);
  });
});
