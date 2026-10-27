import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ExportPreview, JournalStats } from "@/lib/journalTypes";

/**
 * 导出中心（ai-trade-audit-journal §2.6.4）。
 *
 * 四条判据，每一条都对应一个「不这么做就会骗人」的场景：
 *
 * 1. `minimal` 档 ⇒ 后训练导出 disabled 且给出原因（一个点了会导出空文件的按钮更坏）。
 * 2. `skipped` **九项逐项渲染**（不说「另外 2810 条为什么没导出」会让人以为系统只做了 37 次决策）。
 * 3. `slices=3` ⇒ 按钮写明片数与预估耗时，点击后**顺序**触发 3 次下载（**不并发**）。
 * 4. `resolvedMinLinkConfidence` 照抄回执并给出中文说明，**前端不重算**。
 */
const exportPreview = vi.fn();
vi.mock("@/lib/journalApi", () => ({
  journalApi: { exportPreview: (...a: unknown[]) => exportPreview(...a) },
  toQuery: (params: Record<string, unknown>) => {
    const search = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === null || v === undefined || v === "" || v === false) continue;
      search.set(k, String(v));
    }
    const t = search.toString();
    return t ? `?${t}` : "";
  },
}));

const downloads: string[] = [];
vi.mock("@/lib/journalExport", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/journalExport")>();
  return {
    ...actual,
    downloadSlices: async (urls: string[]) => {
      downloads.push(...urls);
      return urls.length;
    },
  };
});

import JournalExportDialog from "./JournalExportDialog";

function preview(over: Partial<ExportPreview> = {}): ExportPreview {
  return {
    rows: 372,
    approxBytes: 18344112,
    overLimit: false,
    estimatedSeconds: 512,
    slices: 1,
    sliceCursors: [],
    timeBudgetS: 240,
    resolvedMinLinkConfidence: "exact",
    resolvedReason: "client_order_id_present",
    skipped: {
      gateVetoed: 118,
      degraded: 41,
      unsettled: 93,
      hold: 2810,
      lowConfidence: 6,
      promptDrift: 0,
      payloadTruncated: 0,
      minimalTier: 0,
      contextIncomplete: 12,
    },
    ...over,
  };
}

function stats(over: Partial<JournalStats> = {}): JournalStats {
  return {
    enabled: true,
    captureTier: "standard",
    contextChainEnabled: true,
    queue: { depth: 0, max: 2000, oldestPendingMs: 0 },
    counters: {
      enqueued: 0, written: 0, dropped: 0, droppedDecisions: 0, droppedEvents: 0,
      shedInfoEvents: 0, writeErrors: 0, serializeErrors: 0, droppedOnShutdown: 0,
    },
    tables: { decisions: 10, events: 2, promptProfiles: 1, approxBytes: 100 },
    retention: { days: 90, maxRows: 100000 },
    limits: {
      textMaxChars: 4000, payloadMaxChars: 24000, exportMaxRows: 200000,
      exportPageSize: 500, exportTimeBudgetS: 240, eventMinLevel: "info",
    },
    ...over,
  };
}

function open(props: { stats?: JournalStats } = {}) {
  return render(
    <JournalExportDialog
      open
      onClose={() => undefined}
      scope={{ groupId: "grp-1" }}
      stats={props.stats ?? stats()}
    />,
  );
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  exportPreview.mockReset();
  exportPreview.mockResolvedValue(preview());
  downloads.length = 0;
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

describe("JournalExportDialog", () => {
  it("minimal 档 ⇒ 后训练选项 disabled 并说清原因与要改的设置项", async () => {
    open({ stats: stats({ captureTier: "minimal" }) });
    const radio = screen.getByRole("radio", { name: /后训练 JSONL/ });
    expect(radio).toBeDisabled();
    expect(screen.getByText(/不含模型输入 \/ 输出/)).toBeTruthy();
    expect(screen.getByText(/AI_TRADE_JOURNAL_CAPTURE_TIER/)).toBeTruthy();
  });

  it("九个 skipped 桶逐项渲染（少一项就等于把一批样本的去向藏起来）", async () => {
    open();
    await vi.advanceTimersByTimeAsync(500);
    await waitFor(() => expect(screen.getByText(/将导出/)).toBeTruthy());
    for (const label of [
      "被确定性门禁改写或否决", "本轮决策降级（输出是系统合成的观望）", "尚未产生完整往返结果",
      "观望轮", "关联置信度不足", "提示词无法逐字重建", "上下文被截断",
      "采集档为精简，不含模型输入输出", "上下文链重建不出来（前驱轮缺席）",
    ]) {
      expect(screen.getByText(new RegExp(label.replace(/[()（）/]/g, ".")))).toBeTruthy();
    }
  });

  it("slices=3 ⇒ 按钮写明片数与预估耗时，点击后顺序触发 3 次下载（不并发）", async () => {
    exportPreview.mockResolvedValue(preview({
      slices: 3, estimatedSeconds: 372, sliceCursors: [91733, 84120],
    }));
    open();
    await vi.advanceTimersByTimeAsync(500);
    const button = await screen.findByRole("button", { name: /分 3 个文件/ });
    expect(button.textContent).toContain("6 分 12 秒");
    fireEvent.click(button);
    await waitFor(() => expect(downloads).toHaveLength(3));
    // 顺序契约：第 1 片不带游标，后续片各自带 part 序号（并发会让几个长扫描互相抢读锁）。
    expect(downloads[0]).not.toContain("part=");
    expect(downloads[1]).toContain("part=2");
    expect(downloads[2]).toContain("part=3");
    // ★ 游标契约：没有它，三片会是三个一模一样的文件——比不分片更坏。
    expect(downloads[0]).not.toContain("beforeId");
    expect(downloads[1]).toContain("beforeId=91733");
    expect(downloads[2]).toContain("beforeId=84120");
  });

  it("服务端只给得出 1 个游标时只发 2 片，且按钮文案跟着说 2 片", async () => {
    // 写「分 3 个文件」却只下 2 个，是这个界面上最容易被信以为真的一句假话。
    exportPreview.mockResolvedValue(preview({ slices: 3, sliceCursors: [91733] }));
    open();
    await vi.advanceTimersByTimeAsync(500);
    const button = await screen.findByRole("button", { name: /分 2 个文件/ });
    expect(screen.queryByRole("button", { name: /分 3 个文件/ })).toBeNull();
    fireEvent.click(button);
    await waitFor(() => expect(downloads).toHaveLength(2));
    expect(downloads[1]).toContain("beforeId=91733");
  });

  it("resolvedMinLinkConfidence 照抄回执并给出中文说明，前端不重算", async () => {
    exportPreview.mockResolvedValue(preview({
      resolvedMinLinkConfidence: "heuristic",
      resolvedReason: "no_client_order_id_in_scope",
    }));
    open();
    fireEvent.click(screen.getByRole("radio", { name: /后训练 JSONL/ }));
    await vi.advanceTimersByTimeAsync(500);
    await waitFor(() =>
      expect(screen.getByText(/本次实际生效：heuristic/)).toBeTruthy());
    expect(screen.getByText(/未发现交易所单号，已自动放宽到启发式关联/)).toBeTruthy();
  });

  it("contextMode 默认停在 full；切到 lastTurnOnly 时出醒目提示", async () => {
    open();
    fireEvent.click(screen.getByRole("radio", { name: /后训练 JSONL/ }));
    const full = screen.getByRole("radio", { name: /整段多轮对话/ });
    expect(full).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: /只含最后一轮/ }));
    expect(screen.getByRole("alert").textContent).toContain("样本将只含最后一轮");
  });

  it("上下文链总闸关 ⇒ full 不可选，且落到 lastTurnOnly 并说明原因", async () => {
    open({ stats: stats({ contextChainEnabled: false }) });
    fireEvent.click(screen.getByRole("radio", { name: /后训练 JSONL/ }));
    await waitFor(() =>
      expect(screen.getByRole("radio", { name: /只含最后一轮/ })).toBeChecked());
    expect(screen.getByRole("radio", { name: /整段多轮对话/ })).toBeDisabled();
    expect(screen.getByText(/只能导出单轮样本/)).toBeTruthy();
  });

  it("超过行数上限 ⇒ 禁用导出按钮并给出收窄建议", async () => {
    exportPreview.mockResolvedValue(preview({ overLimit: true, rows: 300000 }));
    open();
    await vi.advanceTimersByTimeAsync(500);
    await waitFor(() => expect(screen.getByText(/超过单次导出的行数上限/)).toBeTruthy());
    expect(screen.getByRole("button", { name: /导出/ })).toBeDisabled();
  });

  it("预检失败时说出错误，绝不留一个看起来能点的按钮", async () => {
    exportPreview.mockRejectedValue(new Error("JOURNAL_DOWN"));
    open();
    await vi.advanceTimersByTimeAsync(500);
    await waitFor(() => expect(screen.getByText(/预检失败：JOURNAL_DOWN/)).toBeTruthy());
    expect(screen.getByRole("button", { name: /^导出$/ })).toBeDisabled();
  });
});
