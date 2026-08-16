import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { EQUITY_MAX, TRADES_MAX } from "./wireCaps";

// 前后端封顶常量的等值门禁（spec D4 / [R-16]）。
//
// 为什么是源码扫描：这两个数字住在两个进程、两种语言里，没有任何运行期链路会把它们放在一起比。
// 一旦分叉，后端下发 2000 条而前端只留 500 条（或反过来）——症状是「成交莫名其妙少了一截」，
// 而两边各自的用例都是绿的。把两个数字钉在一起是唯一能在改动当天变红的做法。
//
// core.autocrlf=true：跨行正则一律写 \r?\n，否则干净检出上恒红。

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");

function readPython(relative: string): string {
  return fs.readFileSync(path.join(REPO_ROOT, relative), "utf8").replace(/\r?\n/g, "\n");
}

function readIntConstant(source: string, name: string): number {
  // 行尾允许带注释（后端两个常量一个带一个不带），故只锚定「行首 名字 = 数字」这一段。
  const match = source.match(new RegExp(`^${name}\\s*=\\s*(\\d+)\\b`, "m"));
  expect(match, `未能在源码里读到常量 ${name}`).toBeTruthy();
  return Number((match as RegExpMatchArray)[1]);
}

describe("前后端 wire 封顶常量必须相等（D4）", () => {
  it("TRADES_MAX === wire_caps.SNAPSHOT_TRADES_MAX", () => {
    const backend = readPython("services/quant/app/wire_caps.py");
    expect(readIntConstant(backend, "SNAPSHOT_TRADES_MAX")).toBe(TRADES_MAX);
  });

  it("EQUITY_MAX === paper/session.py 的 _SNAPSHOT_EQUITY_MAX", () => {
    const backend = readPython("services/quant/app/paper/session.py");
    expect(readIntConstant(backend, "_SNAPSHOT_EQUITY_MAX")).toBe(EQUITY_MAX);
  });

  it("后端 wire_caps 明确写着「不裁剪会计真值」这条红线", () => {
    // 这条红线一旦从 docstring 里消失，下一个读到 `tail_trades` 的人就会顺手把它用在
    // `executor.trades` 本体上——那是静默改变交易行为（换手闸放松 / metrics 低报）。
    const backend = readPython("services/quant/app/wire_caps.py");
    expect(backend).toContain("绝不裁剪服务端的会计真值");
  });
});
