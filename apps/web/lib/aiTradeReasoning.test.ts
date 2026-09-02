import { describe, expect, it } from "vitest";
import {
  collapsedHeadline,
  missingStageTitles,
  parseReasoningStages,
  stageAt,
  stageProgress,
  tailLines,
} from "./aiTradeReasoning";

// 分阶段解析纯函数（ai-trade-deep-reasoning-chain §7.6 F-1 / F-2 / F-3 / F-4 / F-11）。
// 三处正则必须同口径：后端 `reasoning_protocol.STAGE_HEADING_RE`、本模块、`lib/markdown.ts`。

function twelveStages(): string {
  const parts: string[] = [];
  for (let i = 1; i <= 12; i += 1) parts.push(`## S${i} CODE${i}\nbody of stage ${i}`);
  return parts.join("\n");
}

describe("parseReasoningStages", () => {
  it("parses twelve stages with chinese titles from the static map", () => {
    const { stages, preamble } = parseReasoningStages(twelveStages(), "decision");
    expect(stages).toHaveLength(12);
    expect(preamble).toBe("");
    // F-1：中文阶段名取自**静态映射**，不依赖模型给的 code。
    // F-6（execution-trend-verdict 轴 C）：S1 的题面由「高周期体制」改为「基调 + 佐证」。
    // 这个改名在总闸开与关两种形态下都是真话，故**不随总闸联动**。
    expect(stages[0].titleZh).toBe("趋势基调与多周期背景");
    expect(stages[10].titleZh).toBe("证伪与事前验尸");
    expect(stages[11].titleZh).toBe("校准结论");
    expect(stages[0].code).toBe("CODE1");
    expect(stages[3].body).toBe("body of stage 4");
    expect(stageProgress(stages, "decision")).toEqual({ done: 12, total: 12 });
  });

  it("keeps the preamble the model wrote before S1", () => {
    const { preamble, stages } = parseReasoningStages(
      "Let me work through this.\n## S1 REGIME\n1D is up.",
      "decision",
    );
    expect(preamble).toBe("Let me work through this.");
    expect(stages).toHaveLength(1);
  });

  it("tolerates missing and streaming-partial stages", () => {
    // F-2：漏 S3 ⇒ 导轨 11 实 1 灰、不抛错。
    const missing = twelveStages()
      .split("\n## S3 CODE3\nbody of stage 3")
      .join("");
    const { stages } = parseReasoningStages(missing, "decision");
    expect(stages).toHaveLength(11);
    expect(stageAt(stages, 3)).toBeNull();
    expect(missingStageTitles(stages, "decision")).toEqual(["S3 战术择时"]);
    // 半截 S7 ⇒ 最后一段 `complete === false`（它正是流式期的「当前阶段」）。
    const partial = parseReasoningStages("## S6 A\nfull\n## S7 B\nhalf", "decision");
    expect(partial.stages[0].complete).toBe(true);
    expect(partial.stages[1].complete).toBe(false);
  });

  it("tolerates an out-of-protocol S13 without changing the denominator", () => {
    const { stages } = parseReasoningStages(`${twelveStages()}\n## S13 EXTRA\noops`, "decision");
    expect(stages).toHaveLength(13);
    expect(stageProgress(stages, "decision").total).toBe(12);
  });

  it("falls back to flat rendering without stage markers", () => {
    // F-3：无标记 ⇒ 零阶段（呈现层据此回退整段渲染 + 「未分阶段」标识）。
    const { stages, preamble } = parseReasoningStages("just a wall of text", "decision");
    expect(stages).toHaveLength(0);
    expect(preamble).toBe("just a wall of text");
  });

  it("agrees with the markdown heading rule on spacing and indentation", () => {
    // F-11：`##S1`（无空格）与 `   ## S1`（缩进）**都不**被当作阶段标记。
    expect(parseReasoningStages("##S1 A\nbody", "decision").stages).toHaveLength(0);
    expect(parseReasoningStages("   ## S1 A\nbody", "decision").stages).toHaveLength(0);
    expect(parseReasoningStages("#### S1 A\nbody", "decision").stages).toHaveLength(0);
  });

  it("never mixes decision and review prefixes", () => {
    const text = "## S1 A\nx\n## R1 B\ny\n## R2 C\nz";
    expect(parseReasoningStages(text, "decision").stages).toHaveLength(1);
    const review = parseReasoningStages(text, "review");
    expect(review.stages).toHaveLength(2);
    expect(review.stages[0].titleZh).toBe("复述提议");
    expect(stageProgress(review.stages, "review")).toEqual({ done: 2, total: 7 });
  });

  it("handles empty input without throwing", () => {
    expect(parseReasoningStages("", "decision").stages).toEqual([]);
    expect(missingStageTitles([], "review")).toHaveLength(7);
  });
});

describe("collapsedHeadline", () => {
  it("prefers evidenceDigest, then the S12 first sentence, then reason", () => {
    // F-4：三级回退。
    const { stages } = parseReasoningStages(
      `${twelveStages()}\n## S12 VERDICT\nChosen action is hold. More text follows.`,
      "decision",
    );
    expect(collapsedHeadline("采纳 4H 趋势", stages, "r")).toBe("采纳 4H 趋势");
    expect(collapsedHeadline(null, stages, "r")).toBe("Chosen action is hold.");
    expect(collapsedHeadline(null, [], "回退到理由")).toBe("回退到理由");
    expect(collapsedHeadline(undefined, [], undefined)).toBe("");
  });
});

describe("tailLines", () => {
  it("keeps only the tail so the DOM stays decoupled from the text length", () => {
    const text = Array.from({ length: 200 }, (_, i) => `line ${i}`).join("\n");
    expect(tailLines(text, 4).split("\n")).toHaveLength(4);
    expect(tailLines(text, 4).endsWith("line 199")).toBe(true);
    expect(tailLines("", 4)).toBe("");
  });
});
