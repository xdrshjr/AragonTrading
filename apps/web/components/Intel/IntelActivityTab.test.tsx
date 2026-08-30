import "@testing-library/jest-dom/vitest";
import fs from "node:fs";
import path from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { IntelAlert, IntelRound } from "@/lib/intelTypes";
import IntelActivityTab from "./IntelActivityTab";
import { CODE_LABEL } from "./IntelRoundsTab";

const NOW_MS = 1_800_000_000_000;
const NOW_S = NOW_MS / 1000;

function round(over: Partial<IntelRound> = {}): IntelRound {
  return {
    id: 1, instId: "BTC-USDT-SWAP", kind: "collect", groupId: "g", startedTs: NOW_S - 600,
    finishedTs: NOW_S - 590, status: "ok", searchProvider: "tavily", queries: [],
    docCount: 16, domainCount: 6, accepted: 3, rejected: 1, merged: 0,
    rejects: {}, actions: {}, inputTokens: 100, outputTokens: 20, degradeCode: "",
    errorText: "", readerAttempt: 0, readerOk: 0, quotesProposed: 0, quotesVerified: 0,
    ...over,
  };
}

function alert(over: Partial<IntelAlert> = {}): IntelAlert {
  return {
    id: 1, instId: "BTC-USDT-SWAP", groupId: "g", itemId: 41, roundId: 1,
    createdTs: NOW_S - 599, severity: 0.82, fired: true, suppressedCode: "",
    suppressedText: "", outcome: "woke", outcomeText: "已唤醒一轮决策", outcomeTs: NOW_S - 598,
    runId: 3, roundNo: 47, headline: "某交易所热钱包异常出金", category: "risk",
    tier: "ultra", stance: "bearish", impact: "high", domainCount: 3, quoted: true,
    confidence: 0.78, ref: "I02", inDigest: true, reasons: {},
    ...over,
  };
}

afterEach(cleanup);

describe("IntelActivityTab · 轮次与告警合流", () => {
  it("两类行按时刻倒序合流成一条时间线", () => {
    render(<IntelActivityTab rounds={[round()]} alerts={[alert()]} loading={false}
      nowMs={NOW_MS} pendingTtlS={180} />);
    const timeline = screen.getByTestId("intel-activity");
    const text = timeline.textContent ?? "";
    // 采集轮在 10:03 开始、告警在同一秒之后 ⇒ 告警行排在**前面**（倒序）。
    expect(text.indexOf("已唤醒一轮决策")).toBeLessThan(text.indexOf("16 篇材料"));
  });

  it("轮次行复用 `IntelRoundsTab` 的渲染，不抄第二份", () => {
    render(<IntelActivityTab rounds={[round()]} alerts={[]} loading={false} nowMs={NOW_MS}
      pendingTtlS={180} />);
    expect(screen.getByText(/16 篇材料 \/ 6 个来源/)).toBeInTheDocument();
    expect(screen.getByText("采集")).toBeInTheDocument();
  });

  it("空态说的是这一屏自己的话（含「一条够格的高危情报会当场多问 AI 一次」）", () => {
    render(<IntelActivityTab rounds={[]} alerts={[]} loading={false} nowMs={NOW_MS}
      pendingTtlS={180} />);
    expect(screen.getByText(/还没有任何采集、整理或告警记录/)).toBeInTheDocument();
  });

  it("首次加载走骨架屏，不把已渲染的内容清空重画", () => {
    const { container } = render(<IntelActivityTab rounds={[]} alerts={[]} loading
      nowMs={NOW_MS} pendingTtlS={180} />);
    expect(container.querySelector("[data-testid='intel-activity']")).toBeNull();
  });

  it("折叠只发生在**相邻**的同码抑制行之间：中间隔着一次采集轮就不该被折起来", () => {
    render(<IntelActivityTab
      rounds={[]}
      alerts={[
        alert({ id: 5, fired: false, outcome: "", outcomeText: "",
          suppressedCode: "cooldown", suppressedText: "距上次告警不足冷却时间" }),
        alert({ id: 4, fired: false, outcome: "", outcomeText: "",
          suppressedCode: "cooldown", suppressedText: "距上次告警不足冷却时间" }),
      ]}
      loading={false} nowMs={NOW_MS} pendingTtlS={180} />);
    expect(screen.getAllByTestId("intel-alert-row")).toHaveLength(1);
    expect(screen.getByTestId("intel-alert-fold")).toHaveTextContent("×2");
  });
});

describe("G-24 · 后端码表 ⊆ 前端 CODE_LABEL", () => {
  it("三个新驳回码与两个矛盾计数都有中文", () => {
    for (const code of ["promote_weak_record", "keep_both_bad_pair", "keep_both_thin_note",
      "keep_both", "conflictsOpen", "conflictsResolved", "alerts_pruned"]) {
      expect(CODE_LABEL[code], code).toBeTruthy();
    }
  });

  it("后端 `REVIEW_REJECT_CODES` 的每一个码都能在前端找到文案", () => {
    // core.autocrlf=true：跨行正则写成 CR 可选 + LF 的形态，否则干净检出上恒红。
    const taxonomy = path.resolve(
      __dirname, "..", "..", "..", "..", "services", "quant", "app", "ai_auto", "intel",
      "taxonomy.py",
    );
    const source = fs.readFileSync(taxonomy, "utf8");
    const block = /REVIEW_REJECT_CODES: tuple\[str, \.\.\.\] = \(([\s\S]*?)\r?\n\)/.exec(source);
    if (!block) throw new Error("taxonomy.py 里找不到 REVIEW_REJECT_CODES——这条门禁会假绿");
    const codes = Array.from(block[1].matchAll(/"([a-z_]+)"/g)).map((m) => m[1]);
    expect(codes.length).toBeGreaterThan(8);   // 先证明真的解析到了东西
    expect(codes.filter((code) => !CODE_LABEL[code])).toEqual([]);
  });
});
