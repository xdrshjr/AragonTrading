import { describe, expect, it } from "vitest";
import {
  describeOpenPathFailure,
  describeTrajectoryReceipt,
  describeVerdictLine,
  formatBytes,
  plainText,
  trajectoryConfigBlocker,
  trajectoryDirError,
  trajectoryNotice,
} from "./trajectory";
import type {
  AiTradeTrajectoryConfig,
  AiTradeTrajectoryCounters,
  AiTradeTrajectoryDirVerdict,
  AiTradeTrajectoryReceipt,
  AiTradeTrajectoryState,
} from "./trajectoryTypes";

/**
 * 后训练轨迹采集纯函数层（ai-trade-trajectory-capture §6.2 F-1）。
 *
 * 守两件渲染用例察觉不到的事：
 *  ① 「读不到」绝不被渲染成 0——可用空间显示 0 B 等于告诉用户盘满了；
 *  ② 「到底采不采」只有一个判据（【v2 · RV3】）：说明文案必须与 `effectiveEnabled` 互斥。
 */

const CONFIG: AiTradeTrajectoryConfig = {
  enabled: true,
  defaultRoot: "C:\\Users\\me\\AppData\\Roaming\\aragon-desktop\\datasets\\ai-trade",
  minFreeBytes: 1_073_741_824,
  segmentMaxBytes: 67_108_864,
  compressSegments: true,
  schemaVersion: 1,
  backtestSupported: false,
  thinkingCapturedProviders: ["anthropic"],
};

const OK_VERDICT: AiTradeTrajectoryDirVerdict = {
  ok: true,
  code: null,
  message: null,
  directory: "D:\\data\\ai-trade",
  isDefault: false,
  exists: true,
  freeBytes: 137_870_000_000,
  minFreeBytes: 1_073_741_824,
};

const BAD_VERDICT: AiTradeTrajectoryDirVerdict = {
  ...OK_VERDICT,
  ok: false,
  code: "TRAJECTORY_DIR_NOT_WRITABLE",
  message: "目录不可写：拒绝访问（13）",
};

const COUNTERS: AiTradeTrajectoryCounters = {
  records: 1204, llmCalls: 388, rounds: 130, bytesWritten: 12_345_678, segments: 1,
  dropped: 0, writeErrors: 2, serializeErrors: 0, redactions: 0, lateCalls: 0,
  manifestWriteErrors: 0,
};

function receipt(
  over: Partial<AiTradeTrajectoryReceipt> = {},
  counters: Partial<AiTradeTrajectoryCounters> = {},
): AiTradeTrajectoryReceipt {
  return {
    enabled: true,
    requested: true,
    source: "request",
    state: "recording",
    root: "D:\\data",
    directory: "D:\\data\\20260910-aitradegrp_1c8f",
    isDefaultRoot: false,
    counters: { ...COUNTERS, ...counters },
    pendingBytes: 0,
    freeBytes: 128_400_000_000,
    lastError: null,
    schemaVersion: 1,
    ...over,
  };
}

const PENDING = { isPending: true, isError: false, data: undefined };
const FAILED = { isPending: false, isError: true, data: undefined };
const READY = { isPending: false, isError: false, data: CONFIG };
const OPS_OFF = { isPending: false, isError: false, data: { ...CONFIG, enabled: false } };

describe("formatBytes", () => {
  it("读不到（null / undefined / NaN / ±Infinity / 负数）⇒ null，绝不渲染成 0 B", () => {
    for (const value of [null, undefined, Number.NaN, Infinity, -Infinity, -1]) {
      expect(formatBytes(value), String(value)).toBeNull();
    }
  });

  it("0 是真实读数 ⇒「0 B」；1023 仍按字节，1024 起按 1024 进位保留一位小数", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1023)).toBe("1023 B");
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(1_073_741_824)).toBe("1.0 GB");
    expect(formatBytes(137_870_000_000)).toBe("128.4 GB");
  });

  it("超过 TB 不再进位（没有 PB 这一档）", () => {
    expect(formatBytes(1024 ** 5)).toBe("1024.0 TB");
  });
});

describe("describeOpenPathFailure（桌面端「打开」的拒绝码）", () => {
  it("主进程的三个拒绝码翻译成中文原因", () => {
    expect(describeOpenPathFailure("not_found")).toBe("目录不存在（可能已被移动或删除）");
    expect(describeOpenPathFailure("not_directory")).toBe("该路径不是文件夹");
    expect(describeOpenPathFailure("invalid_path")).toBe("路径无效");
  });

  it("系统错误文本原样透传；原型键名不被当成已知码", () => {
    expect(describeOpenPathFailure("Access is denied.")).toBe("Access is denied.");
    expect(describeOpenPathFailure("toString")).toBe("toString");
  });
});

describe("plainText（后端文案的纯文本化）", () => {
  it("去掉 Markdown 强调符与反引号、首尾空白；null ⇒ 空串", () => {
    expect(plainText("  **目录不可写**：`EACCES`  ")).toBe("目录不可写：EACCES");
    expect(plainText(null)).toBe("");
  });
});

describe("trajectoryDirError", () => {
  it("未激活 / 无回执 / ok ⇒ null（请求在途与请求失败都不阻断）", () => {
    expect(trajectoryDirError({ active: false, verdict: BAD_VERDICT })).toBeNull();
    expect(trajectoryDirError({ active: true, verdict: null })).toBeNull();
    expect(trajectoryDirError({ active: true, verdict: OK_VERDICT })).toBeNull();
  });

  it("激活且 ok=false ⇒ 后端下发的原因", () => {
    expect(trajectoryDirError({ active: true, verdict: BAD_VERDICT })).toBe(
      "目录不可写：拒绝访问（13）",
    );
  });

  it("ok=false 却没给原因 ⇒ 通用句（空串会让 `ready` 的 !error 判真，等于放行一份 ok=false）", () => {
    for (const message of [null, "", "   "]) {
      expect(trajectoryDirError({ active: true, verdict: { ...BAD_VERDICT, message } }))
        .toBe("目录不可用");
    }
  });
});

describe("trajectoryNotice（【v2 · RV3】五个分支）", () => {
  const ON = { enabled: true, directory: null };
  const OFF = { enabled: false, directory: null };

  it("① 回测段 / 用户没勾 ⇒ null（不管配置是什么态）", () => {
    expect(trajectoryNotice({ mode: "backtest", rule: ON, config: FAILED })).toBeNull();
    expect(trajectoryNotice({ mode: "live", rule: OFF, config: FAILED })).toBeNull();
  });

  it("② 配置在途 ⇒「正在读取采集配置…」", () => {
    expect(trajectoryNotice({ mode: "live", rule: ON, config: PENDING }))
      .toBe("正在读取采集配置…");
  });

  it("③ 配置读取失败（老后端 404 / 网络）⇒ 明说本次不会采集", () => {
    expect(trajectoryNotice({ mode: "live", rule: ON, config: FAILED }))
      .toBe("无法读取采集配置（后端尚未就绪），本次不会采集");
  });

  it("④ 运维关 ⇒ 明说是运维关的", () => {
    expect(trajectoryNotice({ mode: "live", rule: ON, config: OPS_OFF }))
      .toBe("后训练数据采集已被运维关闭，本次不会采集");
  });

  it("⑤ 配置可用 ⇒ null（此时 effectiveEnabled 为真，两者互斥）", () => {
    expect(trajectoryNotice({ mode: "live", rule: ON, config: READY })).toBeNull();
  });

  it("后台重取失败但旧数据说开着 ⇒ 仍是 null（与 effectiveEnabled 读同一份 data）", () => {
    // React Query v5 在一次失败的后台重取之后同时给出 `isError` 与旧 `data`。先判 isError 会让
    // 「实际在采」与「说本次不会采」同屏出现——【v2 · RV3】要消灭的正是这种判据分叉。
    const stale = { isPending: false, isError: true, data: CONFIG };
    expect(trajectoryConfigBlocker(stale)).toBeNull();
    expect(trajectoryNotice({ mode: "live", rule: ON, config: stale })).toBeNull();
  });
});

describe("describeVerdictLine（卡片校验行）", () => {
  const base = { isFetching: false, isError: false };

  it("在途 ⇒ 正在检查；请求失败 ⇒ 不阻断的说明；还没有回执 ⇒ 不渲染", () => {
    expect(describeVerdictLine({ ...base, isFetching: true, verdict: OK_VERDICT }))
      .toEqual({ kind: "checking", text: "正在检查目录…" });
    expect(describeVerdictLine({ ...base, isError: true, verdict: undefined }))
      .toEqual({ kind: "unknown", text: "暂时无法检查该目录，启动时会再检查一次" });
    expect(describeVerdictLine({ ...base, verdict: undefined })).toBeNull();
  });

  it("ok=false ⇒ ⚠ + 原因（形状 + 文字双通道）", () => {
    expect(describeVerdictLine({ ...base, verdict: BAD_VERDICT }))
      .toEqual({ kind: "bad", text: "⚠ 目录不可写：拒绝访问（13）" });
  });

  it("ok ⇒ ✓ 可写 · 可用 X · 新建子目录说明", () => {
    expect(describeVerdictLine({ ...base, verdict: OK_VERDICT })).toEqual({
      kind: "ok",
      text: "✓ 可写 · 可用 128.4 GB · 本次启动会新建子目录「日期-分组号」",
    });
  });

  it("ok 但可用空间读不到 ⇒ 整段省略，绝不显示「0 B」", () => {
    const line = describeVerdictLine({ ...base, verdict: { ...OK_VERDICT, freeBytes: null } });
    expect(line?.text).toBe("✓ 可写 · 本次启动会新建子目录「日期-分组号」");
    expect(line?.text).not.toContain("0 B");
  });
});

describe("describeTrajectoryReceipt（六个 state × 暂停与否）", () => {
  const FAILURE = { code: "WRITE_FAILED", message: "写入目录已被移除", ts: 1_755_500_000 };
  const TABLE: [AiTradeTrajectoryState, boolean, string, string, string][] = [
    ["recording", false, "accent", "●", "数据采集中 · 1204 条 · 11.8 MB"],
    ["recording", true, "muted", "●", "数据采集待命（交易已暂停）· 1204 条"],
    ["degraded", false, "warn", "△", "数据采集有写入失败（2 次），仍在继续 · 交易不受影响"],
    ["degraded", true, "warn", "△", "数据采集有写入失败（2 次），仍在继续 · 交易不受影响"],
    ["paused_low_disk", false, "warn", "△", "数据采集已暂停：可用空间低于下限 · 交易不受影响"],
    ["paused_low_disk", true, "warn", "△", "数据采集已暂停：可用空间低于下限 · 交易不受影响"],
    ["failed", false, "danger", "⚠", "数据采集已停止：写入目录已被移除 · 交易不受影响"],
    ["failed", true, "danger", "⚠", "数据采集已停止：写入目录已被移除 · 交易不受影响"],
    ["closed", false, "muted", "—", "数据采集已结束 · 1204 条"],
    ["closed", true, "muted", "—", "数据采集已结束 · 1204 条"],
    ["off", false, "muted", "—", "数据采集未在运行"],
    ["off", true, "muted", "—", "数据采集未在运行"],
  ];

  it.each(TABLE)("%s（暂停=%s）⇒ %s / %s / 文案逐字", (state, paused, tone, glyph, text) => {
    const view = describeTrajectoryReceipt(receipt({ state, lastError: FAILURE }), paused);
    expect(view).toEqual({ tone, glyph, text });
  });

  it("dropped > 0 ⇒ 末尾追加「· 缺口 N 条」（任何状态都追加）", () => {
    expect(describeTrajectoryReceipt(receipt({}, { dropped: 3 }), false).text)
      .toBe("数据采集中 · 1204 条 · 11.8 MB · 缺口 3 条");
    expect(
      describeTrajectoryReceipt(receipt({ state: "closed" }, { dropped: 1 }), false).text,
    ).toBe("数据采集已结束 · 1204 条 · 缺口 1 条");
  });

  it("【RV2】打开失败：requested=true、enabled=false、state=failed ⇒ 危险语气 + 原因", () => {
    const view = describeTrajectoryReceipt(
      receipt({
        enabled: false,
        state: "failed",
        lastError: { code: "OPEN_FAILED", message: "无法创建分组目录", ts: 1 },
      }),
      false,
    );
    expect(view.tone).toBe("danger");
    expect(view.text).toContain("无法创建分组目录");
  });

  it("failed 却没有 lastError ⇒ 如实说原因未知，不留一个空的冒号", () => {
    expect(describeTrajectoryReceipt(receipt({ state: "failed" }), false).text)
      .toBe("数据采集已停止：原因未知 · 交易不受影响");
  });

  it("后端文案带 Markdown 强调符 ⇒ 渲染前剥掉（本仓第 4 次复发的教训）", () => {
    const view = describeTrajectoryReceipt(
      receipt({ state: "failed", lastError: { code: "X", message: "**磁盘已满**", ts: 1 } }),
      false,
    );
    expect(view.text).not.toContain("**");
    expect(view.text).toContain("磁盘已满");
  });

  it("后端将来加的未知状态 ⇒ 如实报出状态名，而不是冒充成某个已知状态", () => {
    const future = receipt({ state: "draining" as AiTradeTrajectoryState });
    expect(describeTrajectoryReceipt(future, false))
      .toEqual({ tone: "muted", glyph: "—", text: "数据采集状态：draining" });
  });
});
