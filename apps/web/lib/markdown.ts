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

type Block =
  | { kind: "code"; lang: string; lines: string[] }
  | { kind: "heading"; level: number; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "p"; lines: string[] };

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
  while (i < lines.length && lines[i].trim() !== "" && !isBlockStart(lines[i])) {
    body.push(lines[i]);
    i += 1;
  }
  return [{ kind: "p", lines: body }, i];
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
      className: "text-[var(--accent)] underline underline-offset-2 hover:brightness-110",
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
    default:
      return renderParagraph(block.lines, key);
  }
}

/** 把 assistant markdown 文本渲染为安全的 React 元素树（无 dangerouslySetInnerHTML）。 */
export function renderMarkdown(text: string): React.ReactNode {
  return parseBlocks(text).map((block, i) => renderBlock(block, `b-${i}`));
}
