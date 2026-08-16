// 无依赖、安全的 markdown 轻渲染（RS-8）：把 assistant 文本解析为最小富文本子集后产出 React
// 元素树。安全铁律：所有文本一律作为 React 字符串子节点（自动 HTML 转义）、**绝不**使用
// dangerouslySetInnerHTML、绝不渲染原始 HTML；链接仅放行 http(s) 协议（中和 javascript: 等向量）。
// 模型输出即不可信输入（对齐 CLAUDE.md §五"外部输入边界校验"）。解析器拆为小函数守阈值。
import React from "react";

const SAFE_HREF = /^https?:\/\//i;
const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const UL_RE = /^\s*[-*+]\s+(.*)$/;
const OL_RE = /^\s*\d+[.)]\s+(.*)$/;
const LINK_RE = /^\[([^\]]+)\]\(([^)]+)\)$/;
// 行内 token：`code` / [text](url) / **bold** / *italic*（顺序即优先级，bold 先于 italic）。
const INLINE_RE = /(`[^`]+`)|(\[[^\]]+\]\([^)]+\))|(\*\*[^*]+\*\*)|(\*[^*]+\*)/g;
// GFM 表格分隔单元格：可选左右冒号 + 至少一个连字符（如 `---` / `:--` / `:--:` / `--:`）。
const DELIM_CELL_RE = /^\s*:?-{1,}:?\s*$/;

type Align = "left" | "center" | "right";

type Block =
  | { kind: "code"; lang: string; lines: string[] }
  | { kind: "heading"; level: number; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "p"; lines: string[] }
  | { kind: "table"; header: string[]; align: (Align | null)[]; rows: string[][] };

/** 链接协议白名单：仅 http(s) 放行，杜绝 javascript:/data: 等危险协议（安全铁律，R6）。 */
export function isSafeHref(url: string): boolean {
  return SAFE_HREF.test(url.trim());
}

function isFence(line: string): boolean {
  return line.trimStart().startsWith("```");
}

function isBlockStart(line: string): boolean {
  return isFence(line) || HEADING_RE.test(line) || UL_RE.test(line) || OL_RE.test(line);
}

function takeCode(lines: string[], start: number): [Block, number] {
  const lang = lines[start].trimStart().slice(3).trim();
  const body: string[] = [];
  let i = start + 1;
  while (i < lines.length && !isFence(lines[i])) {
    body.push(lines[i]);
    i += 1;
  }
  // 容错收尾（R7）：未闭合围栏也成块；闭合则跳过结束 fence 行。
  if (i < lines.length) i += 1;
  return [{ kind: "code", lang, lines: body }, i];
}

function takeList(lines: string[], start: number, kind: "ul" | "ol"): [Block, number] {
  const re = kind === "ul" ? UL_RE : OL_RE;
  const items: string[] = [];
  let i = start;
  for (let m = re.exec(lines[i]); i < lines.length && m; m = re.exec(lines[i])) {
    items.push(m[1]);
    i += 1;
  }
  return [{ kind, items }, i];
}

function takeParagraph(lines: string[], start: number): [Block, number] {
  const body: string[] = [];
  let i = start;
  while (
    i < lines.length &&
    lines[i].trim() !== "" &&
    !isBlockStart(lines[i]) &&
    !isTableStart(lines, i)
  ) {
    body.push(lines[i]);
    i += 1;
  }
  return [{ kind: "p", lines: body }, i];
}

// —— 表格识别（前瞻判定：仅当下一行为分隔行才认作表格，杜绝含竖线的普通段落误判，R5）——

function isTableRow(line: string): boolean {
  return line.includes("|");
}

/** 去掉外侧一对竖线后按**未转义**竖线切分，再把 `\|` 反转义为字面量竖线、逐格 trim。 */
function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  // ES2018 lookbehind：Electron(Chromium)/现代浏览器 + Node(vitest) 均支持（R9）。
  return s.split(/(?<!\\)\|/).map((c) => c.replace(/\\\|/g, "|").trim());
}

function isTableDelimiter(line: string): boolean {
  if (!line.includes("-")) return false;
  const cells = splitRow(line);
  return cells.length > 0 && cells.every((c) => DELIM_CELL_RE.test(c));
}

function isTableStart(lines: string[], i: number): boolean {
  return i + 1 < lines.length && isTableRow(lines[i]) && isTableDelimiter(lines[i + 1]);
}

function parseAlign(cell: string): Align | null {
  const l = cell.startsWith(":");
  const r = cell.endsWith(":");
  if (l && r) return "center";
  if (r) return "right";
  if (l) return "left";
  return null;
}

/** 把数据行对齐到表头列数：多截断、少补空（GFM 容错，R6），渲染期不再越界。 */
function normalizeRow(cells: string[], cols: number): string[] {
  const out = cells.slice(0, cols);
  while (out.length < cols) out.push("");
  return out;
}

function takeTable(lines: string[], start: number): [Block, number] {
  const header = splitRow(lines[start]);
  const align = splitRow(lines[start + 1]).map(parseAlign);
  const rows: string[][] = [];
  let i = start + 2;
  while (
    i < lines.length &&
    lines[i].trim() !== "" &&
    isTableRow(lines[i]) &&
    !isFence(lines[i])
  ) {
    rows.push(normalizeRow(splitRow(lines[i]), header.length));
    i += 1;
  }
  return [{ kind: "table", header, align, rows }, i];
}

/** 块级扫描：把原文切成代码块 / 标题 / 列表 / 段落（小函数 + 早分派，守圈复杂度）。 */
export function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === "") {
      i += 1;
    } else if (isFence(line)) {
      [, i] = pushBlock(blocks, takeCode(lines, i));
    } else if (HEADING_RE.test(line)) {
      const m = HEADING_RE.exec(line) as RegExpExecArray;
      blocks.push({ kind: "heading", level: m[1].length, text: m[2].trim() });
      i += 1;
    } else if (UL_RE.test(line)) {
      [, i] = pushBlock(blocks, takeList(lines, i, "ul"));
    } else if (OL_RE.test(line)) {
      [, i] = pushBlock(blocks, takeList(lines, i, "ol"));
    } else if (isTableStart(lines, i)) {
      [, i] = pushBlock(blocks, takeTable(lines, i));
    } else {
      [, i] = pushBlock(blocks, takeParagraph(lines, i));
    }
  }
  return blocks;
}

function pushBlock(blocks: Block[], taken: [Block, number]): [Block, number] {
  blocks.push(taken[0]);
  return taken;
}

function renderLink(token: string, key: string): React.ReactNode {
  const m = LINK_RE.exec(token);
  if (!m) return token;
  const [, label, url] = m;
  // 危险协议：仅渲染纯文本标签，绝不产出可执行链接（安全护栏，R6）。
  if (!isSafeHref(url)) return label;
  return React.createElement(
    "a",
    {
      key,
      href: url.trim(),
      target: "_blank",
      rel: "noopener noreferrer",
      className: "text-[var(--accent-ink)] underline underline-offset-2 hover:brightness-110",
    },
    label,
  );
}

function renderToken(token: string, key: string): React.ReactNode {
  if (token.startsWith("`")) {
    return React.createElement(
      "code",
      { key, className: "rounded bg-[var(--surface-2)] px-1 py-0.5 font-mono text-[12px]" },
      token.slice(1, -1),
    );
  }
  if (token.startsWith("[")) return renderLink(token, key);
  if (token.startsWith("**")) return React.createElement("strong", { key }, token.slice(2, -2));
  if (token.startsWith("*")) return React.createElement("em", { key }, token.slice(1, -1));
  return token;
}

/** 行内扫描：切出 code/链接/粗斜体 token，其余作纯文本（React 文本节点自动转义）。 */
export function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let last = 0;
  let k = 0;
  INLINE_RE.lastIndex = 0;
  for (let m = INLINE_RE.exec(text); m !== null; m = INLINE_RE.exec(text)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(renderToken(m[0], `${keyPrefix}-${k}`));
    last = INLINE_RE.lastIndex;
    k += 1;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function renderCode(block: Extract<Block, { kind: "code" }>, key: string): React.ReactNode {
  return React.createElement(
    "pre",
    {
      key,
      className:
        "my-1.5 overflow-x-auto rounded-[8px] border border-[var(--border)] bg-[var(--surface-2)] p-2.5 text-[12px] leading-relaxed",
    },
    React.createElement("code", { className: "font-mono text-[var(--text)]" }, block.lines.join("\n")),
  );
}

function renderHeading(block: Extract<Block, { kind: "heading" }>, key: string): React.ReactNode {
  // 气泡内降级标题尺寸（避免巨大标题破坏紧凑布局），保留真实 h 标签语义（a11y）。
  const tag = `h${Math.min(block.level + 2, 6)}`;
  return React.createElement(
    tag,
    { key, className: "mb-0.5 mt-1.5 font-semibold text-[var(--text)]" },
    renderInline(block.text, `${key}-h`),
  );
}

function renderList(items: string[], key: string, kind: "ul" | "ol"): React.ReactNode {
  const cls = kind === "ul" ? "my-1 list-disc pl-5" : "my-1 list-decimal pl-5";
  const lis = items.map((it, i) =>
    React.createElement("li", { key: `${key}-${i}`, className: "my-0.5" }, renderInline(it, `${key}-${i}`)),
  );
  return React.createElement(kind, { key, className: cls }, lis);
}

function renderParagraph(lines: string[], key: string): React.ReactNode {
  const children: React.ReactNode[] = [];
  lines.forEach((ln, i) => {
    if (i > 0) children.push(React.createElement("br", { key: `${key}-br-${i}` }));
    children.push(...renderInline(ln, `${key}-l-${i}`));
  });
  return React.createElement("p", { key, className: "my-1 break-words leading-relaxed" }, children);
}

// —— 表格渲染（单元格一律走 renderInline → 继承字符串子节点自动转义 + http(s) 协议白名单，R8）——
const ALIGN_CLASS: Record<Align, string> = {
  left: "text-left",
  center: "text-center",
  right: "text-right",
};

function cellClass(align: Align | null, isHeader: boolean): string {
  const base = isHeader
    ? "border border-[var(--border)] bg-[var(--surface-2)] px-2.5 py-1.5 font-semibold"
    : "border border-[var(--border)] px-2.5 py-1.5";
  return align ? `${base} ${ALIGN_CLASS[align]}` : base;
}

function renderRow(
  cells: string[],
  align: (Align | null)[],
  key: string,
  tag: "th" | "td",
): React.ReactNode {
  const isHeader = tag === "th";
  const tds = cells.map((c, i) =>
    React.createElement(
      tag,
      {
        key: `${key}-${i}`,
        className: cellClass(align[i] ?? null, isHeader),
        scope: isHeader ? "col" : undefined, // a11y：表头列范围
      },
      renderInline(c, `${key}-${i}`),
    ),
  );
  return React.createElement("tr", { key }, tds);
}

function renderTable(block: Extract<Block, { kind: "table" }>, key: string): React.ReactNode {
  const thead = React.createElement(
    "thead",
    { key: `${key}-h` },
    renderRow(block.header, block.align, `${key}-hr`, "th"),
  );
  const tbody = React.createElement(
    "tbody",
    { key: `${key}-b` },
    block.rows.map((r, i) => renderRow(r, block.align, `${key}-r${i}`, "td")),
  );
  const table = React.createElement(
    "table",
    { className: "w-full border-collapse text-[12px] leading-relaxed text-[var(--text)]" },
    thead,
    tbody,
  );
  // 外层 div 负责窄面板横向滚动与外边距（R7）。
  return React.createElement("div", { key, className: "my-1.5 overflow-x-auto" }, table);
}

function renderBlock(block: Block, key: string): React.ReactNode {
  switch (block.kind) {
    case "code":
      return renderCode(block, key);
    case "heading":
      return renderHeading(block, key);
    case "ul":
      return renderList(block.items, key, "ul");
    case "ol":
      return renderList(block.items, key, "ol");
    case "table":
      return renderTable(block, key);
    default:
      return renderParagraph(block.lines, key);
  }
}

/** 把 assistant markdown 文本渲染为安全的 React 元素树（无 dangerouslySetInnerHTML）。 */
export function renderMarkdown(text: string): React.ReactNode {
  return parseBlocks(text).map((block, i) => renderBlock(block, `b-${i}`));
}
