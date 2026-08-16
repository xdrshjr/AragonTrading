import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

// 根级错误边界的源码扫描（spec D6）。
//
// 为什么是源码扫描而不是渲染用例：`global-error.tsx` 自带 `<html><body>` 外壳，在 jsdom 里
// 渲染一个嵌套 `<html>` 得到的是一个失真的 DOM；而这条用例真正要钉住的四件事全部是**文件事实**
// ——它存在、它是客户端组件、它自带外壳、它自己导入了样式表。第四条尤其重要：`globals.css` 的
// 唯一导入点是 `app/layout.tsx`，而根级边界替换的正是那个 layout，漏了这一行崩溃页就退化成
// 一段无样式黑字——恰好是这次事故里被判定为「白屏」的那个形状。
//
// core.autocrlf=true：跨行正则一律写 \r?\n，否则干净检出上恒红。

describe("根级错误边界存在且自带外壳（D6）", () => {
  const file = path.resolve(__dirname, "global-error.tsx");

  it("文件存在", () => {
    expect(fs.existsSync(file)).toBe(true);
  });

  it("是客户端组件、自带 html/body 外壳、自己导入 globals.css、复用 CrashFallback", () => {
    const source = fs.readFileSync(file, "utf8").replace(/\r?\n/g, "\n");
    expect(source).toMatch(/^"use client";/);
    expect(source).toContain("<html");
    expect(source).toContain("<body");
    expect(source).toContain('import "./globals.css"');
    expect(source).toContain("CrashFallback");
  });
});
