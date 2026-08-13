'use strict';
// 端口常量 + 子进程环境变量组装 (spec §4.5)。
// 固定端口策略 (D3)：与 scripts/dev.py / apps/web / docker-compose 保持一致——
// 后端 18760 / 前端 18761。改动须同步 README「端口同步点」一节（P2-3，第 5 处）。

const BACKEND_HOST = '127.0.0.1';
const WEB_HOST = '127.0.0.1';
const BACKEND_PORT = 18760; // FastAPI / uvicorn
const WEB_PORT = 18761; // Next.js

const BACKEND_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`;
const WEB_URL = `http://${WEB_HOST}:${WEB_PORT}`;
// 后端就绪判据：/api/health 有任意 HTTP 响应即"在听"（含 okx:"down"，spec §4.2 step4）。
const BACKEND_HEALTH_URL = `${BACKEND_URL}/api/health`;

// 就绪轮询超时（ms）。后端首次解压 PyInstaller onedir、前端首次 next dev 冷编译
// 都较慢，故给足余量（P2-6）。/api/health 内含一次 rest.health() OKX 探活，OKX 慢时
// 单次探测可能耗时，轮询间隔与单次探测超时见 process-manager.js。
const BACKEND_READY_TIMEOUT_MS = 60_000;
const WEB_READY_TIMEOUT_MS = 90_000;

/**
 * 代理 env 透传：存在才传（spec §4.5）。OKX 真实数据可达性依赖系统代理
 * （README：本机 Clash 127.0.0.1:7897）。大小写两种都透传，覆盖不同工具的读取习惯。
 */
function proxyEnv() {
  const out = {};
  const keys = [
    'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
    'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
  ];
  for (const k of keys) {
    if (process.env[k]) out[k] = process.env[k];
  }
  return out;
}

/**
 * 后端子进程 env。
 * - PYTHONUTF8 / PYTHONIOENCODING：中文日志不崩（同 dev.py）。
 * - DB_PATH：仅 prod 注入到 userData（资源目录只读，D6）；dev 不设走仓库默认。
 * - AGENT_* / LIVE_*：仅 prod 注入（由 main.js 经 agent-env 解析 userData/agent.env 传入 agentEnv）；
 *   pydantic Settings 从进程 env 读取，prod 无需 .env 文件（dev 经 services/quant/.env 读取）。
 *   LIVE_*（LIVE_AUTO_TRADING_ENABLED / LIVE_TRADING_ENABLED / LIVE_MAX_ORDER_NOTIONAL_USD /
 *   LIVE_DEFAULT_TD_MODE / LIVE_DEFAULT_LEVERAGE）须同 AGENT_* 一并透传，否则打包桌面无法启用实盘下单（C1-6）。
 * - LIVE_AUTO_TRADING_ENABLED：打包链 agent.env 不产出该键、后端默认 False 致「自动交易」开关恒灰
 *   （诊断 desktop-header-right-and-auto-trade-toggle-unresponsive）。故 prod 注入默认 'true' 作为唯一缺失的源，
 *   置于 agentEnv 透传之前——userData/agent.env 的显式值仍可经下方循环覆盖；写类仍受单会话 /
 *   名义上限 / 二次确认 / 审计等既有 guard 约束，config.py 通用默认（关）不变，安全姿态保持。
 * - ALLOW_CACHED_FALLBACK：默认不设（=false），保持诚实默认——OKX 不可达即显式报错，不假数据。
 */
function backendEnv({ mode, dbPath, agentEnv } = {}) {
  const env = {
    ...process.env,
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
    ...proxyEnv(),
  };
  if (mode === 'prod' && dbPath) {
    env.DB_PATH = dbPath;
  }
  // prod 默认开启自动交易总闸（缺失的唯一源）。置于 agentEnv 透传之前，使用户 userData/agent.env 的显式值可覆盖。
  if (mode === 'prod') {
    env.LIVE_AUTO_TRADING_ENABLED ??= 'true';
    env.AI_TRADE_ENABLED ??= 'true';
  }
  // prod：把 AGENT_* / LIVE_* / AI_TRADE_* / DISCOVERY_* 注入后端子进程 env（密钥仅在后端，绝不下发
  // 前端，R3/R4；实盘开关 C1-6）。AI_TRADE_* 为 ai-auto-trading 必改透传（P1-6）；DISCOVERY_* 为
  // strategy-factor-discovery 必改透传（§六）：否则 Settings extra="ignore" 会静默丢弃未透传的
  // DISCOVERY_ENABLED 等键，打包/prod 将无法用 agent.env 覆盖挖掘总闸与配额（漏改即所有
  // DISCOVERY_ env 静默失效）。OKX_ 为 okx-link-resilience 必改透传（§3.5）：不加则打包版
  // **所有** OKX_LINK_* 旋钮（心跳 / 退避 / 熔断 / 连接池 / 补根上限）静默失效——这类漏改
  // 从不报错，本仓库已为它栽过两次。
  if (mode === 'prod' && agentEnv && typeof agentEnv === 'object') {
    for (const [key, value] of Object.entries(agentEnv)) {
      if ((key.startsWith('AGENT_') || key.startsWith('LIVE_') || key.startsWith('AI_TRADE_')
           || key.startsWith('DISCOVERY_') || key.startsWith('OKX_'))
          && value != null) {
        env[key] = String(value);
      }
    }
  }
  return env;
}

/**
 * 前端子进程 env。
 * - PY_BACKEND_URL：决定 /api/py/* 代理目标。dev 下 next dev 启动时重读 next.config.mjs → 有效；
 *   prod 下 standalone 的 rewrites 已在 next build 期烤进 required-server-files.json，运行期注入为
 *   no-op（P1-5），此处仍注入仅作 dev 一致性与防御性冗余；prod 的正确目标由 build:web 构建期烤定。
 *   显式用 IPv4 127.0.0.1（而非 localhost），避免 Windows 解析到 IPv6 ::1 而后端只绑 127.0.0.1（R14）。
 * - PORT / HOSTNAME / NODE_ENV：仅 prod 的 standalone server.js 需要。
 */
function webEnv({ mode } = {}) {
  const env = {
    ...process.env,
    PY_BACKEND_URL: BACKEND_URL,
    ...proxyEnv(),
  };
  if (mode === 'prod') {
    env.PORT = String(WEB_PORT);
    env.HOSTNAME = WEB_HOST;
    env.NODE_ENV = 'production';
  } else {
    // dev：显式置 development，覆盖可能存在的 ambient NODE_ENV=production。
    // 否则 next dev 会告警「non-standard NODE_ENV」并令 CSS/PostCSS(Tailwind) 加载链失效
    // （globals.css 的 @tailwind 不被处理 → Module parse failed）。见 §11 实施发现 3。
    env.NODE_ENV = 'development';
  }
  return env;
}

module.exports = {
  BACKEND_HOST,
  WEB_HOST,
  BACKEND_PORT,
  WEB_PORT,
  BACKEND_URL,
  WEB_URL,
  BACKEND_HEALTH_URL,
  BACKEND_READY_TIMEOUT_MS,
  WEB_READY_TIMEOUT_MS,
  proxyEnv,
  backendEnv,
  webEnv,
};
