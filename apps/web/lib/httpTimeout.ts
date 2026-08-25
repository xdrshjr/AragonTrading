// REST 请求的超时决策（单一真源，trust-boundary-and-signal-integrity D3）。
//
// **准确的问题陈述**（不夸大）：浏览器 `fetch` 本身**没有默认超时**。后端进程死了这条路径没问题
// ——连接立即被拒，前端正常报错。出问题的是「后端活着但不回」：事件循环被占、线程池被挖掘/训练
// 占满、某个 OKX 调用挂住。这时打包 / standalone 路径下唯一的兜底是 `next.config.mjs` 的
// `experimental.proxyTimeout`（5 分钟传输层超时），按钮**冻结最长 5 分钟且零反馈**；
// 而 `next dev` 与任何直连后端的路径**没有任何兜底**。
//
// 这个仓库前两轮的主线就是「让每一条失败路径出声」——给 4 条 WS 流补了空闲保活、给客户端配了 45s
// 停摆看门狗，HTTP 这一侧却被整轮跳过了。一个「停止会话」按钮转了 5 分钟圈还什么都不说，
// 和一条假装连着的 WS 是同一种失信。
//
// 本模块是**纯函数**（无 React、无 fetch），18 个 `*Api.ts` 各自把它接进自己既有的 `request`
// 包装即可，决策集中在这里、接线留在原地，回归面最小。

/** 普通读写：20 秒足够覆盖一次本机往返 + 一次 OKX 调用。 */
export const FAST_MS = 20_000;

/** = `next.config.mjs` 的 `experimental.proxyTimeout`。两个数字由 contrastTokens.source.test.ts 钉在一起。 */
export const PROXY_TIMEOUT_MS = 300_000;

/**
 * 重计算路径（回测 / 挖掘 / 训练）的超时。
 *
 * **必须严格大于 `PROXY_TIMEOUT_MS`，不能相等。** 判据是「客户端绝不能比代理先放弃，否则会把
 * 『代理还在等』误报成『超时了』」——而**客户端计时从发起请求那一刻起算，代理计时从 Next
 * 服务端收到请求才起算**，取等值时客户端**必然**先触发，正好落进这条判据要避免的误报。
 * 15 秒裕量覆盖排队 / 连接 / 传输。
 */
export const LONG_MS = PROXY_TIMEOUT_MS + 15_000;

/**
 * 续跑档（ai-trade-lifecycle-truth F-7，修 D-8）。
 *
 * `/ai-trade/{groupId}/resume` 今天落 `FAST_MS`（20 秒），而**冷续跑要跑**
 * `resolve_active_provider` + 逐成员 `_prime()`（`prefill_htf` / `prefill_tactical` /
 * `fetch_priming_rows` / 逐根影子回放）+ 交易所私有取数。多标的 / 慢网下客户端先放弃：
 * **后端续跑成功、界面停在「已暂停 + 继续失败」**——正是用户报告现象的镜像版。
 *
 * 为什么不是 `LONG_MS`：`FAST_MS < RESUME_MS < PROXY_TIMEOUT_MS`。取 `LONG_MS`（5 分 15 秒）
 * 会让用户对着一个转圈的按钮干等五分钟；取 3 分钟则代理不会先放弃，客户端也不至于等太久。
 */
export const RESUME_MS = 180_000;

/**
 * 长跑路径前缀表（逐条来自实读的后端路由，不猜）。
 *
 * 匹配的是 `/api/py` **之后**的那段路径，即各 `*Api.ts` 传给 `request(path)` 的原样入参。
 * 含路径参数的用「前缀 + 尾段」两截判定（`/strategies/7/backtest`）。
 */
const LONG_EXACT_PREFIXES = [
  "/strategies/compare",
  "/strategies/combo/preview",
  "/factors/mine",
  "/factors/evaluate",
  "/factors/compute",          // 因子实验室的单因子预览：拉 K 线 + 全窗口计算，同步返回
  "/factor-sets/train",        // 组合训练的内联版本，与 /weight-models/train 是同一条重计算
  "/factor-sets/score-preview", // 组合打分的内联版本，与 /factor-sets/{id}/score 同形
  "/weight-models/train",
  "/ai-trade/prior/rebuild",
  // 交易经验库（ai-trade-experience-context-graph E-11）：全库重建在大库上超 20 秒是常态
  // （F-2 自己论证过），导出是几万条事件的 NDJSON 流，替换式导入要先删再写。三条都落
  // `FAST_MS` 的话，「重建图谱」按钮**必然**假超时——而后端其实跑得好好的。
  "/experience/rebuild",
  "/experience/export",
  "/experience/import",
] as const;

/**
 * `/strategies/{id}/backtest` 这类「前缀 + 中间是 id + 尾段」的长跑路径。
 *
 * `ms` 是**可选**档位：缺省仍取 `LONG_MS`，故既有六行一字不改。加它是为了让「比 20 秒长、
 * 但远不到 5 分钟」的路径（续跑）有个自己的档，而不是被迫二选一。
 */
const LONG_SEGMENT_SUFFIXES = [
  { prefix: "/strategies/", suffix: "/backtest" },
  { prefix: "/strategies/", suffix: "/multi-backtest" },
  { prefix: "/strategies/", suffix: "/walk-forward" },
  { prefix: "/factor-sets/", suffix: "/score" },
  { prefix: "/weight-models/", suffix: "/backtest" },      // 一键回测：/weight-models/{id}/runs/{id}/backtest
  { prefix: "/discovery/candidates/", suffix: "/backtest" }, // 候选提升前的一次性回测，同步返回
  // 冷续跑：provider 解析 + 逐成员 `_prime()` + 交易所私有取数（D-8）。
  { prefix: "/ai-trade/", suffix: "/resume", ms: RESUME_MS },
  // 续跑预检同样要读交易所（持仓 / 挂单 / 账户配置）。
  { prefix: "/ai-trade/", suffix: "/resume-preview", ms: RESUME_MS },
] as const satisfies readonly { prefix: string; suffix: string; ms?: number }[];

/**
 * 写类路径表 —— **不靠 HTTP 方法猜**。
 *
 * `/okx/connectivity/probe` 是 POST 但只读，按方法猜会给它挂上「结果未知」的文案，
 * 那是在一个什么都没改的操作上制造恐慌。
 *
 * 无路径参数的走这张前缀表；**带 `{id}` 的会话动作走下面那张「家族 + 末段」表**——
 * 会话生命周期的动作路径全都是 `/ai-trade/{groupId}/stop` 这个形状，写成 `/ai-trade/stop`
 * 一条都匹配不上，而 `tsc`/`vitest` 都不会红（这正是本轮 D3 要消灭的那类静默不完整）。
 */
const WRITE_PREFIXES = [
  "/trading/order",
  "/trading/close",       // 同时覆盖 /trading/close-all
  "/trading/close-all",
  "/trading/leverage",
  "/ai-trade/start",
  "/live-auto/group/start",
  "/live-auto/start",
  "/paper/start",
  "/paper/batch/start",
  "/paper/stop-all",
  // 经验库的两个写类路径（E-11）。**必须登记在这张前缀表、而不是下面那张「家族 + 末段」表**：
  // 往 `WRITE_ACTION_SUFFIXES` 里加 `/rebuild` 会让**既有的** `/ai-trade/prior/rebuild` 一并被
  // 重判为写路径——那是在一个与本特性无关的按钮上悄悄改掉超时文案。`/experience/*` 没有路径
  // 参数，前缀表本就是为这种形状准备的。
  // 一次超时的 `replace` 导入（它会先删除既有手工与导入节点）必须说「结果未知」而不是「失败」。
  "/experience/rebuild",
  "/experience/import",
] as const;

/**
 * 带 `{id}` 的会话动作：`/ai-trade/{groupId}/stop`、`/paper/{sessionId}/stop`、
 * `/live-auto/group/{groupId}/member/{instId}/stop`、`/ai-trade/{groupId}/backstop/repair` ……
 *
 * 判据是「三个家族前缀之一 + 末段动作之一」。它们**都会改变真实资金的状态**（停会话要平仓、
 * 兜底自愈要补单），所以超时必须说「结果未知」而不是「失败」。
 * `/ai-trade/backtest/{jobId}/cancel` 之类的作业控制不在其中：末段动作对不上，天然落读类。
 */
const WRITE_ACTION_FAMILIES = ["/ai-trade/", "/live-auto/", "/paper/"] as const;
const WRITE_ACTION_SUFFIXES = ["/stop", "/pause", "/resume", "/end", "/repair"] as const;

/** 去掉 query string，只按路径判定（`?bar=1H` 不该影响超时档位）。 */
function pathOnly(path: string): string {
  const cut = path.indexOf("?");
  return cut >= 0 ? path.slice(0, cut) : path;
}

/**
 * 该路径应当用哪一档超时。
 *
 * @param path 各 `*Api.ts` 传给自己 `request()` 的路径（`/api/py` 之后那段）。
 * @returns 该行自带的 `ms` 档 → `LONG_MS`（重计算）→ `FAST_MS`（其余一律）。
 */
export function timeoutFor(path: string): number {
  const clean = pathOnly(path);
  if (LONG_EXACT_PREFIXES.some((prefix) => clean.startsWith(prefix))) return LONG_MS;
  const segment = LONG_SEGMENT_SUFFIXES.find(
    ({ prefix, suffix }) => clean.startsWith(prefix) && clean.endsWith(suffix),
  );
  if (segment) return "ms" in segment ? segment.ms : LONG_MS;
  return FAST_MS;
}

/** 这条路径是否会**改变服务端状态**（决定超时文案是「失败」还是「结果未知」）。 */
export function isWritePath(path: string): boolean {
  const clean = pathOnly(path);
  if (WRITE_PREFIXES.some((prefix) => clean.startsWith(prefix))) return true;
  return (
    WRITE_ACTION_FAMILIES.some((family) => clean.startsWith(family))
    && WRITE_ACTION_SUFFIXES.some((suffix) => clean.endsWith(suffix))
  );
}

/**
 * 给 `RequestInit` 挂上超时 signal。
 *
 * **调用方自带 `signal` 时不覆盖**：`discoveryAssetApi.ts` 的「立即体检」已经有一个 65 秒的专用
 * 超时且明确要求绝不重试，覆盖它等于把那条深思熟虑的策略悄悄换掉。
 *
 * @param init 原始 `RequestInit`（可为 undefined）。
 * @param ms 超时毫秒数，通常来自 `timeoutFor(path)`。
 */
export function withTimeout(init: RequestInit | undefined, ms: number): RequestInit {
  return { ...init, signal: init?.signal ?? AbortSignal.timeout(ms) };
}

/** 超时是不是这个错误的根因（`AbortSignal.timeout` 抛 `TimeoutError`，手工 abort 抛 `AbortError`）。 */
export function isTimeoutError(err: unknown): boolean {
  const name = (err as { name?: string } | null)?.name;
  return name === "TimeoutError" || name === "AbortError";
}

/**
 * 超时文案。**写类路径必须说「未确认」而不是「失败」** —— 这是本模块里最重要的一条规则。
 *
 * 一个超时的 `POST /api/trading/order` **可能已经成交了**。把它显示成「下单失败」，用户会重下一单
 * ——那是这个仓库最不能犯的错（见提交 `7b55d16`：never let it claim a position was closed when it wasn't）。
 */
export function timeoutMessage(path: string, ms: number): string {
  const seconds = Math.round(ms / 1000);
  return isWritePath(path)
    ? `请求超时（${seconds} 秒）——本次操作结果未知，请刷新持仓/会话状态核对后再决定是否重试。`
    : `请求超时（${seconds} 秒），请检查后端是否响应。`;
}
