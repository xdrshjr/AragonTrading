'use strict';
// 运行期 Agent 凭证装载 (Agent Trading Copilot §3.3)。
// 首启把 resources/agent/agent.env 播种到 userData/agent.env（用户可编辑覆盖），
// 解析 AGENT_* 供 config.backendEnv 注入后端子进程（prod 无需 .env 文件，与 DB_PATH→userData 同构）。

const fs = require('fs');
const path = require('path');
const { app } = require('electron');

function resourceAgentEnv() {
  return path.join(process.resourcesPath, 'agent', 'agent.env');
}

function userAgentEnv() {
  return path.join(app.getPath('userData'), 'agent.env');
}

function parseEnvText(text) {
  const out = {};
  for (const raw of String(text).split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq < 0) continue;
    out[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
  }
  return out;
}

// 首启播种：userData/agent.env 不存在且资源副本存在 → 拷过去（之后用户可改、升级不覆盖）。
function seedUserAgentEnv() {
  const dst = userAgentEnv();
  try {
    if (fs.existsSync(dst)) return dst;
    const src = resourceAgentEnv();
    if (fs.existsSync(src)) {
      fs.mkdirSync(path.dirname(dst), { recursive: true });
      fs.copyFileSync(src, dst);
    }
  } catch {
    // 播种失败不致命：loadAgentEnv 仍会尝试读资源副本。
  }
  return dst;
}

// 解析 AGENT_*：优先 userData 可写副本，回落资源副本；都没有则空对象（后端按"未配置"引导）。
function loadAgentEnv() {
  for (const candidate of [userAgentEnv(), resourceAgentEnv()]) {
    try {
      if (fs.existsSync(candidate)) {
        return parseEnvText(fs.readFileSync(candidate, 'utf-8'));
      }
    } catch {
      // 读失败则尝试下一个候选。
    }
  }
  return {};
}

module.exports = {
  resourceAgentEnv,
  userAgentEnv,
  parseEnvText,
  seedUserAgentEnv,
  loadAgentEnv,
};
