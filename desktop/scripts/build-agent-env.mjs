// build:agent-env (Agent Trading Copilot §3.3)：把 services/quant/.env 的 AGENT_* 拷成
// desktop/resources/agent/agent.env（打包用，gitignored）。
//
// 凭证链失败行为（P1-C）：源 .env 缺 AGENT_API_KEY 时**显著告警**但仍产出含占位的 agent.env，
// 使桌面端首启即生成可编辑的 userData/agent.env，避免静默打出"无密钥"包。verify-dist 断言其存在。

import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPTS_DIR = dirname(fileURLToPath(import.meta.url));
const DESKTOP = dirname(SCRIPTS_DIR);
const REPO = dirname(DESKTOP);
const SRC_ENV = join(REPO, 'services', 'quant', '.env');
const OUT_DIR = join(DESKTOP, 'resources', 'agent');
const OUT_ENV = join(OUT_DIR, 'agent.env');

// 仅拷 AGENT_* 键（绝不携带 OKX_*/代理等无关键，避免泄露面扩大）。
const AGENT_KEYS = [
  'AGENT_PROVIDER',
  'AGENT_BASE_URL',
  'AGENT_API_KEY',
  'AGENT_MODEL',
  'AGENT_ANTHROPIC_VERSION',
  'AGENT_USER_AGENT',
  'AGENT_MAX_TOKENS',
  'AGENT_TEMPERATURE',
  'AGENT_TOP_P',
  'AGENT_PROXY',
  // 加固增强（iteration 2）：LLM 传输层重试 + 断连 drain 超时（缺省回落 config.py 默认值）。
  'AGENT_MAX_RETRIES',
  'AGENT_RETRY_BASE_MS',
  'AGENT_RETRY_MAX_MS',
  'AGENT_DRAIN_TIMEOUT_SECONDS',
  // 韧性流式（iteration 3）：run/socket 解耦续流 + 心跳保活（缺省即不写、回落 config.py 默认值）。
  'AGENT_DETACH_GRACE_SECONDS',
  'AGENT_HEARTBEAT_SECONDS',
  'AGENT_EVENT_BUFFER_SIZE',
];

function parseEnv(text) {
  const out = {};
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq < 0) continue;
    out[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
  }
  return out;
}

function main() {
  let parsed = {};
  if (existsSync(SRC_ENV)) {
    parsed = parseEnv(readFileSync(SRC_ENV, 'utf-8'));
  } else {
    console.warn(`[build-agent-env] ⚠ 源 .env 不存在：${SRC_ENV}（将产出占位 agent.env）`);
  }

  const picked = {};
  for (const key of AGENT_KEYS) {
    if (parsed[key] != null) picked[key] = parsed[key];
  }
  // 合理占位（缺省也能跑出"未配置"引导态，而非崩溃）。
  picked.AGENT_PROVIDER = picked.AGENT_PROVIDER || 'anthropic';
  picked.AGENT_BASE_URL = picked.AGENT_BASE_URL || 'https://api.kimi.com/coding';
  picked.AGENT_MODEL = picked.AGENT_MODEL || 'kimi-k2.6';
  if (!picked.AGENT_API_KEY) {
    picked.AGENT_API_KEY = '';
    console.warn(
      '[build-agent-env] ⚠⚠ 源 services/quant/.env 缺 AGENT_API_KEY：' +
        '打出的桌面包不含密钥，用户需安装后在 userData/agent.env 填写。',
    );
  }

  mkdirSync(OUT_DIR, { recursive: true });
  const lines = ['# Aragon Trading Agent 凭证（打包产物；用户可在 userData/agent.env 覆盖修改）'];
  for (const [key, value] of Object.entries(picked)) lines.push(`${key}=${value}`);
  writeFileSync(OUT_ENV, `${lines.join('\n')}\n`, 'utf-8');

  const note = picked.AGENT_API_KEY ? '含密钥' : '无密钥占位';
  console.log(`[build-agent-env] ✓ 写 ${OUT_ENV}（${Object.keys(picked).length} 项，${note}）`);
}

main();
