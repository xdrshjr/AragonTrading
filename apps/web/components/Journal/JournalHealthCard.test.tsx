import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { JournalStats } from "@/lib/journalTypes";
import JournalHealthCard from "./JournalHealthCard";

/**
 * 采集健康度卡（ai-trade-audit-journal §2.6.5）。
 *
 * 两条判据：**`dropped > 0` ⇒ 琥珀横幅且文案含条数**（审计系统必须说出自己哪里不完整），
 * 以及**口径数字全部来自 props**（一个都不硬编码——硬编码的读数一旦与常量分叉就开始说谎）。
 */
function stats(over: Partial<JournalStats> = {}): JournalStats {
  return {
    enabled: true,
    captureTier: "standard",
    contextChainEnabled: true,
    queue: { depth: 3, max: 2000, oldestPendingMs: 120 },
    counters: {
      enqueued: 91733,
      written: 91730,
      dropped: 0,
      droppedDecisions: 0,
      droppedEvents: 0,
      shedInfoEvents: 0,
      writeErrors: 0,
      serializeErrors: 0,
      droppedOnShutdown: 0,
    },
    tables: { decisions: 91730, events: 1204, promptProfiles: 6, approxBytes: 742318112 },
    retention: { days: 90, maxRows: 100000 },
    limits: {
      textMaxChars: 4000,
      payloadMaxChars: 24000,
      exportMaxRows: 200000,
      exportPageSize: 500,
      exportTimeBudgetS: 240,
      eventMinLevel: "info",
    },
    ...over,
  };
}

afterEach(cleanup);

describe("JournalHealthCard", () => {
  it("没有读数时如实说尚未载入，绝不画一张全 0 的卡", () => {
    render(<JournalHealthCard stats={null} />);
    expect(screen.getByText(/尚未载入/)).toBeTruthy();
  });

  it("dropped > 0 ⇒ 出横幅且文案含条数", () => {
    render(<JournalHealthCard stats={stats({
      counters: { ...stats().counters, dropped: 12 },
    })} />);
    const banner = screen.getByRole("alert");
    expect(banner.textContent).toContain("12");
    expect(banner.textContent).toContain("审计记录存在缺口");
  });

  it("关停未排空也计进缺口（那同样是一段没写下来的历史）", () => {
    render(<JournalHealthCard stats={stats({
      counters: { ...stats().counters, droppedOnShutdown: 5 },
    })} />);
    expect(screen.getByRole("alert").textContent).toContain("5");
  });

  it("没有缺口时不出横幅（不制造无谓的红点）", () => {
    render(<JournalHealthCard stats={stats()} />);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("序列化失败单列一句，并说清那几轮的交易本身没受影响", () => {
    render(<JournalHealthCard stats={stats({
      counters: { ...stats().counters, serializeErrors: 2 },
    })} />);
    expect(screen.getByText(/序列化时失败/).textContent).toContain("交易本身照常完成");
  });

  it("口径数字全部来自 props：改一个 props，界面上的那个数跟着变", () => {
    const { unmount } = render(<JournalHealthCard stats={stats()} />);
    expect(screen.getByText("100000")).toBeTruthy();     // 保留行数
    expect(screen.getByText("200000")).toBeTruthy();     // 导出行数上限
    unmount();
    render(<JournalHealthCard stats={stats({
      retention: { days: 7, maxRows: 42 },
      limits: { ...stats().limits, exportMaxRows: 99 },
    })} />);
    expect(screen.getByText("42")).toBeTruthy();
    expect(screen.getByText("99")).toBeTruthy();
  });

  it("两把保留闸置 0 时说「不限（该闸未启用）」，而不是显示一个会被误读的 0", () => {
    render(<JournalHealthCard stats={stats({ retention: { days: 0, maxRows: 0 } })} />);
    expect(screen.getAllByText("不限（该闸未启用）").length).toBeGreaterThanOrEqual(2);
  });

  it("总闸关 / 上下文链关时各出一枚带形状记号的徽标", () => {
    render(<JournalHealthCard stats={stats({ enabled: false, contextChainEnabled: false })} />);
    // 「已关闭」在两枚徽标里各出现一次（采集 / 上下文链），故按各自的完整文案分别取。
    expect(screen.getByText("已关闭")).toBeTruthy();
    expect(screen.getByText("上下文链已关闭")).toBeTruthy();
  });

  it("实占字节按量级换单位（742318112 → 708.0 MB 级）", () => {
    render(<JournalHealthCard stats={stats()} />);
    expect(screen.getByText(/MB$/)).toBeTruthy();
  });
});
