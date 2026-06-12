# Aragon Trading — 多策略量化模拟交易平台

基于**欧易（OKX）真实行情**的「左图右策略」量化模拟交易工作台。左侧专业 K 线图（缩放 / 平移 / 日期跳转 / 滚动加载历史），右侧选择交易策略并在左图叠加买卖点 markers 与净值/盈亏折线。本期（Phase 1）仅做**模拟快速交易**（内置 SimBroker 撮合），不接入真实下单。

> 设计文档：`docs/plans/aragon-trading-platform/spec.md`（v2，已评审）。

## 架构

```
apps/web        Next.js 14 (App Router) + React 18 + TS + Tailwind + lightweight-charts v4
                └ BFF：/api/py/* 经 next.config 代理到 Python 后端
services/quant  Python 3.11 + FastAPI：OKX 适配 / 策略引擎 / SimBroker 回测 / 实时模拟 / SQLite
```

- 前端只画图与交互，不持有任何密钥。
- Python 后端对接 OKX 公共 REST（历史 K 线）+ 公共 WebSocket（实时 K 线），运行 5 个内置高频/日内策略与模拟撮合。
- 所有 `ts` 全系统统一为**秒级 UTC**（仅 OKX 适配层做一次毫秒↔秒转换）。

## 内置 5 个策略

1. **长影线反转** `long_shadow_reversal` — 长下影抄底 / 长上影做空
2. **暴涨动量突破** `momentum_breakout` — 累计大涨 + 突破高点 + 放量追势，跟踪止损
3. **双 EMA 交叉剥头皮** `ema_cross_scalper` — 金叉做多 / 死叉做空（建议 5m/15m）
4. **布林带均值回归** `bollinger_reversion` — 破轨 + RSI 极值回归中轨
5. **VWAP + RSI 动量** `vwap_rsi_momentum` — 站上/跌破当日 VWAP + RSI 穿越 50（仅日内）

> 策略可插拔：新增策略 = 新建一个 `services/quant/app/strategies/<key>.py` + `@register_strategy`，并在 `strategies/__init__.py` import 一行，无需改引擎/接口。

## 环境要求

- Node.js ≥ 18.18，pnpm
- Python 3.11
- **网络**：OKX 在部分地区受限。本机检测到 Clash 代理 `127.0.0.1:7897`，后端透传 `HTTP_PROXY/HTTPS_PROXY`。

## 快速开始

### 0) 连通性预检（必须先通过 — 真实数据硬约束）

```bash
cd services/quant
python -m venv .venv
# Windows
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts\check_okx.py        # 应打印 ✓ 取到真实 K 线
```

> 预检失败请先解决网络/代理，再继续。系统**严禁**在 OKX 不可达时以随机/mock 数据冒充真实行情——届时前端会显示明确的「无法连接 OKX」错误态。

### 1) 启动 Python 后端（端口 8000）

```bash
cd services/quant
# Windows（中文日志需 UTF-8）
set PYTHONUTF8=1
.venv\Scripts\uvicorn app.main:app --port 8000 --reload
# 健康检查： http://localhost:8000/api/health  -> {"status":"ok","okx":"up"}
# OpenAPI 文档： http://localhost:8000/docs
```

### 2) 启动前端（端口 3000）

```bash
cd apps/web
pnpm install
pnpm dev
# 打开 http://localhost:3000
```

## 环境变量

复制根目录 `.env.example` 后按需填写：

- 前端 `apps/web/.env.local`：`PY_BACKEND_URL`（默认 `http://localhost:8000`）、`NEXT_PUBLIC_PY_WS_URL`（默认 `ws://localhost:8000`）。
- 后端 `services/quant/.env`：`HTTP_PROXY/HTTPS_PROXY`、`DB_PATH`、`DEFAULT_INST/BAR`、`INITIAL_CAPITAL`、`MAX_PAPER_SESSIONS`、`ALLOW_CACHED_FALLBACK` 等。

> **安全**：本期仅用 OKX 公共行情，**不需要** API 密钥。切勿把任何真实密钥写入仓库；若密钥曾明文出现，请视为已泄露并轮换。`.env` 已被 `.gitignore` 排除。

## 测试

```bash
# 后端
cd services/quant && .venv\Scripts\python -m pytest -q

# 前端
cd apps/web && pnpm test
```

## 主要接口（后端真实路径，前端经 `/api/py/*` 代理）

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查（含 `okx:up\|down`）|
| GET | `/api/instruments` | 可选标的 |
| GET | `/api/candles` | 历史 K 线（真实 OKX，秒级 ts）|
| GET/POST/PUT/DELETE | `/api/strategies[/{id}]` | 策略 CRUD（内置不可删，可"另存为"）|
| POST | `/api/strategies/{id}/backtest` | 触发回测，返回 `{runId, metrics}` |
| GET | `/api/runs/{id}/trades` · `/equity` | 成交明细 / 净值曲线 |
| POST | `/api/paper/start` · `/api/paper/{sid}/stop` | 实时模拟会话 |
| WS | `/api/ws/market` · `/api/ws/paper/{sid}` | 实时 K 线 / 模拟事件流 |

## 免责声明

内置策略与默认参数仅用于**模拟 / 教学**，回测/模拟结果**不构成任何投资建议，不承诺真实盈利**。
