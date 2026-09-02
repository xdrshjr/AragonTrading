// 深度思维链的分阶段解析（ai-trade-deep-reasoning-chain §3.5.1）。纯函数、无副作用、可离线单测。
//
// 一段 ~9000 字符的英文思维链原样糊进右侧窄栏 = 交互倒退。本模块把英文全文按**稳定锚点**
// （`## S1` … `## S12` / `## R1` … `## R7`）切段，交给呈现层做「渐进式披露 + 母语索引」。
//
// ⚠️ 阶段标记的正则有**三个**消费者，三者必须同一口径：
//   1. 后端 `reasoning_protocol.STAGE_HEADING_RE`（阶段计数、尾段摘录）；
//   2. 本文件（前端切段）；
//   3. `lib/markdown.ts` 的 `HEADING_RE = /^(#{1,6})\s+(.*)$/`（正文渲染）。
// 三者口径不一致时，`##S1`（无空格）会被前两者计为阶段、被渲染层当成普通正文——表现是
// 「导轨说有 12 段，展开后某一段的标题混在上一段正文里」。故此处与后端一样要求**行首顶格 +
// `##` 与字母之间恰好一个空格**（用例 F-11 冻结这一点）。

export type ReasoningKind = "decision" | "review";

export interface ReasoningStage {
  id: string; // "S1" … "S12" / "R1" … "R7"
  index: number; // 1..12 / 1..7
  code: string; // REGIME / EXECUTION …（模型给的英文码，缺失则空串）
  titleZh: string; // 中文阶段名（静态映射，**不依赖模型措辞**）
  body: string; // 该阶段正文（markdown 原文）
  complete: boolean; // 是否已收到下一个阶段标记（流式进度用）
}

export interface ParsedReasoning {
  preamble: string; // 首个阶段标记之前的内容（模型写的引言 / 未按协议输出时的全文）
  stages: ReasoningStage[];
}

// 中文阶段名的**唯一真源在前端**：它是纯呈现物，后端只认 `S<k>` 数字。
// 模型没按协议给出 code、或给了一个我们不认识的 code，都不会影响这里的中文名。
const DECISION_TITLES: Record<number, string> = {
  // execution-trend-verdict 轴 C：S1 的题面由「高周期体制」改为「基调 + 佐证」。这个改名在
  // 总闸开与关两种形态下都是真话（S1 本来就同时读基调与多周期背景），故**不随总闸联动**
  // ——后端总闸是运维回退开关，前端静态表跟着它联动只会多一条不必要的耦合。
  1: "趋势基调与多周期背景",
  2: "执行周期结构",
  3: "战术择时",
  4: "量能与参与度",
  5: "因子读数",
  6: "信号逐条裁决",
  7: "冲突与权重",
  8: "历史先验与经验",
  9: "持仓与铁律核对",
  10: "候选方案与期望值",
  11: "证伪与事前验尸",
  12: "校准结论",
};

const REVIEW_TITLES: Record<number, string> = {
  1: "复述提议",
  2: "证据核对",
  3: "遗漏的反证",
  4: "高周期与铁律冲突",
  5: "敞口与成本",
  6: "事前验尸",
  7: "分级裁决",
};

/** 协议规定的阶段总数（导轨格子数；模型多写的 `S13` 不改变分母）。 */
export const STAGE_TOTAL: Record<ReasoningKind, number> = { decision: 12, review: 7 };

/** 结算后展开时**默认展开**的阶段：最有决策价值的两段优先可见（§3.5.2 ③）。 */
export const DEFAULT_OPEN_STAGES: Record<ReasoningKind, number[]> = {
  decision: [11, 12],
  review: [6, 7],
};

// 与后端 `STAGE_HEADING_RE` 逐字对应：行首顶格 + `##` + 恰好一个空格 + S|R + 1~2 位数字。
const STAGE_HEADING_RE = /^## (S|R)(\d{1,2})\b[ \t]*(.*)$/;

function titleFor(kind: ReasoningKind, index: number): string {
  const table = kind === "review" ? REVIEW_TITLES : DECISION_TITLES;
  return table[index] ?? `阶段 ${index}`;
}

/** 从标题行的尾巴里取模型复述的英文 code（`REGIME - Higher-timeframe regime` → `REGIME`）。 */
function codeFrom(rest: string): string {
  const first = rest.trim().split(/[\s\-–—:：]/)[0] ?? "";
  return /^[A-Za-z][A-Za-z0-9_]{1,19}$/.test(first) ? first.toUpperCase() : "";
}

/**
 * 把研判 / 复核全文按阶段标记切段。
 *
 * 必须**容错**：模型漏了某个阶段、多写了 `S13`、在 S1 之前写了引言、正在流式写到一半——
 * 四种情形都不得抛错。漏掉的阶段由呈现层在导轨上留灰色空槽（这本身就是有价值的观测：
 * 用户能一眼看出模型跳过了 S11 证伪）。
 */
export function parseReasoningStages(text: string, kind: ReasoningKind): ParsedReasoning {
  const wanted = kind === "review" ? "R" : "S";
  const lines = (text ?? "").split("\n");
  const stages: ReasoningStage[] = [];
  const preamble: string[] = [];
  let current: { stage: ReasoningStage; body: string[] } | null = null;
  for (const line of lines) {
    const m = STAGE_HEADING_RE.exec(line);
    if (m && m[1] === wanted) {
      if (current) {
        current.stage.body = current.body.join("\n").trim();
        current.stage.complete = true;
        stages.push(current.stage);
      }
      const index = Number(m[2]);
      current = {
        stage: {
          id: `${m[1]}${m[2]}`,
          index,
          code: codeFrom(m[3] ?? ""),
          titleZh: titleFor(kind, index),
          body: "",
          complete: false,
        },
        body: [],
      };
      continue;
    }
    if (current) current.body.push(line);
    else preamble.push(line);
  }
  if (current) {
    // 最后一段**不标记为 complete**：流式期间它正是「当前阶段」，结算后由调用方按 state 判断。
    current.stage.body = current.body.join("\n").trim();
    stages.push(current.stage);
  }
  return { preamble: preamble.join("\n").trim(), stages };
}

/** 阶段进度（导轨用）：`done` 是命中的**不重复**阶段数，`total` 取协议规定的分母。 */
export function stageProgress(
  stages: ReasoningStage[],
  kind: ReasoningKind,
): { done: number; total: number } {
  const seen = new Set(stages.map((s) => s.index));
  return { done: seen.size, total: STAGE_TOTAL[kind] };
}

/** 该阶段序号是否命中（导轨实心 / 灰色空槽）。 */
export function stageAt(stages: ReasoningStage[], index: number): ReasoningStage | null {
  return stages.find((s) => s.index === index) ?? null;
}

/** 缺失的阶段中文名（诚实呈现，不美化：模型跳过了证伪就该看得见）。 */
export function missingStageTitles(stages: ReasoningStage[], kind: ReasoningKind): string[] {
  const seen = new Set(stages.map((s) => s.index));
  const out: string[] = [];
  for (let i = 1; i <= STAGE_TOTAL[kind]; i += 1) {
    if (!seen.has(i)) out.push(`${kind === "review" ? "R" : "S"}${i} ${titleFor(kind, i)}`);
  }
  return out;
}

/** 只取一段文本的尾部若干行（流式渲染用：DOM 节点数与轮次长度**解耦**）。 */
export function tailLines(text: string, maxLines = 4): string {
  const lines = (text ?? "").split("\n");
  return lines.slice(Math.max(0, lines.length - maxLines)).join("\n");
}

/**
 * 收起态标题（§3.5.2 ②）：`evidenceDigest` → S12 首句 → `reason`，三级回退。
 *
 * `evidenceDigest` 由后端保证是中文（不达标时后端已把它回落成 S12 尾段），此处不再二次判语言
 * ——两处各判一次必然漂移，而这里漂移的表现是「决策卡首段是中文、收起态标题是英文」。
 */
export function collapsedHeadline(
  digest: string | null | undefined,
  stages: ReasoningStage[],
  reason: string | null | undefined,
): string {
  const clean = (digest ?? "").trim();
  if (clean) return clean;
  // 取**最后**一个 S12：一次被采纳的追问会让模型把结论段写两遍，最终结论是后写的那一份
  // （与后端 `tail_stage_excerpt` 的取值口径逐字一致）。
  const verdicts = stages.filter((s) => s.index === STAGE_TOTAL.decision);
  const verdict = verdicts[verdicts.length - 1];
  const first = (verdict?.body ?? "").split(/(?<=[.。!！?？])\s/)[0]?.trim();
  if (first) return first;
  return (reason ?? "").trim();
}
