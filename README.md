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

### 一键启动（推荐）

仓库根目录下，一条命令完成预检 + 依赖就绪 + 同时拉起前后端（跨平台纯 Python 编排，F1）：

```bash
pnpm setup     # 建 venv + 装 Python 依赖 + pnpm install（首次）
pnpm preflight # OKX 连通性预检（真实数据硬约束）
pnpm dev       # 预检 → 端口检查 → 并发起后端(:18760) + 前端(:18761)，聚合日志，Ctrl-C 双进程优雅退出
pnpm test      # 后端 pytest（不含 network）+ 前端 vitest
```

> 等价底层命令：`python scripts/dev.py {setup|preflight|up|test}`（`up` 支持 `--skip-preflight`）。
> 端口刻意选用不常用值（后端 18760 / 前端 18761），规避与常见开发端口冲突；启动前会先做端口占用预检，被占用即给出定位/释放命令而非崩在半路。
> Ctrl-C 时编排器会向前后端各发 CTRL_BREAK（Win）/SIGTERM（POSIX）整组优雅停止，超时再强杀整棵进程树，杜绝 `uvicorn --reload` 子进程残留占端口。

### 真实数据端到端校验（F2）

```bash
# 经代理直连真实 OKX，串起 candles → 回测 全链路并断言（退出码 0=PASS）
services/quant/.venv/Scripts/python scripts/smoke_e2e.py     # Windows
# 或后端可选网络测试（默认排除，opt-in）：
cd services/quant && .venv\Scripts\python -m pytest -m network
```

`GET /api/health` 现额外返回 `okxLatencyMs`（往返时延）、`lastOkTs`（最近成功取数秒级 UTC）、`wsConfigured`（WS 端点是否配置）；前端 Header 连接点 tooltip 会展示时延与最近取数时间。

### 新增功能用法

- **多策略对比（F3）**：顶部模式段切到「对比」→ 右侧勾选 2–12 个策略 → 「运行对比」，左图叠加多条**归一化净值曲线**（`净值/初始-1`），右侧榜单可按收益 / 夏普 / 胜率 / 回撤 / 交易数排序，点「打开」下钻单策略详情。
- **运行历史（F4）**：右侧底部「历史」Tab 列出近期 run（可按 全部 / 回测 / 模拟 过滤），点击任意行复现其净值与成交。
- **历史回放加速模拟（F5）**：模式段切到「实时」→ 数据来源选「历史回放」+ 倍速（1× / 4× / 16×），按真实近端 OKX K 线加速驱动模拟撮合，数秒内即见 markers / 净值推进，结束自动停止。**回放数据全部为真实 OKX 历史（非合成），仅加速演示。**

- **策略与因子挖掘**：最左侧导航第三项「策略与因子挖掘」→「挖掘任务」→「新建挖掘」，
  选标的 / 周期 / 数据窗口与每轮候选数（第一步）、阈值与 LLM 用量（第二步）后开始。后端起一个
  可 **24 小时长跑**的发现循环，每轮按「生成 → 审查 → 验证 → 入库」四步推进：

  - **生成**：变异 / 交叉 / 参数扰动 / 随机探索 / LLM 机制引导五种操作产出因子表达式候选，
    预算每 10 轮按入库率与去重率自适应调整；
  - **审查**：九条确定性规则挡掉退化结构、跨量纲运算、复杂度越界与重复表达式，
    再抽样交 LLM 子代理做经济学含义精判（**只有否决权**）；
  - **验证**：**硬编码**的 11 条联合过滤（样本内 RankIC / IR / t 值 / 分段稳定性、
    **样本外同号且保留强度**、分位单调性、成本后价差、换手代理、双通道相关性去重、
    多重检验惩罚）—— 达标与否**绝不由大模型说了算**；
  - **入库**：达标候选进「候选库」，用户勾选后一键入库到「我的因子」，
    随后可在因子实验室评估、进权重训练、被组合策略引用。

  「实时监控」页有漏斗、五维来源堆叠、入库率曲线、被冻结的结构骨架与 LLM 调用轨迹；
  「候选库」可按状态 / 机制族 / 拒绝原因筛选并查看表达式树、中文解释与样本内外双份指标；
  「成果库」记录已入库 / 待入库 / 已退化三组，全程可回溯。任务可暂停 / 恢复 / 停止，
  进程重启后按 `autoResume` 从检查点续跑（续跑前先重建候选池，重建失败即停在 `paused`，
  绝不带残缺池开跑）。行情不可达时任务转 `paused` 并明确提示，**绝不用合成数据顶替**。
  相关配置见 `.env.example` 的 `DISCOVERY_*` 段（总闸 `DISCOVERY_ENABLED` 关闭后，
  写类端点 403 且重启不会拉起任何后台循环）。

  > ⚠️ 挖掘产出的一切指标均为**历史样本统计**，不构成任何收益预期或投资建议。
  > 候选详情里的「入库时试验数」值得你亲自看一眼：它越大，说明该因子是在越多次尝试中被
  > 选出的，统计上越可能沾了多重检验的光（系统已按 log10(试验数) 抬高 IR 门槛）。

  **成果资产与持续体检**（第 6 个分区「成果资产」）：候选一旦被判「通过全部过滤条件」，
  就按**语义身份**登记成一条跨任务的资产——同一条成果在三个任务被挖到只占一行，携带全部溯源，
  且**自持** genome、冻结方向、挖掘窗口口径与登记时的完整指标与阈值，因此任务被删掉之后
  详情、入库与体检照常可用。随后一个独立于任务生命周期的后台守护会周期性地在
  **挖掘窗口右端之后新产生的那一段数据**上，用**与挖掘完全同一把尺子**重评每条资产，
  产出健康时间线与留存度。这是本项目里唯一的**真样本外**证据：那些数据在挖掘时还不存在，
  任何形式的过拟合都触不到它。姿态上诚实优先——新增样本不够一律「暂不评级」而不是给一个勉强的
  绿灯，一次网络抖动不写进健康时间线，降级**只标注不代劳**（要不要停用是你的显式动作）。
  相关配置见 `.env.example` 的 `DISCOVERY_HEALTH_*` 段（15 个键，含总闸
  `DISCOVERY_HEALTH_ENABLED`；关闭后总览卡会明说「健康状态不会更新」）。

- **AI 自动交易的标的市场情报**：开启 AI 自动交易后，左侧面板每张标的卡片上 hover 出「市场情报」
  按钮，点开即是该标的的三层情报库（超短 / 中期 / 长期 × 市场 / 情绪 / 宏观 / 链上 / 项目 / 风险）。
  一个**与交易 Agent 完全分开、没有任何交易工具**的情报 Agent 定期检索公开信源，把结果摘要成几句话
  的短情报，过服务端的七道真实性闸后入库；另有整理轮自主升降层、合并与清理，第三个不知道任何持仓
  方向的核查 Agent 定期回头证实或证伪。全部情报**持久化**，关掉自动交易之后照样可查可管可增删改。

  **每一条情报都可以点开它的原文引证**：后端会第一方把被引用的网页抓回来、抽出正文喂给情报 Agent，
  并要求它从正文里**逐字复制**一段原文；服务端做 NFKC + 空白折叠 + 引号归一后**逐字比对**，比中的
  那一段原文随情报一起落盘——**页面日后失效也照样读得到**（引文不依赖网页缓存）。卡片上的
  「原文引证 N」展开即读；没有引证时显示一枚「无引证」标记，并说清是哪一种原因（功能已关 / 没抓到
  正文 / 模型没给 / 给了没比中），**由后端下发，前端不猜**。

  两把逐层收窄的运维单键（`.env.example` 的 `AI_TRADE_INTEL_*` 段，共 64 项）：
  `AI_TRADE_INTEL_READER_ENABLED=false` ⇒ **不发任何出网抓取请求**，情报仍会采集但只依据搜索摘要；
  `AI_TRADE_INTEL_QUOTE_MODE` 三态 —— `off`（不要求引文，逐字节回到没有本功能的样子）/
  `soft`（**默认**，记录但不强制，准入率相对上一轮只可能上升）/ `strict`（必须附可核对引文，
  且要求 reader 开着）。收紧到 `strict` 之前先看弹窗顶部与设置卡上的**引文命中率**：低于 70%
  不建议开启——那时准入率归零的样子，与「今天真的没新闻」在界面上分不出来。

  抓取只去检索源返回的地址，经语法 + 解析两段 SSRF 闸与逐跳复检，请求**零 Cookie、零 Referer、
  零本地事实**；抓回的正文只进不可信语料区，**一个字都不会进入交易决策**。情报本身也是
  advisory-only：它进 prompt，但不参与任何确定性门禁、不改变任何阈值。

  > ⚠️ 升级到本版本后的**第一个整理轮**会有一次集中降层：一处既有缺陷（被核查过的已排期条目
  > 会永久退出失效清理）已在本版本修复，那些一直「侥幸活着」的过期预告会被一次性降层或归档。
  > 这是修复的正当后果，条数会记在该标的的「整理记录」里。

### 迭代 3 收尾增强

- **默认视图实时行情（F1）**：回测 / 对比模式下左图最右一根 K 线随真实 OKX 行情**实时跳动**，工具栏显示实时最新价与「实时流」状态脉冲点（接通既有 `/api/ws/market`，纯展示，不改撮合）。OKX 不可达时不连、不刷屏。
- **缓存回退诚实标注（F2）**：`/api/candles` 经响应头 `X-Aragon-Data-Source: live|cache`（+ `X-Aragon-Stale`）标注数据来源。**默认配置**下 OKX 不可达即显式 502、绝不假数据；仅当运维显式开启 `ALLOW_CACHED_FALLBACK=true` 且命中缓存时返回**真实但过期**的历史，前端醒目横幅「缓存样例 · 非实时」，杜绝静默冒充实时。`/api/health` 增 `cachedFallbackEnabled` 供前端预知。
- **选择态与主题持久化（F3）**：标的 / 周期 / 策略 / 模式 / 主题落 `localStorage`（key `aragon-ui`），刷新后恢复上次工作上下文；恢复到已删除策略时安全回落第一个。**不持久化**运行期句柄（`runId` / `paperSessionId`）。
- **成交 / 净值导出 CSV（F4）**：右侧「绩效指标」/「成交明细」Tab 一键导出当前 run 的净值曲线 / 成交明细为 CSV（UTF-8 + BOM，字段 RFC4180 转义，可被 Excel / Pandas 正确解析）。

### 容器化一键运行（F5）

```bash
docker compose up --build      # 拉起 web:18761 + quant:18760
docker compose down            # 停止并清理
# 浏览器打开 http://localhost:18761 ；后端健康检查 http://localhost:18760/api/health
```

> **真实数据硬约束**：容器内**仍需能连通 OKX**（compose 透传 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY`），否则按硬约束**显式报错、绝不以假数据兜底**。`NEXT_PUBLIC_PY_WS_URL` 默认 `ws://localhost:18760`（**仅同机浏览器**有效；远程部署须改为宿主可达地址并作为 build-arg 传入）。SQLite 数据持久化在命名卷 `aragon-db`。
>
> CI（`.github/workflows/ci.yml`）在 push / PR 上跑**后端 pytest（不含 network）+ 前端 tsc / vitest / build**；CI 只跑离线用例，真实数据校验仍以 `scripts/smoke_e2e.py` / `pytest -m network` 为准。

### 0) 连通性预检（必须先通过 — 真实数据硬约束）

```bash
cd services/quant
python -m venv .venv
# Windows
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts\check_okx.py        # 应打印 ✓ 取到真实 K 线
```

> 预检失败请先解决网络/代理，再继续。系统**严禁**在 OKX 不可达时以随机/mock 数据冒充真实行情——届时前端会显示明确的「无法连接 OKX」错误态。

### 1) 启动 Python 后端（端口 18760）

```bash
cd services/quant
# Windows（中文日志需 UTF-8）
set PYTHONUTF8=1
.venv\Scripts\uvicorn app.main:app --port 18760 --reload
# 健康检查： http://localhost:18760/api/health  -> {"status":"ok","okx":"up"}
# OpenAPI 文档： http://localhost:18760/docs
```

### 2) 启动前端（端口 18761）

```bash
cd apps/web
pnpm install
pnpm dev
# 打开 http://localhost:18761
```

## 环境变量

复制根目录 `.env.example` 后按需填写：

- 前端 `apps/web/.env.local`：`PY_BACKEND_URL`（默认 `http://localhost:18760`）、`NEXT_PUBLIC_PY_WS_URL`（默认 `ws://localhost:18760`）。
- 后端 `services/quant/.env`：`HTTP_PROXY/HTTPS_PROXY`、`DB_PATH`、`DEFAULT_INST/BAR`、`INITIAL_CAPITAL`、`MAX_PAPER_SESSIONS`、`ALLOW_CACHED_FALLBACK`、`SECURITY_ALLOWED_ORIGINS` 等。

> **安全**：切勿把任何真实密钥写入仓库；若密钥曾明文出现，请视为已泄露并轮换。`.env` 已被 `.gitignore` 排除。
> 另需知悉：实盘凭据（`api_key` / `secret_key` / `passphrase`）目前**以明文存在库内**，
> 因此 `%APPDATA%/aragon-desktop/aragon.db` 这个文件本身等同于凭证，请按凭证的规格保管它。

## 安全 · 本机 API 的信任边界

后端只绑 `127.0.0.1`，所以局域网够不着它。但**回环绑定挡不住用户自己浏览器里的任意网页**：
一个广告位或被投毒的第三方站点，可以直接对 `http://127.0.0.1:18760/api/trading/close-all`
发跨源 POST。这里必须点破一个常见误解——**CORS 保护的是「读」，不是「执行」**：
浏览器只在拿不到 `Access-Control-Allow-Origin` 时**隐藏响应**，请求本身早就到达服务端并执行完了。
而 `/api/trading/*` 的 8 条路由用库内明文密钥向 OKX 下**真实委托**。

因此后端有两层（都不是鉴权，是边界）：

| 层 | 作用 | 不合规时 |
|---|---|---|
| `OriginGuardMiddleware`（纯 ASGI，装在 CORS 外层） | 拦住**执行**：跨源请求不进路由、不碰 DB、不碰 OKX | HTTP `403 ORIGIN_NOT_ALLOWED` |
| 9 条 WS 路由的 `guard_ws_origin` | WebSocket **完全不过 CORS**，且响应可读——不守就等于把实盘会话交给任意网页 | `accept()` 之前即 `close(1008)`，一帧不发；因为握手还没完成，浏览器/客户端看到的是一次 **HTTP 403 握手拒绝**（连接从未建立） |

判据只有两态，很干净：

- **`Origin` 缺失 → 放行。** 浏览器的非 GET/HEAD `fetch`/XHR 与 WS 握手**必带** `Origin`，
  所以「没有 `Origin`」可靠地等价于「不是浏览器」（`scripts/smoke_e2e.py`、
  `desktop/scripts/verify-dist.mjs` 的 node http 走的就是这条）。
- **`Origin: null` → 拒绝。** 它不是「本地文件」的同义词，而是**不透明源**：带 `sandbox`
  且不含 `allow-same-origin` 的 iframe 发的就是它，而这样的 iframe 任意网页三行 HTML 就能造出来。
  代价是**用 `file://` 打开的本地调试页调不通本机 API**——这是有意的取舍，本地调试请用 http 页面。

**排障**：如果界面打得开但点什么都没反应、DevTools 里全是 403 `ORIGIN_NOT_ALLOWED`，
说明你的前端入口不在白名单里（最常见的原因是改过前端端口）。白名单默认值是
`http://localhost:18761` 与 `http://127.0.0.1:18761`，改法是设 `SECURITY_ALLOWED_ORIGINS`
（逗号分隔，**完全替换**默认，不接受 `*` 与 `null`）：

- dev：写进 `services/quant/.env`；
- 打包桌面端：写进 `%APPDATA%/aragon-desktop/agent.env`（经 `SECURITY_` 前缀白名单透传给后端进程 —— 打包后端**不读任何 `.env`**）；
- 容器：`docker-compose.yml` 的 `quant` 服务已透传同名变量（远程部署时那是唯一入口，因为浏览器地址栏里的 host 不再是 `localhost`）。

改前端端口时，`desktop/README.md` §五「端口同步点」的第 6 处指的就是这份白名单。

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
| GET | `/api/health` | 健康检查（含 `okx:up\|down`、`cachedFallbackEnabled`）|
| GET | `/api/instruments` | 可选标的 |
| GET | `/api/candles` | 历史 K 线（真实 OKX，秒级 ts；响应头 `X-Aragon-Data-Source: live\|cache`）|
| GET/POST/PUT/DELETE | `/api/strategies[/{id}]` | 策略 CRUD（内置不可删，可"另存为"）|
| POST | `/api/strategies/{id}/backtest` | 触发回测，返回 `{runId, metrics}` |
| GET | `/api/runs/{id}/trades` · `/equity` | 成交明细 / 净值曲线 |
| POST | `/api/paper/start` · `/api/paper/{sid}/stop` | 实时模拟会话 |
| WS | `/api/ws/market` · `/api/ws/paper/{sid}` | 实时 K 线 / 模拟事件流 |

## 桌面端（Electron）

可把整套 Web 应用封装成**双击即用**的桌面应用（自带 Python 后端可执行体 + Next 服务，目标机无需预装
Node / Python / pnpm）。全部产物集中在 `desktop/`，对 `apps/web` / `services/quant` **零源码改动**。

```powershell
pnpm setup                 # 前置：建 venv + 装依赖（仓库根，一次性）
cd desktop ; npm install
npm run dev                # 拉起 Electron 桌面调试窗口（自动起后端+前端+DevTools），而非浏览器
npm run smoke              # 快速打包冒烟（免安装目录，先 build 再 --dir）
npm run dist               # 全量打包 → desktop/dist/（Windows NSIS 安装包 + portable）
npm run release:check      # 打包 → 端到端发布验收（verify:dist：断言真实 OKX 数据 + 前端代理 + 无残端口）
```

> 详见 `desktop/README.md`；设计文档 `docs/plans/desktop-electron-packaging/spec.md`（v2）。
> Windows 为一等公民；PyInstaller 不能交叉编译，mac/linux 需各自平台构建。真实数据硬约束不变。
> 发布前用 `npm run verify:dist` 机器复验「确保可用 / 数据真实可用」（验收方案见 `docs/plans/desktop-release-acceptance/spec.md`）。

## 免责声明

内置策略与默认参数仅用于**模拟 / 教学**，回测/模拟结果**不构成任何投资建议，不承诺真实盈利**。
