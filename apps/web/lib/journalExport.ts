// 导出 URL 的组装与分片下载（ai-trade-audit-journal §2.6.4）。
//
// **刻意不用 `fetch` + `Blob`**：几百 MB 的 NDJSON 走 Blob 会把渲染进程的内存顶起来。
// 下载一律由隐藏的 `<a download>` 触发（服务端已设 `Content-Disposition`）。
//
// **分片必须顺序、不得并发**：并发会让 SQLite 上的几个长扫描互相抢读锁，而这条链上还挂着一个
// 正在下单的会话。
import { toQuery } from "./journalApi";
import type { ExportFormat, JournalFilters, SftOptions } from "./journalTypes";

const BASE = "/api/py/ai-trade/journal/export";

export interface ExportUrlInput {
  format: ExportFormat;
  scope: JournalFilters;
  sft?: Partial<SftOptions>;
  /** 上一片 `eof.nextBeforeId`（NDJSON）或尾注行第三列（CSV）。第 1 片不传。 */
  beforeId?: number;
  /** 第几片（1-based）。只影响文件名，不影响取数。 */
  part?: number;
}

/**
 * 组装一次（分片）导出的 URL。
 *
 * 后训练专属参数只在 `format === "sft-jsonl"` 时才拼进去——把它们无条件带上会让两个审计档的
 * URL 里出现一串没有任何作用的参数，而那正是「看起来生效、其实没读」的温床。
 */
export function buildExportUrl(input: ExportUrlInput): string {
  const params: Record<string, unknown> = { format: input.format, ...input.scope };
  if (input.beforeId) params.beforeId = input.beforeId;
  if (input.part && input.part > 1) params.part = input.part;
  if (input.format === "sft-jsonl" && input.sft) {
    // `false` 也必须进串：`excludeGateVetoed=false` 是用户的显式放开，`toQuery` 会把 `false`
    // 丢掉，故这里显式转成字符串。
    const sft = input.sft;
    if (sft.excludeGateVetoed !== undefined) {
      params.excludeGateVetoed = String(sft.excludeGateVetoed);
    }
    if (sft.excludeDegraded !== undefined) params.excludeDegraded = String(sft.excludeDegraded);
    if (sft.onlySettled !== undefined) params.onlySettled = String(sft.onlySettled);
    if (sft.includeHold !== undefined) params.includeHold = String(sft.includeHold);
    if (sft.minLinkConfidence) params.minLinkConfidence = sft.minLinkConfidence;
    if (sft.contextMode) params.contextMode = sft.contextMode;
  }
  return `${BASE}${toQuery(params)}`;
}

/**
 * 触发一次浏览器下载。
 *
 * @param url 由 `buildExportUrl` 组装的地址。
 * @param doc 注入点（测试可传替身）。
 */
export function triggerDownload(url: string, doc: Document = document): void {
  const anchor = doc.createElement("a");
  anchor.href = url;
  anchor.setAttribute("download", "");
  anchor.style.display = "none";
  doc.body.appendChild(anchor);
  anchor.click();
  doc.body.removeChild(anchor);
}

/**
 * 顺序触发 N 片下载。
 *
 * **必须顺序、不得并发**（见模块 docstring）。每片之间留一个短间隔，让浏览器把上一片的下载
 * 真正发出去——同一 tick 里连点 N 次 `<a download>` 在部分浏览器上只会发出第一次。
 *
 * ⚠️ 浏览器导航式的下载**读不回响应体**，故第 k+1 片的游标**不能**指望第 k 片的
 * `eof.nextBeforeId`——它要到下载完成才存在，而那时前端也读不到。游标改由**预检**给出
 * （`preview.sliceCursors`，服务端在同一份过滤 + 同一个排序上精确算出）。
 * 片是自洽的：每片都有自己的 manifest 与 eof，任何一片缺 eof 行即视为该片不完整。
 */
export async function downloadSlices(
  urls: string[],
  opts: { doc?: Document; gapMs?: number; sleep?: (ms: number) => Promise<void> } = {},
): Promise<number> {
  const doc = opts.doc ?? document;
  const gap = opts.gapMs ?? 400;
  const sleep = opts.sleep ?? ((ms: number) => new Promise((r) => setTimeout(r, ms)));
  let done = 0;
  for (const url of urls) {
    triggerDownload(url, doc);
    done += 1;
    if (done < urls.length) await sleep(gap);
  }
  return done;
}

/**
 * 按预检回执把一次导出切成 N 片的 URL 列表。
 *
 * 第 1 片不带游标；第 k+1 片带 `cursors[k-1]`——那是**服务端精确算出**的边界
 * （`preview.sliceCursors`），不是前端估的。
 *
 * ⚠️ **游标不够就少发几片**：一片没有游标的请求会从头再拉一遍全量，用户拿到的是两个内容
 * 重叠的文件，而它在界面上和一次正常分片长得一模一样。调用方按
 * `min(slices, cursors.length + 1)` 收敛片数。
 */
export function buildSliceUrls(input: ExportUrlInput, slices: number,
                               cursors: number[] = []): string[] {
  const total = Math.max(1, Math.min(Math.floor(slices), cursors.length + 1));
  return Array.from({ length: total }, (_v, index) =>
    buildExportUrl({
      ...input,
      part: index + 1,
      beforeId: index === 0 ? undefined : cursors[index - 1],
    }),
  );
}
