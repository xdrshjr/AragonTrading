import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { isSafeHref, parseBlocks, renderMarkdown } from "./markdown";

function html(src: string): string {
  return renderToStaticMarkup(React.createElement(React.Fragment, null, renderMarkdown(src)));
}

describe("markdown rendering (RS-8)", () => {
  it("renders fenced code blocks", () => {
    const out = html("```js\nconst a = 1;\n```");
    expect(out).toContain("<pre");
    expect(out).toContain("<code");
    expect(out).toContain("const a = 1;");
  });

  it("renders inline code, bold and italic", () => {
    const out = html("普通 `code` **粗** *斜*");
    expect(out).toContain("<code");
    expect(out).toContain("<strong>粗</strong>");
    expect(out).toContain("<em>斜</em>");
  });

  it("renders unordered and ordered lists", () => {
    expect(html("- a\n- b")).toContain("<ul");
    expect(html("1. a\n2. b")).toContain("<ol");
  });

  it("renders headings as heading tags", () => {
    expect(html("# 标题")).toMatch(/<h[1-6]/);
  });

  it("renders safe http(s) links with rel/target", () => {
    const out = html("看 [这里](https://example.com)");
    expect(out).toContain('href="https://example.com"');
    expect(out).toContain('rel="noopener noreferrer"');
    expect(out).toContain('target="_blank"');
  });
});

describe("markdown safety — XSS 护栏 (R6)", () => {
  it("escapes raw HTML, never emits executable markup", () => {
    const out = html("<script>alert(1)</script>");
    expect(out).not.toContain("<script>");
    expect(out).toContain("&lt;script&gt;");
  });

  it("neutralizes img onerror injection (no <img> emitted)", () => {
    const out = html('<img src=x onerror="alert(1)">');
    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;img");
  });

  it("rejects javascript: links — renders label as plain text, no anchor", () => {
    const out = html("[x](javascript:alert(1))");
    expect(out).not.toContain("<a");
    expect(out).not.toContain("javascript:");
    expect(out).toContain("x");
  });

  it("isSafeHref allows only http(s)", () => {
    expect(isSafeHref("https://e.com")).toBe(true);
    expect(isSafeHref("http://e.com")).toBe(true);
    expect(isSafeHref("javascript:alert(1)")).toBe(false);
    expect(isSafeHref("data:text/html,x")).toBe(false);
  });

  it("tolerates an unclosed code fence without throwing (R7)", () => {
    expect(() => html("```js\nconst a = 1;")).not.toThrow();
    expect(html("```js\nconst a = 1;")).toContain("const a = 1;");
  });
});

describe("parseBlocks structure", () => {
  it("separates code / heading / list / paragraph", () => {
    const blocks = parseBlocks("# H\n\n段落\n\n- a\n\n```\nx\n```");
    expect(blocks.map((b) => b.kind)).toEqual(["heading", "p", "ul", "code"]);
  });
});

describe("markdown tables (GFM)", () => {
  it("renders a GFM table with thead/tbody", () => {
    const out = html("| A | B |\n| --- | --- |\n| a1 | b1 |");
    expect(out).toContain("<table");
    expect(out).toContain("<thead");
    expect(out).toContain("<th");
    expect(out).toContain("<td");
    expect(out).toContain("A");
    expect(out).toContain("b1");
  });

  it("applies column alignment classes", () => {
    const out = html("| L | C | R |\n| :-- | :--: | --: |\n| 1 | 2 | 3 |");
    expect(out).toContain("text-left");
    expect(out).toContain("text-center");
    expect(out).toContain("text-right");
  });

  it("renders inline formatting inside cells", () => {
    const out = html("| H |\n| --- |\n| **粗** |");
    expect(out).toContain("<strong>粗</strong>");
  });

  it("does not treat a pipe paragraph without a delimiter row as a table", () => {
    const out = html("a | b\nc | d");
    expect(out).not.toContain("<table");
  });

  it("unescapes escaped pipes in cells", () => {
    const out = html("| a\\|b | c |\n| --- | --- |\n| 1 | 2 |");
    expect(out).toContain("a|b");
  });

  it("escapes html inside table cells (R6)", () => {
    const out = html("| H |\n| --- |\n| <script>alert(1)</script> |");
    expect(out).not.toContain("<script>");
    expect(out).toContain("&lt;script&gt;");
  });

  it("parseBlocks separates a table block", () => {
    const blocks = parseBlocks("| A | B |\n| --- | --- |\n| a1 | b1 |");
    expect(blocks.map((b) => b.kind)).toContain("table");
  });

  it("separates a table that immediately follows a paragraph", () => {
    const blocks = parseBlocks("段落文字\n| A | B |\n| --- | --- |\n| a1 | b1 |");
    expect(blocks.map((b) => b.kind)).toEqual(["p", "table"]);
  });
});
