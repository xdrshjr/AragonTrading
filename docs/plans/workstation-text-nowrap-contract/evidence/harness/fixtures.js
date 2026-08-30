// 无头验收用的后端桩：只覆盖工作台渲染路径，其余一律回空。
const NOW = Math.floor(Date.now() / 1000);
const SYMS = [
  "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "DOGE-USDT-SWAP",
  "1000PEPE-USDT-SWAP", "AVAX-USDT-SWAP", "LINK-USDT-SWAP", "MATIC-USDT-SWAP",
];

function instruments() {
  return SYMS.map((instId, i) => ({
    instId, instType: "SWAP", base: instId.split("-")[0], quote: "USDT",
    last: 104235.5 - i * 1000, open24h: 103900 - i * 1000, vol24h: 12345.6,
    volCcy24h: 987654.3, changePct: (i % 2 ? -1 : 1) * (0.0123 + i / 1000),
  }));
}

function candles() {
  const out = [];
  for (let i = 300; i > 0; i -= 1) {
    const base = 104000 + Math.sin(i / 7) * 900;
    out.push({
      ts: NOW - i * 3600, o: +(base).toFixed(1), h: +(base + 240).toFixed(1),
      l: +(base - 260).toFixed(1), c: +(base + 120).toFixed(1), vol: +(10 + (i % 17)).toFixed(2),
    });
  }
  return out;
}

const METRICS = {
  totalReturn: 0.1234, winRate: 0.5231, profitFactor: 1.4412, maxDrawdown: -0.0812,
  sharpe: 1.2043, tradeCount: 1234567, avgPnl: 3.2145,
};

const EXPLAIN = {
  schema: 1, kind: "factor_score", side: "long",
  headline: "复合得分越过进多阈值，且三项确认指标同向",
  summary: "这一句是散文：它必须继续正常换行，绝不能被继承来的 nowrap 截成一行加省略号。多写一些字用来把这段话撑到需要换行的长度，验证 Type D 没有被误伤。",
  metrics: [
    { label: "入场依据", value: "0.6421", hint: "复合得分" },
    { label: "进多阈值", value: "0.3000" },
    { label: "波动率(ATR%)", value: "1.4200%" },
    { label: "持仓周期档", value: "中周期(4H)" },
  ],
};

function trades() {
  return Array.from({ length: 12 }, (_, i) => ({
    ts: NOW - i * 3600, side: i % 2 ? "short" : "long", price: 104235.5 + i,
    qty: 0.5, fee: 0.1234, pnl: (i % 3 - 1) * 123.45, positionAfter: i % 2 ? 0 : 0.5,
    reason: i % 2 ? "exit" : "entry", seeded: false,
    slPrice: 103000.5, tpPrice: 106000.5,
    explain: i % 2 === 0 ? EXPLAIN : undefined,
  }));
}

const STRATEGIES = [
  { id: 1, templateKey: "ma_cross", name: "均线交叉", description: "d", kind: "atomic", params: {}, paramsSchema: {}, enabled: true, builtin: true },
  { id: 2, templateKey: "trend_band_swing", name: "大周期趋势带波段", description: "d", kind: "atomic", params: {}, paramsSchema: {}, enabled: true, builtin: true },
];

const RUNS = Array.from({ length: 6 }, (_, i) => ({
  id: i + 1, strategyId: 1, instId: SYMS[i % SYMS.length], bar: "1H",
  mode: i % 2 ? "paper" : "backtest", status: "done",
  startTs: NOW - 86400 * 30, endTs: NOW, metrics: METRICS,
}));

const POSITIONS = {
  positions: SYMS.slice(0, 3).map((instId, i) => ({
    instId, posSide: i % 2 ? "short" : "long", pos: 1.25, avgPx: 104235.5,
    last: 104512.3, upl: -123.45, uplRatio: -0.0123, lever: 10,
    margin: 1234.56, mgnMode: "cross", liqPx: 90000.5, notionalUsd: 12345.67,
  })),
  accountId: 1,
};

const OPEN_ORDERS = {
  orders: SYMS.slice(0, 4).map((instId, i) => ({
    ordId: `ord-${i}`, clOrdId: `aragon-${i}`, instId, side: i % 2 ? "sell" : "buy",
    posSide: i % 2 ? "short" : "long", ordType: "limit", px: 104235.5, sz: 1.25,
    accFillSz: 0, state: "live", cTime: (NOW - 600) * 1000, lever: 10,
    reduceOnly: false, algoType: null,
  })),
};

const FILLS = {
  fills: SYMS.slice(0, 5).map((instId, i) => ({
    tradeId: `t-${i}`, ordId: `ord-${i}`, clOrdId: `aragon-${i}`, instId,
    side: i % 2 ? "sell" : "buy", posSide: i % 2 ? "short" : "long",
    fillPx: 104235.5, fillSz: 1.25, fee: -0.1234, feeCcy: "USDT",
    ts: (NOW - i * 60) * 1000, pnl: (i % 3 - 1) * 88.88,
    explain: i % 2 === 0 ? EXPLAIN : null,
  })),
};

// 路径（`/api/py` 之后那段）→ 响应体。第一个命中的正则生效。
const ROUTES = [
  [/^\/health/, { status: "ok", version: "test" }],
  [/^\/okx\/connectivity/, {
    ts: NOW, overall: "up", accountState: "up",
    summary: "行情与账户链路均正常，最近一次公共探活 42ms",
    clockOffsetMs: 3, clockSyncedTs: NOW,
    channels: [
      { key: "rest:public", kind: "restPublic", label: "公共行情 REST", state: "up", lastOkTs: NOW, lastErrorTs: null, lastErrorCode: null, lastErrorMessage: null, latencyMs: 42, consecutiveFailures: 0, reconnects1h: 0, resubscribes1h: 0, gapRepairs1h: 0, lastMessageTs: NOW, circuit: "closed", circuitOpenUntilTs: null },
      { key: "ws:business:BTC-USDT-SWAP:1H:chart", kind: "wsBusiness", label: "BTC-USDT-SWAP 1H K 线流", state: "up", lastOkTs: NOW, lastErrorTs: null, lastErrorCode: null, lastErrorMessage: null, latencyMs: 88, consecutiveFailures: 0, reconnects1h: 1, resubscribes1h: 0, gapRepairs1h: 0, lastMessageTs: NOW, circuit: "closed", circuitOpenUntilTs: null },
    ],
  }],
  [/^\/instruments/, instruments()],
  [/^\/candles/, candles()],
  [/^\/strategies/, STRATEGIES],
  [/^\/runs\/\d+\/trades/, trades()],
  [/^\/runs\/\d+/, { id: 1, strategyId: 1, instId: SYMS[0], bar: "1H", mode: "backtest", status: "done", startTs: NOW - 86400 * 30, endTs: NOW, metrics: METRICS }],
  [/^\/runs/, RUNS],
  [/^\/workspace\/watchlist/, { instIds: SYMS, autoTradeInstIds: SYMS.slice(0, 4), activeInstId: SYMS[0] }],
  [/^\/live-auto\/group\/status/, { liveAutoTradingEnabled: false, group: null }],
  [/^\/trading\/positions/, POSITIONS],
  [/^\/trading\/orders/, OPEN_ORDERS],
  [/^\/trading\/fills/, FILLS],
  [/^\/accounts/, { accounts: [{ id: 1, label: "主账户", ipWhitelist: "", permissions: "read,trade", createdTs: NOW, testedTs: NOW, testOk: true }], defaultId: 1 }],
  [/^\/ai-trade/, {}],
  [/^\/intel/, { items: [], stats: null }],
];

function respond(path) {
  for (const [re, body] of ROUTES) if (re.test(path)) return body;
  return [];
}

module.exports = { respond, SYMS, METRICS };
