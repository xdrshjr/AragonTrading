import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import IntelReaderBanner from "./IntelReaderBanner";
import type { IntelDocumentHealth } from "@/lib/intelTypes";

function health(over: Partial<IntelDocumentHealth> = {}): IntelDocumentHealth {
  return {
    cachedDocs: 30, bodyBytes: 0, oldestFetchedTs: 0, lastPrunedTs: 0, readerEnabled: true,
    fetchAttempt: 102, fetchOk: 0, zeroOkStreak: 17,
    blockedByStatus: { blocked: 30 },
    rejectCodesSinceStart: { private_host: 29, dns_failed: 1 },
    sinceProcessStart: true,
    ...over,
  };
}

beforeEach(() => cleanup());

describe("IntelReaderBanner（silent-degradation-repair 轴 B-2）", () => {
  it("连续零命中达阈 ⇒ 出现，且读数与成因分两行说", () => {
    render(<IntelReaderBanner health={health()} streakWarn={3} />);
    const banner = screen.getByTestId("intel-reader-banner");
    expect(banner).toHaveTextContent("已连续 17 轮一篇都没抓到");
    expect(banner).toHaveTextContent("正文抓取 0/102 全部失败");
    // 成因是**猜测**，与事实分行——一句被当成结论的猜测比不说话更贵。
    expect(banner).toHaveTextContent("198.18.0.0/15");
  });

  it("未达阈值 ⇒ 整段不渲染（一轮抓不到是常态，做成常驻横幅只会变成背景噪声）", () => {
    const { container } = render(
      <IntelReaderBanner health={health({ zeroOkStreak: 2 })} streakWarn={3} />);
    expect(container.querySelector("[data-testid='intel-reader-banner']")).toBeNull();
  });

  it("抓到过 ⇒ 不渲染；老后端（键缺席）⇒ 不渲染", () => {
    const ok = render(<IntelReaderBanner health={health({ fetchOk: 5 })} streakWarn={1} />);
    expect(ok.container.querySelector("[data-testid='intel-reader-banner']")).toBeNull();
    cleanup();
    const legacy = render(
      <IntelReaderBanner
        health={{ cachedDocs: 3, bodyBytes: 10, oldestFetchedTs: 0, lastPrunedTs: 0,
                  readerEnabled: true }}
        streakWarn={1} />);
    expect(legacy.container.querySelector("[data-testid='intel-reader-banner']")).toBeNull();
    cleanup();
    const none = render(<IntelReaderBanner health={null} />);
    expect(none.container.querySelector("[data-testid='intel-reader-banner']")).toBeNull();
  });

  it("被拦明细默认收起；展开后**如实标注哪一份会随重启归零**", () => {
    render(<IntelReaderBanner health={health()} streakWarn={3} />);
    expect(screen.queryByTestId("intel-reader-reject-codes")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看被拦明细" }));
    expect(screen.getByTestId("intel-reader-blocked-status")).toHaveTextContent("blocked 30");
    const rejects = screen.getByTestId("intel-reader-reject-codes");
    expect(rejects).toHaveTextContent("private_host 29");
    expect(rejects).toHaveTextContent("重启后归零");
  });

  it("界面文本里绝不出现 markdown 粗体标记", () => {
    render(<IntelReaderBanner health={health()} streakWarn={3} />);
    expect(screen.getByTestId("intel-reader-banner").textContent).not.toContain("**");
  });
});
