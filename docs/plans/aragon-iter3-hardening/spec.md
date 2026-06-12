# Aragon Trading — 迭代 3 生产化收尾方案 (spec, iteration 3)

- **Feature**: `aragon-iter3-hardening`
- **文档版本**: v2（迭代 3 · Subtask #1 设计评审完成；P0=0、P1/P2 已就地修复或登记，详见「评审记录」）
- **作者 / 角色**: Solution Architect (Anthropic Engineering)
- **创建日期**: 2026-06-12
- **迭代**: Iteration 3 / 3（**最后一轮**）
- **全局目标**: 完成项目开发，确保可用，数据真实可用
- **基线**: 迭代 1 已交付并提交（commit `414c28c`）+ 迭代 2 增量增强已交付并提交（commit `f961e7f`）的 monorepo（`apps/web` Next.js 14 + `services/quant` FastAPI）。前序设计见 `docs/plans/aragon-trading-platform/spec.md`（v2）与 `docs/plans/aragon-iter2-enhancements/spec.md`（v2）。
- **关系声明**: 本文档**只规划在迭代 1 + 迭代 2 基线之上的增量收尾**，不重述、不推翻已交付的架构、接口语义、数据模型、5 策略逻辑、SimBroker 撮合内核与回测/回放管线。迭代 1 v2 的 **5 条放行条件**（① 真实数据无假数据 ② 凭据不入库 ③ 全系统秒级 UTC ts ④ 锁 `lightweight-charts@^4.2` ⑤ SimBroker·VWAP·注册表合并规则）在本迭代**继续生效、不得回退**。
- **本轮主题**: 因核心闭环（左图右策略 / 5 策略 / 真实 OKX 数据 / 回测 / 对比 / 历史 / 回放）已具备，迭代 3 作为收尾轮聚焦三件事——**(A) 把已写好却"接而未用"的真实实时行情通道接通**、**(B) 把缓存回退这一诚信缺口堵上**、**(C) 补齐最后一公里可用性与可交付性（持久化 / 导出 / 容器化 / CI）**。全部为**纯增量**，不动 5 策略、SimBroker、DB schema。

---

## 评审记录（Review Notes · Subtask #1）

> 评审人：Senior Reviewer（Anthropic Engineering）。方法：对照「可行性 / 完整性 / 一致性 / 规模适配」四维 + 迭代 1 v2 的 5 条放行条件，**逐文件核对源码落点**（已实读 `apps/web/lib/ws.ts`、`app/page.tsx`、`lib/store.ts`、`lib/api.ts`、`lib/types.ts`、`lib/format.ts`、`next.config.mjs`、`components/Chart/ChartToolbar.tsx`、`components/Strategy/StrategyPanel.tsx` 与后端 `app/api/market.py`、`app/main.py`、`app/api/ws_market.py`、`app/okx/ws_client.py`、`app/config.py`、`pyproject.toml`、`tests/conftest.py`、`requirements.txt`）。
>
> **基线核对结论**：方案引用的全部文件 / 签名 / 行号**真实存在且匹配**——`marketWS`@`ws.ts:74`、`page.tsx` 确实只连 `paperWS` 从未连 `marketWS`、`/candles` 缓存回退@`market.py:56-62`、`main.py` CORS 确无 `expose_headers`、`ws_client.py:108` 确实回调 `confirm=False` 的成形根（F1「最右一根实时跳动」前提成立）、`store.ts` 裸 `create` 无持久化、`StrategyPanel` 默认选中@`35-39` 且已含 `trades` query、`Trade/EquityPoint/Candle` 字段与 `fmtPrice` 齐备、`config.py:47` `allow_cached_fallback=False`、`services/quant/requirements.txt` 与 `tests/conftest.py` 均在。设计**纯增量、不回退 5 条放行条件**，方向正确。
>
> **总览**：P0 = 0（无阻断项）；P1 = 4（C1–C4，已就地修复）；P2 = 5（C5–C9，C5/C7 已就地修复，C6/C8/C9 登记并接受）。

| # | 维度 | 严重级 | 位置 | 问题 | 处置 |
|---|---|---|---|---|---|
| C1 | 可行性 / 正确性 | **P1** | §4.2 / R4 | F2 把 `expose_headers` 当作前端读到 `X-Aragon-Data-Source` 的**关键机制**。但前端走**同源** `/api/py/*`（Next `rewrites` 服务端代理），同源响应**不触发 CORS**，`expose_headers` 在该路径上**不生效也非必要**；真实依赖是「Next rewrite 透传上游自定义响应头」（成立）。误判会让 Subtask #2 验错对象、白排查 CORS。 | 已修：§4.2 / R4 改述真实机制（同源代理可读头 + Next 透传上游头），`expose_headers` 降为「直连兜底」；§8.1 要求**经 `/api/py/*` 代理路径**断言响应头。 |
| C2 | 完整性 | **P1** | §4.3 / R7 | F3 称 persist「客户端首帧前恢复、不引入 hydration 报错」不准确：Next App Router 先以**默认 state** SSR 客户端组件，浏览器再以持久化 state 水合 → 工具栏 `instId/bar/mode` 的服务端 HTML 与客户端首帧分歧 → React **hydration 不一致告警**。 | 已修：§4.3 增 `skipHydration:true` + 挂载后 `useAppStore.persist.rehydrate()`，接受「默认→恢复」一帧闪烁换零告警；R7 同步。 |
| C3 | 可行性 | **P1** | §4.5 / §5.1 / R5 | F5 构建路径未锁死：(a) 仓库**无根 `pnpm-lock.yaml`**，锁文件在 `apps/web/pnpm-lock.yaml`，所有 pnpm 命令须 `-C apps/web` / 构建上下文 = `apps/web`；(b) 根有 `pnpm-workspace.yaml`，`output:"standalone"` 在 monorepo 下可能把 `server.js` 产到**嵌套路径**，`CMD ["node","server.js"]` 会 `MODULE_NOT_FOUND`。（后端依赖 `services/quant/requirements.txt` 经核**已存在**，该项无误。） | 已修：§4.5 / §5.1 锁死构建上下文与锁文件，要求实测 standalone `server.js` 路径再定 `CMD`；R5 扩列该失败模式。 |
| C4 | 完整性 | **P1** | §8.1 | F2 缓存回退测试漏掉最难一步：`get_settings()` 为 `@lru_cache` 且 `conftest.py` 在导入期固定 `ALLOW_CACHED_FALLBACK=false`，测试既翻不动该开关、也未交代如何**预置缓存**，照搬会写出跑不通的用例。 | 已修：§8.1 明确 `cache_clear()`+env 覆盖（或 monkeypatch `app.api.market.get_settings`）与「先 upsert 预置 `candles_cache` 再令假客户端抛 `OKXError`」的装配步骤。 |
| C5 | 完整性 | P2 | §4.1 | F1 切标的 / 周期时仅清 `older/live/forming`，未重置 `lastPrice/dataSource`，工具栏会短暂显示上一标的旧价（下一 tick 自愈）。 | 已修：§4.1 在既有重置 effect 内补 `setLastPrice(null)` / `setDataSource` 复位。 |
| C6 | 一致性 | P2 | §4.1 | F1 复用 `setLive` 写入「市场 WS 确认根」，与 paper 自身确认根**共享同一 `live`**——隐式共享态。 | 登记：§4.1 注明为**有意共享**（同 (inst,bar) 下都是真实确认根、去重升序后等价），靠 `mode!=="paper"` 防双订阅（R3）。 |
| C7 | 完整性 | P2 | §4.4 | F4「导出净值 CSV」按钮未规定空数据禁用；paper/compare 下 `equity` 缓存可能为空，点了导出空文件。 | 已修：§4.4 要求 `disabled={!equityQ.data?.length}`，与成交按钮一致。 |
| C8 | 规模适配 | P2 | §4.5 / §8.2 | F5 全量验收（`docker compose up` 跑通 + 容器内 OKX 可达 + CI 全绿）在开发沙箱可能无法实测（Docker / 真实网络非常备）。 | 登记：§3 已允许 P2 受限时降级为「仅交付资产」并入 §11；保持纯增量、零业务侵入。 |
| C9 | 完整性 | P2 | §4.2 | F2 仅主历史拉取用 `candlesWithMeta`，左滚 `loadMore` 静默回退缓存时横幅不反映（左滚本就是历史，影响小）。 | 登记：可接受；横幅语义限定「当前主视图新鲜度」。 |

---

## 1. Overview（概述）

迭代 1 与迭代 2 已经把 "Aragon Trading" 从 0 建成一个数据真实、闭环完整的多策略量化模拟交易工作台：左侧 `lightweight-charts` 专业 K 线（缩放 / 平移 / 日期跳转 / 滚动回溯），右侧可在 5 个内置高频 / 日内策略间切换并即时回测、横向对比（归一化净值叠加榜单）、浏览运行历史、以"历史回放加速"演示"模拟快速交易"；后端 FastAPI 跑可插拔策略注册表、SimBroker 撮合、回测器、绩效、SQLite 持久化与实时 / 回放 paper 会话，已用真实 OKX 数据端到端复验、后端 27 项 + 网络可选 1 项 pytest、前端 vitest / tsc / `next build` 全绿。换言之，**功能性目标已基本达成**。

但对照全局目标"**完成项目开发，确保可用，数据真实可用**"，逐文件审阅现有源码后，仍能客观地定位到三类**收尾级**缺口，且每条都已在代码中找到确凿落点：(1) **真实"实时"数据接而未用**——后端 `/api/ws/market` 路由（`app/api/ws_market.py`）与前端 `marketWS()`（`apps/web/lib/ws.ts:74`）都已实现并可工作，但 `apps/web/app/page.tsx` 仅在 `mode==="paper"` 分支连了 `paperWS`，**从未调用 `marketWS`**——于是默认（回测 / 对比）视图的 K 线是一次性拉取的**静态历史**，最右一根不会随真实行情跳动，作为"交易工作台"少了"活"的真实感。(2) **缓存回退是一处诚信缺口**——`app/api/market.py:56` 在 `ALLOW_CACHED_FALLBACK=true` 时静默返回本地缓存 K 线，响应体与实时**完全无法区分**，代码注释自陈"前端须显著标注'非实时'"却**没有任何信号**透传给前端，违背"数据真实可用"应有的诚实性。(3) **最后一公里可用性 / 可交付性不足**——选择态（标的 / 周期 / 策略 / 模式 / 主题）刷新即丢（`lib/store.ts` 无持久化），用户无法导出"实际交易情况与收益"做离线分析，且整套系统只能靠手动 venv + pnpm 起，缺容器化与 CI，"确保可用 / 完成项目开发"在可交付维度仍有欠缺。

因此迭代 3 规划 **5 个纯增量收尾特性**：**F1 默认视图实时行情接入**（接通已有 `marketWS` / `/ws/market`，让最右一根真实跳动 + 实时价读数）、**F2 缓存回退诚实标注**（后端经响应头透传 `live|cache` 来源，前端醒目标注"缓存样例·非实时"）、**F3 选择态与主题持久化**（zustand `persist` 落 localStorage）、**F4 成交 / 净值导出 CSV**（纯函数 + 一键下载）、**F5 容器化 + CI**（Dockerfile / docker-compose / GitHub Actions）。所有特性复用既有契约（`marketWS`、`/candles`、`api.request`、`run`/`equity`/`trades` 序列化、`STRATEGY_REGISTRY`），**不改 DB schema、不改 SimBroker、不改 5 策略、不破坏任何既有接口语义**，且新增代码同样**严禁以假数据冒充真实行情**。

---

## 2. Goals / Non-Goals（迭代 3）

### Goals
1. **真实实时可见**：默认（回测 / 对比）视图接通真实 OKX 实时行情，最右一根 K 线随真实成交跳动，工具栏显示实时最新价与 WS 连接态（F1）。
2. **数据来源诚实**：当且仅当启用缓存回退（非默认）返回缓存数据时，后端经响应头明确标注来源，前端醒目提示"缓存样例·非实时（真实历史，非最新）"，杜绝"静默以缓存冒充实时"（F2）。
3. **可用性**：标的 / 周期 / 策略 / 模式 / 主题刷新后保持（localStorage 持久化），重开即恢复上次工作上下文（F3）。
4. **可分析**：一键把当前 run 的成交明细与净值曲线导出为 CSV，便于用户"查看不同策略实际的交易情况以及收益"（F4）。
5. **可交付**：`docker compose up` 一键拉起前后端容器；GitHub Actions 在 push / PR 上跑后端 pytest（不含 network）+ 前端 tsc / vitest / build，守护回归（F5）。
6. 全程不违反 5 条放行条件；新增路径不引入任何假数据。

### Non-Goals（迭代 3 不做）
- 不接入真实下单 / 真实资金（与前序一致，Phase 1 仅模拟）。
- 不引入多用户账号 / 鉴权 / 服务端会话。
- 不改 5 策略逻辑、不改 SimBroker 撮合内核、不改既有 DB 表结构、不改回测 / 回放语义。
- 不新增策略、不做参数寻优。
- 不把 WS 实时行情做成"实时下单"或改变 paper 会话的撮合来源（F1 仅用于**展示**最右一根真实跳动，撮合仍走既有回测 / paper 管线）。
- 不追求生产级编排（K8s / 多副本）；F5 仅做单机 compose + CI，OKX 连通仍由部署方保证（容器内需可达 OKX，详见 R5）。

---

## 3. 优先级与范围控制

| 优先级 | 特性 | 服务的全局目标 | 备注 |
|---|---|---|---|
| **P0** | F1 默认视图实时行情接入 | 数据真实可用 / 完成 | 接通"接而未用"的 `marketWS` + `/ws/market`，零后端业务改动 |
| **P0** | F2 缓存回退诚实标注 | 数据真实可用（诚信） | 后端仅加响应头 + CORS expose；前端加醒目横幅 |
| **P1** | F3 选择态与主题持久化 | 确保可用 | 纯前端，`store.ts` 包 `persist` 中间件 |
| **P1** | F4 成交 / 净值导出 CSV | 完成（核心诉求："查看实际交易情况与收益"） | 纯函数 `lib/export.ts` + 按钮，可单测 |
| **P2** | F5 容器化 + CI | 确保可用 / 完成 | 纯新增 Dockerfile / compose / workflow，零业务侵入 |

> 下游 Subtask #2 按 P0 → P1 → P2 实施；若 P2 受时间限制未完成，须在本文档 §11"实施过程发现"段记录，不得静默偏离。

---

## 4. 技术设计（架构 / 关键路径 / 时序）

整体架构沿用前序：浏览器 → Next.js（BFF：`/api/py/*` rewrite 到 Python `/api/*`，WS 经 `NEXT_PUBLIC_PY_WS_URL` 直连 `/api/ws/*`）→ FastAPI（OKX 适配 / 策略 / 引擎 / paper / SQLite）→ OKX V5（经系统 / Clash 代理）。迭代 3 **不新增架构层**，仅在既有层内接通通道、追加响应头、包持久化中间件、加纯函数与一组 Docker / CI 资产。

### 4.1 F1 默认视图实时行情接入

**现状（已核对）**：`apps/web/lib/ws.ts:74` 的 `marketWS(instId,bar,onEvent,onState)` 与后端 `app/api/ws_market.py` 的 `/ws/market` 已实现并可推送 `{type:"candle", data:{ts,o,h,l,c,vol,confirm}}`（含**未确认的成形中**那根，`confirm=false`）。但 `apps/web/app/page.tsx` 只在 `mode==="paper"` 时连 `paperWS`（`page.tsx:95-111`），从不连 `marketWS`，导致默认视图的 K 线只有 `candlesQ`（一次性历史）+ `older`（左滚增量）+ paper 的 `live`，**最右一根不会实时跳动**。

**设计（仅前端；后端零改动）**：
- `page.tsx` 新增一个仅在**非 paper 模式**生效的 WS 订阅 effect（避免与 paper 自己的事件流重复）：
  - 触发条件：`mode !== "paper"`（即 `backtest` / `compare`）且 `okxUp !== false`（health 已判 OKX 不可达时不连，省得无意义重连）。
  - 用 `marketWS(instId, bar, onEvent, onState)` 建连并 `connect()`：
    - `onEvent`：`e.type==="candle"` 时取 `c = e.data as Candle`；
      - 若 `c.confirm === true` → 视为**已收口的新历史根**，并入 `live`（复用既有 `setLive(prev => mergeUnique(prev, [c]))`，与 paper 同语义），同时清空 `forming`（成形根已收口）。
      - 若 `c.confirm === false` → 存入新增的 `forming` 单根状态（`setForming(c)`），作为**成形中**的最右一根。
      - 两种情况都更新新增的 `lastPrice` 状态（= `c.c`），供工具栏显示实时价。
    - `onState(open)` → 更新新增的 `liveWsOpen` 布尔。
  - 清理：effect 依赖 `[mode, instId, bar, okxUp]`；返回 `() => ws.close()`；切标的 / 周期时既有 effect（`page.tsx:59-62`）已 `setLive([])`，本处补 `setForming(null)`、`setLastPrice(null)`、`setDataSource("live")`（**评审 C5**：避免工具栏短暂显示上一标的旧价 / 残留来源态，下一 tick 之前不留脏值）。
  - **共享态说明（评审 C6）**：市场 WS 的确认根经 `setLive` 写入，与 paper 模式的确认根**有意共享**同一 `live` 数组——同一 `(instId,bar)` 下两者都是真实 OKX 确认根，经 `mergeUnique`（按 ts 去重升序）后等价，不会冲突；双方互斥订阅由订阅 effect 的 `mode!=="paper"` 条件保证（见 R3），不存在双流叠加。
- 展示合并：把 `forming` 并入 `candles` memo 的最右端——扩展 `mergeCandles(older, base, live, forming?)`：在原 `mergeUnique(mergeUnique(older,base),live)` 结果上，若 `forming` 存在则再 `mergeUnique(result, [forming])`（按 `ts` 去重，成形根与同 ts 历史根**覆盖**，天然得到"最后一根实时跳动"效果，且全程 ts 严格升序，满足 lightweight-charts 约束，复用 R6 经验）。
- 工具栏可视化：`ChartToolbar` 增**可选** props `liveWsOpen?:boolean`、`lastPrice?:number|null`（可选 = 向后兼容，不破坏现有调用）；在 `ConnDot` 旁渲染：一个"实时价"chip（`lastPrice` 用 `fmtPrice` 格式化）+ 一个表示 WS 实时流状态的小脉冲点（open 绿 / 否灰）。`page.tsx` 把 `liveWsOpen` / `lastPrice` 透传给 `ChartToolbar`。
- **不改撮合**：F1 纯展示；回测仍取静态 `candlesQ` 跑，paper 仍走自身管线。实时根只影响左图最右一根的视觉。

**时序（1m 周期最易演示）**：进入页面 → `candlesQ` 拉 300 根历史渲染 → `marketWS` 连上 → 每秒级推送成形根（`confirm=false`）→ 左图最右一根实时跳动、工具栏实时价刷新 → 该根收口（`confirm=true`）→ 固化为历史根、新成形根接续。

### 4.2 F2 缓存回退诚实标注

**现状（已核对）**：`app/api/market.py:42-62` 的 `/candles` 在 `OKXError` 且 `settings.allow_cached_fallback` 为真时返回 `repo.get_cached_candles(...)`，**响应体（`list[{ts,o,h,l,c,vol}]`）与实时取数完全同构**，前端无从分辨；默认 `allow_cached_fallback=False`（`config.py:47`），故默认仍是"OKX 不可达 → 502 显式报错"（正确，无假数据）。缺口在于：**一旦运维开启该开关，缓存数据就会被当作实时悄悄端给用户**。

**设计（后端仅加头 + CORS expose；前端加横幅；不改 body 结构）**：
- 后端 `app/api/market.py` `get_candles` 注入 `response: fastapi.Response` 形参（FastAPI 支持），在不改返回类型的前提下设响应头：
  - 成功路径（真实实时）：`response.headers["X-Aragon-Data-Source"] = "live"`。
  - 缓存回退路径（命中 `allow_cached_fallback` 且有缓存）：`response.headers["X-Aragon-Data-Source"] = "cache"`，并加 `response.headers["X-Aragon-Stale"] = "1"`。
  - OKX 不可达且未开回退或无缓存：维持现状 `raise`（→ main 的 `OKXError` 处理器转 502）。
- **响应头如何抵达浏览器（评审修正 C1）**：前端历史拉取走**同源** `/api/py/candles`，由 Next `rewrites()` 在**服务端**代理到 `${PY_BACKEND_URL}/api/candles`。对浏览器而言这是**同源响应**，**不触发 CORS**——因此只要 Next 把上游的自定义响应头转发回来，`candlesWithMeta` 即可直接 `res.headers.get("X-Aragon-Data-Source")` 读到。**真正的依赖是「Next rewrite 透传上游响应头」（Next 14 的 rewrite 代理默认转发上游响应头，成立），而非 CORS `expose_headers`**。
- 仍在 `app/main.py` 的 `CORSMiddleware` 增 `expose_headers=["X-Aragon-Data-Source","X-Aragon-Stale"]`，但**定位修正为「直连兜底」**：仅当浏览器**跨源直连** FastAPI（绕过 Next 代理，如本地直接调试 `:8000`）时才靠它放行响应头；经代理的默认路径不依赖它。因其无害且零成本，保留。**验收须经 `/api/py/*` 代理路径核对头是否可读**（见 §8.1），而非只对 FastAPI 直连单测——否则会验错对象。
- 后端 `/health` 增**可选**字段 `cachedFallbackEnabled: bool = get_settings().allow_cached_fallback`，让前端能在"回退可能发生"时预先知情（仅信息性）。
- 前端 `lib/api.ts` 新增 `candlesWithMeta(instId,bar,limit,before?)`：底层用 `fetch` 后读 `res.headers.get("X-Aragon-Data-Source")`，返回 `{ candles: Candle[], source: "live"|"cache" }`（保留原 `candles()` 供左滚增量等无需 meta 的调用）。为复用错误处理，`request<T>` 可抽出一个返回 `{data,res}` 的内部变体，或在 `candlesWithMeta` 内重走一遍 `fetch + 错误分支`（与 `request` 同构）。
- 前端 `page.tsx` 主历史拉取改用 `candlesWithMeta`，新增 `dataSource` 状态；当 `dataSource==="cache"` 时在 K 线区顶部渲染**醒目琥珀色横幅**："缓存样例 · 非实时——OKX 暂不可达，当前显示本地缓存的真实历史（非最新行情）"。`health.data.cachedFallbackEnabled` 为 false 时该路径永不触发。
- **诚信声明**：本特性**不放宽**硬约束——默认配置仍是"不可达即显式报错、无任何回退"；缓存数据本身是**真实 OKX 历史**（只是过期），现在被**显式标注**，从此不会"静默冒充实时"。

### 4.3 F3 选择态与主题持久化

**现状（已核对）**：`lib/store.ts` 用裸 `create(...)`，无持久化；刷新后 `instId/bar/strategyId/mode` 回默认、`theme` 回 `light`（`toggleTheme`/`setTheme` 只 `classList.toggle`，未落盘）。`StrategyPanel.tsx:35-39` 仅在 `strategyId===null` 时默认选第一个。

**设计（纯前端）**：
- `lib/store.ts` 用 `zustand/middleware` 的 `persist` 包裹（`zustand@^4.5.5` 已含该中间件，无需新依赖）：
  - `create<AppState>()(persist((set,get)=>({...原实现...}), { name:"aragon-ui", version:1, storage: createJSONStorage(()=>localStorage), partialize: (s)=>({ instId:s.instId, bar:s.bar, strategyId:s.strategyId, mode:s.mode, theme:s.theme }) }))`。
  - **只持久化** `instId/bar/strategyId/mode/theme`；**绝不持久化** `runId` 与 `paperSessionId`（运行期句柄——过期的 runId 会 404、paperSessionId 进程重启即失效），`partialize` 把它们排除。
- 主题水合：保留 `page.tsx:120-122` 的 `useEffect(() => classList.toggle('dark', theme==='dark'), [theme])`——persist 在 store 初始化时即恢复 `theme`，首个 effect 跑时就会把 `dark` class 同步到 `<html>`，避免主题闪烁。
- **SSR / 水合安全（评审修正 C2）**：Next App Router 会先用**默认 state** 在服务端渲染客户端组件（工具栏的 `instId/bar/mode` 等随之进入服务端 HTML），浏览器随后用持久化 state 水合——两者分歧会触发 React **hydration 不一致告警**。因此 `persist` 配置 `skipHydration: true`，并在根客户端组件（`app/page.tsx`，store 的首个消费方）的 `useEffect(() => { useAppStore.persist.rehydrate(); }, [])` 内**于挂载后**显式补水：首帧渲染默认值（与服务端 HTML 一致 → 无告警），挂载后再切到持久化值（接受「默认→恢复」的一帧闪烁，换取零水合告警）。`createJSONStorage(()=>localStorage)` 在服务端无 `window` 时仍由 zustand 守护；store 仅在 `"use client"` 树中使用。
- 失效保护（**小改 `StrategyPanel`**）：默认选中逻辑从"`strategyId===null` 才选第一个"增强为"`strategyId===null` **或** 持久化的 `strategyId` 不在 `strategies` 列表中（被删的自定义策略）→ 选第一个"，避免恢复到一个已删除的策略导致空详情 / 自动回测打到不存在的 id。

### 4.4 F4 成交 / 净值导出 CSV

**现状（已核对）**：`TradesTable.tsx` 渲染成交但无导出；净值在 `page.tsx` 经 `equityQ` 取（图表用），`StrategyPanel` 内有 `trades`（query）、`runId`、`metrics`，但没有 `equity`。用户原始诉求明确要"查看不同策略实际的交易情况以及收益"，导出是该诉求的自然延伸。

**设计（纯前端 + 可单测）**：
- 新增 `apps/web/lib/export.ts`（纯函数，对齐 `format.ts` + `format.test.ts` 既有约定）：
  - `toCsv(rows: Record<string, unknown>[], columns: {key:string; label:string}[]): string`——输出含表头；字段含 `, " \n` 时用双引号包裹并把内部 `"` 转义为 `""`（RFC4180 风格）；行分隔 `\r\n`。
  - `tradesToCsv(trades: Trade[]): string`——列：时间(ISO8601 UTC，由 `ts*1000` 转)、方向(side)、价格(price)、数量(qty)、手续费(fee)、盈亏(pnl)、持仓后(positionAfter)、原因(reason)。
  - `equityToCsv(equity: EquityPoint[]): string`——列：时间(ISO)、净值(equity)、回撤(drawdown)。
  - `downloadCsv(filename: string, content: string): void`——`new Blob([content], {type:"text/csv;charset=utf-8"})` + 临时 `<a download>` 触发，前置 `﻿` BOM 以便 Excel 正确识别 UTF-8；`typeof document==="undefined"` 时直接返回（SSR / 测试安全）。
- 新增 `apps/web/lib/export.test.ts`（vitest）：断言表头正确、含逗号 / 引号 / 换行的字段被正确转义、`tradesToCsv`/`equityToCsv` 行数与内容匹配（`downloadCsv` 触发 DOM 的部分不在纯函数单测覆盖，仅测 CSV 生成）。
- UI 接线（`StrategyPanel.tsx`）：
  - "成交明细"Tab：在 `TradesTable` 上方加一个"导出 CSV"按钮，`disabled={!trades?.length}`，点击 `downloadCsv(\`aragon-trades-run\${runId}.csv\`, tradesToCsv(trades))`（`trades` 已在 panel 作用域）。
  - "绩效指标"Tab：加"导出净值 CSV"按钮——为此在 `StrategyPanel` 增一个轻量 `equityQ = useQuery({ queryKey:["equity",runId], queryFn:()=>api.equity(runId), enabled: !!runId })`（与 page 的同键查询共享 RQ 缓存，不重复请求），按钮 `disabled={!equityQ.data?.length}`（**评审 C7**：paper/compare 下 equity 缓存可能为空，与成交按钮一致禁用，避免导出空文件），点击 `downloadCsv(\`aragon-equity-run\${runId}.csv\`, equityToCsv(equityQ.data))`。
- 不新增后端接口（数据已由 `/runs/{id}/trades`、`/runs/{id}/equity` 提供）。

### 4.5 F5 容器化 + CI

**现状（已核对）**：仅有 `scripts/dev.py` 跨平台编排（venv + pnpm），无容器化、无 CI workflow；`apps/web/package.json` 有 `build`/`start`，`next.config.mjs` 未开 standalone。

**设计（纯新增资产 + 一行 next 配置）**：
- `apps/web/next.config.mjs` 增 `output: "standalone"`（Next 14 官方瘦镜像产物；附加项，不影响 `next dev`/现有 rewrite）。
- 新增 `services/quant/Dockerfile`：基于 `python:3.11-slim`；`COPY requirements.txt` → `pip install`（不装测试依赖亦可，简单起见照装）；`COPY app ./app`；`EXPOSE 8000`；`CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]`；设 `ENV PYTHONUTF8=1`。
- 新增 `apps/web/Dockerfile`（多阶段）：`node:20-alpine` builder `pnpm install --frozen-lockfile` + `pnpm build`（`output:standalone`）→ runner 拷 `.next/standalone` + `.next/static` + `public`；`EXPOSE 3000`；`ENV PY_BACKEND_URL`、`NEXT_PUBLIC_PY_WS_URL` 由 compose 注入（注意 `NEXT_PUBLIC_*` 在 **build 期**注入，故 WS URL 需作为 build-arg 传入或在 compose 用浏览器可达地址，见 R5）。
  - **构建路径锁定（评审 C3，必须遵守）**：(a) 仓库**无根 `pnpm-lock.yaml`**——锁文件实际在 `apps/web/pnpm-lock.yaml`，故 **build 上下文取 `apps/web`**，`pnpm install --frozen-lockfile` 在该目录内执行（不要以仓库根为上下文，否则 `--frozen-lockfile` 找不到锁会失败）。(b) 后端依赖取 `services/quant/requirements.txt`（经核**已存在**，`pip install -r requirements.txt` 可行）。(c) 根存在 `pnpm-workspace.yaml`，`output:"standalone"` 一旦被识别为 monorepo，可能把入口产到 `.next/standalone/apps/web/server.js` 等**嵌套路径**——以 `apps/web` 为构建上下文可避免 Next 探到工作区根；Subtask #2 须**实测 standalone 产物中 `server.js` 的真实位置**再据此写 runner 的 `WORKDIR`/`COPY`/`CMD`，**不可想当然写 `CMD ["node","server.js"]`**（路径错位会让镜像启动即 `MODULE_NOT_FOUND`）。
- 新增根 `docker-compose.yml`：`quant` 服务（build `services/quant`，端口 8000，env 透传 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY`、`ALLOW_CACHED_FALLBACK`、`DB_PATH`，挂 volume 持久化 `aragon.db`）+ `web` 服务（build `apps/web`，端口 3000，`PY_BACKEND_URL=http://quant:8000`，`NEXT_PUBLIC_PY_WS_URL` 设为**浏览器可达**的 `ws://localhost:8000`，`depends_on: quant`）。
- 新增 `services/quant/.dockerignore`、`apps/web/.dockerignore`（排除 `.venv/ __pycache__/ node_modules/ .next/ *.db .env*`）。
- 新增 `.github/workflows/ci.yml`：两个 job——
  - `backend`：`ubuntu-latest` + Python 3.11，`pip install -r services/quant/requirements.txt`，`cd services/quant && python -m pytest -q`（`pyproject` 的 `addopts="-m 'not network'"` 默认排除真实网络用例，CI 离线可绿）。
  - `frontend`：`ubuntu-latest` + Node 20 + pnpm，`pnpm -C apps/web install --frozen-lockfile`，`pnpm -C apps/web exec tsc --noEmit`，`pnpm -C apps/web test`（vitest），`pnpm -C apps/web build`。
- `README.md` 增"容器化一键运行（`docker compose up --build`）"段，并**明确声明**：容器内仍需能连通 OKX（透传代理 / host 网络），否则按硬约束显式报错，**绝不**以假数据兜底；CI 只跑离线用例，真实数据校验仍以 `scripts/smoke_e2e.py` / `pytest -m network` 为准。

---

## 5. 文件 / 模块变更计划

> 约定：**新增** = create，**修改** = 在既有文件内追加 / 扩展（不重写既有语义）。所有路径用正斜杠。

### 5.1 根 / 文档 / 容器 / CI
| 文件 | 动作 | 意图 |
|---|---|---|
| `docs/plans/aragon-iter3-hardening/spec.md` | 新增 | 本设计文档（Subtask #0 产出） |
| `docker-compose.yml`（仓库根） | 新增 | 一键拉起 `quant`(8000) + `web`(3000)，透传代理 / 持久化卷（F5） |
| `services/quant/Dockerfile` | 新增 | Python 后端镜像（python:3.11-slim + uvicorn）（F5） |
| `apps/web/Dockerfile` | 新增 | 前端镜像（多阶段 Node20 + Next standalone）（F5） |
| `services/quant/.dockerignore` | 新增 | 排除 venv / 缓存 / db / env（F5） |
| `apps/web/.dockerignore` | 新增 | 排除 node_modules / .next / env（F5） |
| `.github/workflows/ci.yml` | 新增 | CI：后端 pytest(非 network) + 前端 tsc/vitest/build（F5） |
| `README.md` | 修改 | 增"实时行情"、"缓存回退标注"、"持久化"、"导出 CSV"、"容器化运行"说明 |

### 5.2 后端 `services/quant`
| 文件 | 动作 | 意图 |
|---|---|---|
| `app/api/market.py` | 修改 | `/candles` 注入 `Response` 并设 `X-Aragon-Data-Source: live\|cache`（+ `X-Aragon-Stale`）；`/health` 增 `cachedFallbackEnabled`（F2） |
| `app/main.py` | 修改 | `CORSMiddleware` 增 `expose_headers=["X-Aragon-Data-Source","X-Aragon-Stale"]`（F2 关键） |
| `app/api/ws_market.py` | **不改** | 已可推送 confirm/未确认根，F1 直接复用 |
| `pyproject.toml` | **不改** | 已有 `addopts="-m 'not network'"`，CI 直接复用 |

### 5.3 前端 `apps/web`
| 文件 | 动作 | 意图 |
|---|---|---|
| `lib/store.ts` | 修改 | 包 `persist` 中间件，`partialize` 仅持久化 `instId/bar/strategyId/mode/theme`（F3） |
| `lib/api.ts` | 修改 | 新增 `candlesWithMeta(...)` 读 `X-Aragon-Data-Source` 头；`Health` 消费新增字段（F2） |
| `lib/types.ts` | 修改 | `Health` 增 `cachedFallbackEnabled?:boolean`；新增 `DataSource = "live"\|"cache"`（F2） |
| `lib/export.ts` | 新增 | `toCsv`/`tradesToCsv`/`equityToCsv`/`downloadCsv` 纯函数（F4） |
| `lib/export.test.ts` | 新增 | CSV 生成 / 转义 单测（vitest）（F4） |
| `app/page.tsx` | 修改 | 非 paper 模式接 `marketWS`（forming/lastPrice/liveWsOpen）+ 合并 forming 根；主历史改用 `candlesWithMeta` + 缓存横幅（F1/F2） |
| `components/Chart/ChartToolbar.tsx` | 修改 | 增可选 `liveWsOpen`/`lastPrice` props，渲染实时价 chip + 实时流状态点（F1） |
| `components/Chart/CandleChart.tsx` | **不改**（如需）| 最右一根跳动经 `candles` 数组覆盖即可；若用 `series.update` 优化再小改 |
| `components/Strategy/StrategyPanel.tsx` | 修改 | 默认选中加"持久化 id 失效回退"守卫（F3）；成交 / 绩效 Tab 加导出按钮 + 轻量 `equityQ`（F4） |
| `components/Strategy/TradesTable.tsx` | 修改（可选）| 或在此就地放"导出 CSV"按钮（与 StrategyPanel 二选一，避免重复）（F4） |
| `next.config.mjs` | 修改 | 增 `output:"standalone"`（F5） |

> **不改动**：`engine/*`、`strategies/*`、`db/models.py`、`db/repositories.py`、`okx/rest_client.py`（F2 只在路由层加头，不改取数 / 归一化）、`okx/ws_client.py`、`paper/*`、`schemas.py`、`db/session.py`。迭代 3 是它们的**消费者 / 接通者**，不是修改者。

---

## 6. 接口设计（REST / WebSocket / CLI）

### 6.1 REST / 响应头 变更（后端真实路径，前端经 `/api/py/*`）
| Method | Path | 变更 | 说明 |
|---|---|---|---|
| GET | `/api/candles` | **新增响应头** `X-Aragon-Data-Source: live\|cache`、缓存时 `X-Aragon-Stale: 1` | Body **不变**（`[{ts,o,h,l,c,vol}]`），向后兼容（F2） |
| GET | `/api/health` | **新增字段** `cachedFallbackEnabled:boolean` | 在 v2 `{status,okx,okxLatencyMs,lastOkTs,wsConfigured}` 基础上**追加**，不破坏既有消费（F2） |

错误体沿用前序：OKX 不可达且未开回退 / 无缓存 → 502 `{error:{code:"OKX_UNREACHABLE",...}}`（main 的 `OKXError` 处理器）。所有 `ts` 仍为**秒级 UTC**（放行条件 ③）。**无新增 / 删除路由，无 body 结构破坏**。

### 6.2 类型（`apps/web/lib/types.ts`，camelCase 对齐后端）
```ts
type DataSource = "live" | "cache";
type Health = {
  status: string; okx: "up" | "down";
  okxLatencyMs: number | null; lastOkTs: number | null; wsConfigured: boolean;
  cachedFallbackEnabled?: boolean;   // F2 新增（可选，向后兼容）
};
// api.candlesWithMeta(...) -> { candles: Candle[]; source: DataSource }
// store 持久化形状（localStorage key "aragon-ui"）：{ instId, bar, strategyId, mode, theme }
```

### 6.3 WebSocket
WS 通道 / 事件 schema **完全不变**：F1 直接复用既有 `/api/ws/market`（`{type:"candle",data:{...,confirm}}`）与 `lib/ws.ts` 的 `marketWS`/`ReconnectingWS`（自带指数退避重连）。前端只是**首次真正调用** `marketWS`，无需新通道、无后端改动。

### 6.4 CLI / 容器命令
- 既有：`pnpm setup|preflight|dev|test`、`python scripts/dev.py {setup|preflight|up|test}`、`python scripts/smoke_e2e.py`、`pytest -m network`（全部不变）。
- 新增：`docker compose up --build`（拉起 web:3000 + quant:8000）、`docker compose down`（F5）。
- 新增（CI，自动触发）：GitHub Actions `ci.yml` 在 push / PR 跑后端 pytest（非 network）+ 前端 tsc/vitest/build。

---

## 7. 数据模型

**不新增 / 不修改任何 DB 表**。迭代 3 全部复用既有 `strategies / strategy_runs / trades / equity_points / candles_cache`（schema 见迭代 1 v2 §10），且 F4 导出纯读既有 `/runs/{id}/trades`、`/runs/{id}/equity`。

新增的"数据形态"仅有两处，均非 DB：
- **浏览器 localStorage**（F3，key `aragon-ui`，由 zustand persist 管理）：`{ version:1, state:{ instId:string, bar:Bar, strategyId:number|null, mode:Mode, theme:"light"|"dark" } }`。**不含** `runId`/`paperSessionId`（运行期句柄，刻意不持久化）。
- **运行时内存**（F1，page 组件内）：`forming: Candle|null`（成形中最右一根）、`lastPrice:number|null`、`liveWsOpen:boolean`、`dataSource:"live"|"cache"`、以及一个 `marketWS` 的 `ReconnectingWS` 句柄（effect 卸载即 `close()`）。

> 缓存表 `candles_cache` 的数据**本就是真实 OKX 历史**；F2 不改写它，只在回退返回它时**诚实标注其来源**。

---

## 8. 测试与验收标准

### 8.1 单元 / 集成测试
- **后端**（pytest + httpx AsyncClient，mock OKX）：
  - 新增 / 扩展 `test_api`（或新 `test_market_headers`）：`/candles` 成功路径响应头含 `X-Aragon-Data-Source: live`；构造 OKX 抛错 + `allow_cached_fallback=True` + 预置缓存 → 响应头 `X-Aragon-Data-Source: cache` 且 `X-Aragon-Stale: 1`，body 仍为 candle 数组；`allow_cached_fallback=False` + OKX 抛错 → 仍 502（无回退、无假数据）。`/health` 含 `cachedFallbackEnabled` 字段。
  - **测试装配要点（评审修正 C4，否则用例跑不通）**：`get_settings()` 为 `@lru_cache` 且 `tests/conftest.py` 在导入期固定 `os.environ["ALLOW_CACHED_FALLBACK"]="false"`——翻动该开关须 `os.environ["ALLOW_CACHED_FALLBACK"]="true"` 后调 `get_settings.cache_clear()`（用例结束复位 env 并再 `cache_clear()`），或更稳妥地 `monkeypatch.setattr("app.api.market.get_settings", lambda: FakeSettings(allow_cached_fallback=True))`。**预置缓存**：先以可用的假 OKX 客户端成功跑一次 `/candles`（触发 `repo.upsert_candles` 落 `candles_cache`），或直接 `await repo.upsert_candles(...)` 写入，再令假客户端的 `get_candles` 抛 `OKXError`，方能命中回退分支并断言 `cache` 头。断言走被测 ASGI app 的真实路由（httpx `AsyncClient`），头由 FastAPI 注入的 `Response` 形参携带。
  - 既有 27 项后端测试须继续全绿（回归，尤其 health / candles）。
- **前端**（vitest）：
  - 新增 `export.test`：`toCsv` 表头 + 逗号 / 引号 / 换行转义；`tradesToCsv`/`equityToCsv` 行内容与列序正确；含 BOM。
  - 既有 11 项 vitest（含 `compare.test`/`format.test`）须继续全绿；`tsc --noEmit` 0 error；`next build`（standalone）成功。

### 8.2 验收标准（Acceptance Criteria）
1. **实时可见（F1）**：默认（回测）视图、1m 周期下，最右一根 K 线在数秒内随真实 OKX 行情跳动，工具栏显示刷新中的实时最新价与"实时流"状态点；切标的 / 周期后自动重连且不串数据。OKX 不可达时不报错刷屏（health 已判 down 即不连）。
2. **来源诚实（F2）**：默认配置下 OKX 不可达仍显式 502、前端红色"无法连接 OKX"态（无假数据）；置 `ALLOW_CACHED_FALLBACK=true` 且有缓存时，前端出现醒目"缓存样例·非实时"横幅，且 `/candles` 响应头 `X-Aragon-Data-Source: cache`。
3. **持久化（F3）**：选定标的 / 周期 / 策略 / 模式 / 主题后刷新浏览器，上述选择全部恢复；恢复到已删除策略时安全回落到第一个，无空白 / 无 404 刷屏。
4. **导出（F4）**：有 run 时点"导出 CSV"得到可被 Excel / Pandas 正确解析的成交与净值文件（UTF-8 + BOM，字段转义正确），内容与界面一致。
5. **可交付（F5）**：`docker compose up --build` 后浏览器 `http://localhost:3000` 可用、`http://localhost:8000/api/health` 返回；GitHub Actions CI 两 job（后端非 network pytest、前端 tsc/vitest/build）全绿。
6. **回归无破坏**：迭代 1 + 迭代 2 全部验收（真实 K 线、单策略回测叠加、参数重跑、CRUD、对比榜单、运行历史、回放加速、F2 smoke）仍成立；5 条放行条件未回退。
7. 后端 pytest（含新增、不含 network）+ 前端 vitest + `tsc` + `next build` 全绿；`git status` 仅含本迭代相关文件，无构建产物 / `.env` / `*.db` / `node_modules` / `.next`。

---

## 9. 风险与缓解（Risks & Mitigations）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | F1 每个浏览器连接在后端各起一个 OKX 上游 WS（`ws_market` 每连一个 `OKXWSClient`） | 多标签 / 多用户放大上游连接数 | 本期为**单机本地工具**，连接数可控；effect 在切标的 / 周期 / 卸载时 `close()`；`okxUp===false` 时不连；如需可在后续做按 (inst,bar) 复用的 fan-out（本迭代不做，列为已知约束） |
| R2 | F1 成形根（`confirm=false`）与历史根 ts 冲突致 lightweight-charts time 不单调抛错 | 图表崩 | 经 `mergeUnique`（Map keyed by ts）**覆盖**同 ts 根，输出严格升序；复用迭代 1/2 的 ts 去重升序经验（R6 历史） |
| R3 | F1 与 paper 模式实时流重复订阅 | 双流 / 数据错乱 | 订阅 effect 条件 `mode!=="paper"`；paper 模式仍只走 `paperWS` |
| R4 | F2 前端读不到 `X-Aragon-Data-Source`（横幅永不出现） | 标注失效、诚信缺口未堵 | **真实风险是 Next rewrite 是否透传上游自定义头**（Next 14 默认透传，成立）——验收须**经 `/api/py/*` 代理路径**手验 / 集成断言头存在，而非仅对 FastAPI 直连单测；同源代理路径不受 CORS 约束，`expose_headers` 仅作跨源直连兜底（评审 C1） |
| R5 | F5 容器内无法连通 OKX（代理 / 地域限制）、`NEXT_PUBLIC_*` build 期注入、或 monorepo standalone 入口路径错位 | 容器起得来但取不到真实数据 / WS 连不上 / 镜像启动即 `MODULE_NOT_FOUND` | compose 透传 `HTTP(S)_PROXY`；`NEXT_PUBLIC_PY_WS_URL` 用浏览器可达地址（`ws://localhost:8000`，**仅同机浏览器有效**，远程部署须改为宿主可达地址）并作为 build-arg；**前端 build 上下文取 `apps/web`、锁文件 `apps/web/pnpm-lock.yaml`、实测 standalone `server.js` 路径再定 `CMD`**（评审 C3）；README 显式声明"容器需可达 OKX，否则按硬约束显式报错、绝不假数据" |
| R6 | F3 持久化了过期 `strategyId`/`mode` 导致进入异常态 | 空详情 / 自动回测打到无效 id | `partialize` 排除 runtime 句柄；StrategyPanel 加"id 失效回落第一个"守卫；`version` 字段便于将来迁移 |
| R7 | F3 主题持久化与 SSR 首帧不一致 | 主题闪烁 | persist 在 store 初始化即恢复 theme，首个 effect 同步 `dark` class；store 仅客户端使用 |
| R8 | F4 CSV 在 Excel 中中文 / 逗号乱码 | 导出不可用 | 加 `﻿` BOM + 标准字段转义（`"` → `""`、含分隔符即包引号）；纯函数单测覆盖转义 |
| R9 | F5 `output:"standalone"` 影响现有 `next dev` / rewrite | 开发回归 | standalone 只影响 `next build` 产物，`dev`/rewrite 不受影响；CI 的 `next build` 守护 |

---

## 10. 下游实施顺序（建议给 Subtask #2）

1. **F2 后端**（`market.py` 加头 + `main.py` expose_headers + `/health` 字段 + 后端测试）→ **F2 前端**（`api.candlesWithMeta` + `types.Health` + page 横幅）。先把诚信缺口堵上并被测试锚定。
2. **F1**（page 接 `marketWS` + forming/lastPrice/liveWsOpen + 合并；ChartToolbar 实时价 chip）。1m 周期手验最右一根跳动。
3. **F3**（`store.ts` 包 persist + partialize；StrategyPanel 失效守卫）。手验刷新恢复。
4. **F4**（`lib/export.ts` + `export.test.ts` + StrategyPanel/TradesTable 导出按钮 + 轻量 equityQ）。
5. **F5**（`next.config.mjs` standalone + 两个 Dockerfile + `.dockerignore` + `docker-compose.yml` + `.github/workflows/ci.yml` + README）。
6. 收尾：跑全量回归（后端非 network pytest + 前端 tsc/vitest/build），真实数据手验 F1 实时跳动 / F2 缓存横幅（临时置开关）/ F4 导出；把实施中对本方案的受控澄清 / 偏离记录到 §11。

---

## 11. 实施过程发现的方案缺陷（Subtask #2 实施记录）

> 体例：D = 实施发现（受控澄清 / 受控偏离 / 验收实测）。全程**纯增量**，未触碰 5 策略 / SimBroker / DB schema，未回退 5 条放行条件。

- **D1（F2，受控澄清）**：`page.tsx` 的 `dataSource` 未新增独立 `useState`，而是**从 `candlesQ.data?.source` 派生**（来源即主历史一次性拉取的 `X-Aragon-Data-Source`）。这比独立 state 更简洁，且 query 在切标的 / 周期时自动重取 → `data` 短暂为 `undefined` → `dataSource` 回落 `"live"`，**天然满足 C5「重置来源态」**，无需手动 `setDataSource`。横幅语义仍限定「当前主视图新鲜度」（C9）。满足 spec §4.2 意图。

- **D2（F5，受控偏离——对 §5.1「无条件 `output:'standalone'`」的修正）**：`next.config.mjs` 改为**环境变量门控** `output: process.env.NEXT_OUTPUT_STANDALONE === "1" ? "standalone" : undefined`。原因：本项目 Windows 执行环境下，pnpm 的 `node_modules` 为**符号链接结构**，Next `output:"standalone"` 的 `copyTracedFiles` 会逐个 `fs.symlink(...)` 重建依赖软链，在无 Developer Mode / 管理员权限时报 **`EPERM: operation not permitted, symlink`**，致 `next build` 退出码 1。实测：编译本身**成功**（`✓ Compiled successfully` / `✓ Generating static pages (4/4)` / `Finalizing page optimization`），**仅 standalone 复制步骤**因 Windows 软链限制失败；Linux 容器（`node:20-alpine`）/ CI（`ubuntu-latest`）**无此限制**。若保留无条件 standalone，会破坏本机标准构建命令 `pnpm build`（违反 Definition of Done「项目仍能编译」）。故经 env 门控：本机默认普通产物（绿），`apps/web/Dockerfile` 与（如需）CI 设 `NEXT_OUTPUT_STANDALONE=1` 产出 standalone。零业务侵入，`next dev` / rewrite 不受影响。

- **D3（F5，受控降级——按 §3 / 放行条件 #6 / 评审 C8）**：**Docker 镜像构建与 `docker compose up --build` 在本沙箱未实测**（Docker 守护进程 / 真实出网非常备）。已按「仅交付资产」降级交付：`docker-compose.yml`、`services/quant/Dockerfile`、`apps/web/Dockerfile`、两个 `.dockerignore`、`.github/workflows/ci.yml` 全部就位。standalone 入口路径按 C3 锁定为**扁平** `/app/.next/standalone/server.js`（依据：Docker 构建上下文 = `apps/web`，镜像内不含仓库根 `pnpm-workspace.yaml`，Next 不会探到 monorepo 工作区根；推理与依据见 `apps/web/Dockerfile` 注释）。**CI 前端 `pnpm build` 走普通产物**（不设 standalone env），以保证 Linux CI **确定性绿**；standalone 路径的真实验证由 Docker 镜像（Linux 软链可用）承载。容器内仍需可达 OKX（compose 透传代理），否则按硬约束显式报错、绝不假数据（R5，README 已声明）。

- **D4（F4，遵循变更计划「二选一」）**：导出按钮放在 `StrategyPanel.tsx` 的「成交明细」/「绩效指标」Tab，**未改 `TradesTable.tsx`**（§5.3 列其为「修改（可选）·与 StrategyPanel 二选一，避免重复」）。`equityQ`（`enabled:!!runId`）与 `page.tsx` 同 `["equity", runId]` 键**共享 React Query 缓存**，不重复请求；导出按钮 `disabled` 在 `!trades?.length` / `!equityQ.data?.length` 时禁用（C7）。

- **D5（验收实测·真实数据约束）**：本轮新增**零假数据**——F1 实时根复用既有 `/api/ws/market`（真实 OKX 成形/确认根）、F2 缓存回退返回的是 `candles_cache` 中**真实 OKX 历史**（仅过期，现经响应头诚实标注）。后端测试用假 OKX 客户端仅用于**单元/集成**（与既有 `test_api` 同构，离线 CI），不进入运行时取数路径；真实数据端到端校验仍以 `scripts/smoke_e2e.py` / `pytest -m network` 为准。

---

> 本文档为迭代 3 收尾增量设计 **v2（Subtask #1 设计评审完成）**。Subtask #0 产出 v1 后，本轮已按"可行性 / 完整性 / 一致性 / 规模适配"四维 + 5 条放行条件逐文件核对源码并评审：文首插入「评审记录」（P0=0 / P1=4 / P2=5），P1（C1–C4）与 P2（C5、C7）已就地修复，余 P2（C6/C8/C9）登记接受，文末追加「评审结论」。Subtask #2 按本 **v2** 实施。本方案为**纯增量收尾**：不触碰 5 策略、SimBroker 撮合内核与既有 DB schema，所复用的后端契约（`/ws/market`、`/candles`、`get_cached_candles`、`run_out`、`/runs/{id}/{trades,equity}`）与前端结构（`marketWS`/`ReconnectingWS`、`api.request`、zustand store、`StrategyPanel` Tabs、`ChartToolbar`/`CandleChart`）经逐文件核对**全部真实存在且签名匹配**。

---

## 评审结论（Review Verdict · Subtask #1）

**结论：有条件通过（Approved with conditions）。**

**判断依据**：本方案为纯增量收尾，方向与全局目标「完成项目开发、确保可用、数据真实可用」高度对齐；所引用的全部源码落点经逐文件核对**真实存在且签名匹配**（含 `marketWS`/`ws_market` 确推送 `confirm=False` 成形根、`market.py` 缓存回退、`store.ts` 无持久化、`StrategyPanel` 已含 `trades` query、`requirements.txt`/`conftest.py` 在）；不回退迭代 1 v2 的 5 条放行条件，不触碰 5 策略 / SimBroker / DB schema。评审**未发现 P0 阻断项**；4 个 P1（C1 CORS 机制误判、C2 SSR 水合、C3 F5 构建路径、C4 F2 测试装配）与 2 个 P2（C5、C7）已在正文就地修复，余 P2（C6/C8/C9）登记并接受。

**放行条件（下游 Subtask #2 实施时须逐条满足）**：
1. **数据真实**：F1 实时根与 F2 缓存回退一律为真实 OKX 数据；默认配置「不可达即 502、绝不假数据」不得回退；新增路径零假数据。
2. **F2 验对路径（C1/C4）**：响应头须经 `/api/py/*` 同源代理路径手验 / 集成测试，确认 Next 透传上游头；缓存头单测按 §8.1 的 `cache_clear`/`monkeypatch` + 预置 `candles_cache` 装配，不可照搬空壳用例。
3. **F3 零水合告警（C2）**：`skipHydration` + 挂载后 `persist.rehydrate()` 落实，刷新恢复 `instId/bar/strategyId/mode/theme` 且无 React hydration 报错；恢复到已删除策略安全回落第一个。
4. **F5 构建可复现（C3）**：前端构建上下文取 `apps/web`、锁文件 `apps/web/pnpm-lock.yaml`、后端依赖 `services/quant/requirements.txt`；**实测 standalone `server.js` 真实路径后再定 `CMD`**；CI 两 job（后端非 network pytest、前端 tsc/vitest/build）离线全绿。
5. **纯增量无回归**：迭代 1 + 迭代 2 全部验收仍成立；后端 `pytest`（非 network）+ 前端 `tsc`/`vitest`/`next build` 全绿；`git status` 不含构建产物 / `.env` / `*.db` / `node_modules` / `.next`。
6. **受控记录（C8）**：若 F5 全量运行验证受 Docker / 真实网络环境限制无法实测，按 §3 降级为「仅交付资产（Dockerfile/compose/CI）」并写入 §11；任何对本 v2 的偏离须在 §11 留痕，不得静默偏离。

满足以上条件即视为通过。**Subtask #2 按本 v2 实施。**
