# 实盘模式与实盘下单「默认未开启」— 问题定位与根因分析

> 诊断节点（Subtask #0）产出。本节点不写修复代码、不提交。下游修复节点请以「六、推荐方案」为准。
>
> 对应本轮需求（第 2 轮）：
> 1. 「实盘模式要默认开启，现在显示这个不对。」
> 2. 「实盘下单未启用（需后端设置 LIVE_TRADING_ENABLED=true），当前仅可查看真实持仓 / 成交。」

---

## 一、问题描述

用户报告两个相关但独立的症状，本质都是「默认值把实盘能力关掉了」：

- **症状 A（默认账户模式错）**：应用启动后顶部账户模式分段开关默认停在「模拟盘」，用户期望默认进入「实盘」。
- **症状 B（实盘下单被禁用）**：即便切到「实盘」，下单面板顶部出现横幅
  > 实盘下单未启用（需后端设置 LIVE_TRADING_ENABLED=true），当前仅可查看真实持仓 / 成交。

  且方向 / 价格 / 杠杆 / 数量 / 止盈止损 / 「买入开多」等全部控件被禁用，只能查看真实持仓与成交。

两个症状分属前端持久化默认值与后端配置默认值，需分别定位，二者都应翻转为「默认开启」。

---

## 二、复现步骤

### 环境
- OS：Windows（`process.platform === 'win32'`）。
- 后端：`services/quant`（FastAPI，pydantic-settings 从环境变量 / `.env` 读取）。
- 前端：`apps/web`（Next.js + Zustand persist，localStorage key `aragon-ui`）。
- 前置：已按第 1 轮配置了一个「就绪默认 OKX 账户」（含 Secret/Passphrase，如 `Dev-JR-US`）。

### 复现 A —— 账户模式默认是「模拟盘」
1. 清空浏览器对 key `aragon-ui` 的 localStorage（模拟全新安装），或直接全新安装桌面端。
2. 启动应用，观察顶部账户模式开关。
3. **实际**：默认选中「模拟盘」（`accountMode === "sim"`）。**期望**：默认选中「实盘」。

### 复现 B —— 实盘下单被横幅禁用
1. 切到「实盘」（已有就绪默认账户时可点）。
2. 观察图表下方 `LiveBottomPanel` 左侧的下单面板。
3. **实际**：顶部显示「实盘下单未启用（需后端设置 LIVE_TRADING_ENABLED=true）…」横幅，下单控件全禁用。
4. 直接打后端写接口可证实守门返回 403：
   ```
   POST /api/py/trading/order  →  403 {"error":{"code":"LIVE_TRADING_DISABLED","message":"实盘下单未启用"}}
   ```

### 后端默认值实证（无需起服务）
在 `services/quant` 目录执行：
```bash
python -c "from app.config import Settings; s=Settings(_env_file=None); print('live_trading_enabled=', s.live_trading_enabled)"
```
输出：
```
live_trading_enabled= False
```
即：在未显式设置 `LIVE_TRADING_ENABLED` 时，后端默认 `False`，写接口一律 403、`/trading/config` 下发 `liveTradingEnabled=false`，前端据此渲染禁用横幅。

---

## 三、根因分析（含证据）

### 根因 A：前端账户模式默认值为 `"sim"`

Zustand store 的初始态把账户模式写死成模拟盘：

- `apps/web/lib/store.ts:181`
  ```ts
  accountMode: "sim",
  ```
- 该字段进入持久化白名单（刷新后从 localStorage 恢复）：`apps/web/lib/store.ts:376` `accountMode: s.accountMode,`。
- 历史升级迁移亦回落 `"sim"`：`apps/web/lib/store.ts:410`（`version < 5` 时 `accountMode: "sim"`）。
- 安全守门（合理、应保留）：`AccountModeSwitch` 在账户数据就绪后，若停在实盘却无就绪默认账户则回落模拟盘——
  `apps/web/components/Header/AccountModeSwitch.tsx:33`
  ```ts
  useEffect(() => {
    if (!accountsQ.isSuccess) return;
    if (accountMode === "live" && !hasReadyDefault) setAccountMode("sim");
  }, [accountsQ.isSuccess, accountMode, hasReadyDefault, setAccountMode]);
  ```

**结论**：全新安装只走初始化器（`store.ts:181`），故默认 `"sim"`；老用户从 localStorage 恢复亦为 `"sim"`。这正是症状 A「默认显示模拟盘」的来源。注意 `AccountModeSwitch.tsx:33` 的水合守门是必要的安全网（无就绪账户不得进实盘），修复时**不应删除**，只需把「初始默认」改为 `"live"`，让有就绪账户的用户落在实盘、无账户者由守门回落模拟盘。

下游消费点（佐证「默认 sim → 看不到实盘下单面板」）：`apps/web/components/Chart/ChartWorkspace.tsx:305`
```tsx
{accountMode === "live" ? (
  <LiveBottomPanel instId={instId} markPrice={markPrice} />
) : ( /* 模拟盘的成交/持仓 tabs */ )}
```

### 根因 B：后端 `live_trading_enabled` 默认 `False`，写接口被守门

- 配置默认值（总开关）：`services/quant/app/config.py:45`
  ```python
  live_trading_enabled: bool = False           # 总开关：False 时 /trading/order|leverage 一律 403
  ```
- 写接口守门：`services/quant/app/api/trading.py:47`
  ```python
  def _require_live_enabled(settings: Settings) -> None:
      if not settings.live_trading_enabled:
          raise HTTPException(status_code=403,
              detail={"code": "LIVE_TRADING_DISABLED", "message": "实盘下单未启用"})
  ```
  应用于 `POST /trading/order`（`trading.py:110`）与 `POST /trading/leverage`（`trading.py:100`）。读接口 config/positions/fills 不受限。
- config 下发该标志：`services/quant/app/okx/trading.py:254`
  ```python
  "liveTradingEnabled": settings.live_trading_enabled,
  ```
- 前端据此渲染禁用横幅与禁用全部控件：`apps/web/components/Live/LiveTradePanel.tsx:151`
  ```tsx
  const blockedReason = !config.liveTradingEnabled
    ? "实盘下单未启用（需后端设置 LIVE_TRADING_ENABLED=true），当前仅可查看真实持仓 / 成交。"
    : ...
  const controlsDisabled = blockedReason !== null || submitting;
  ```
  —— 这正是用户原文引用的横幅（逐字一致），是症状 B 的直接产物。
- 桌面端 env 透传：`desktop/src/lib/config.js:59`-`65` 仅在 `userData/agent.env` 内存在 `LIVE_*` 时才透传给后端子进程；未设置即落回 `config.py` 的 `False`。
- `.env.example` 未文档化 `LIVE_TRADING_ENABLED`（全文无任何 `LIVE_*` 条目），用户无从知晓如何开启。

**结论**：`config.py:45` 的 `False` 默认是症状 B 的根因；守门链（`trading.py:47/100/110` → `okx/trading.py:254` → `LiveTradePanel.tsx:151`）把它一路传导到 UI 横幅。

### 设计背景（为何当初默认全关）
`config.py:46`-`47` 注释明确：「首跨『real trading disabled』红线，故默认全关」；spec（`docs/plans/live-trading-mode/spec.md` §7.3）把真实账户联调定为**手动 opt-in**。即默认 `False` 是**有意为之的安全姿态**，并非疏漏。本轮需求由产品方显式要求把它翻为默认开启，故属「按新需求调整默认姿态」，须在分析中如实标注安全权衡（见影响面与推荐方案）。

---

## 四、影响面

| 维度 | 说明 |
|---|---|
| 受影响功能 | 实盘账户模式（`accountMode === "live"`）的进入默认值；实盘下单 / 设杠杆写接口；下单面板可用性。 |
| 受影响用户 | 全部用户：全新安装默认进模拟盘；任何部署在未显式设 `LIVE_TRADING_ENABLED` 时都无法下单。 |
| 读类不受影响 | `/trading/config`、`/trading/positions`、`/trading/fills` 永远可用（仅查不写），故「仅可查看真实持仓/成交」成立。 |
| 安全红线 | 翻 `live_trading_enabled` 默认为 `True` 会让**任意运行该应用者默认具备真实下单能力**（真实资金）。现有护栏仍在：①每单二次确认弹窗 + 风险勾选（`LiveTradePanel.tsx:323` `OrderConfirmModal`，`store.ts:88` `liveAcknowledged` 瞬态）；②本地名义上限 `live_max_order_notional_usd=1000`（`config.py:48`）；③仅 USDT 本位 SWAP/FUTURES、net_mode 反向单本地拦截（`okx/trading.py`）；④账户模式水合守门（无就绪账户回落 sim）。 |
| 测试影响（关键） | **B（后端）默认翻转零回归**：`services/quant/tests/test_trading_api.py:37`-`39` 用 `_override_settings` 对每个用例**显式**构造 `Settings(live_trading_enabled=...)`（403 用例 line 114-125 显式传 `False`）；前端 `apps/web/components/Live/LiveTradePanel.test.tsx:51` 直接 mock `liveTradingEnabled:false`；全仓 grep `live_trading_enabled` 仅 4 处引用，**无任何用例以裸 `Settings()` 依赖后端默认 `False`**。<br>**⚠️ A（前端）有一处回归，须补正**：症状 A 的 v16 迁移会**打破** `apps/web/lib/store.test.ts:51` —— 用例 `migrates v4 persisted state to v5 with account defaults`（line 41-56）对 `version:4` 持久化态重水合后断言 `expect(s.accountMode).toBe("sim")`；新增 `if (version < 16) accountMode:"live"` 处于迁移链尾，会把该断言期望覆写为 `"live"`，用例失败。此为「翻默认不破坏现有用例」结论的**唯一例外**，修复节点须同步更新（详见「八、评审结论」8.2）。 |

---

## 五、候选修复方案

### 症状 A（账户模式默认）

| 方案 | 改动 | 侵入性 | 风险 | 工作量 |
|---|---|---|---|---|
| **A1（推荐）** | 把初始默认改 `store.ts:181` `accountMode: "live"`，并加一条 migrate（version 16）令老用户持久化态补 `"live"`；保留 `AccountModeSwitch.tsx:33` 水合守门（无就绪账户自动回落 sim）。 | 低（2 处 + 版本号） | 低：守门兜底，无账户者仍落 sim；首帧可能「默认→恢复」一帧闪烁（项目已接受此约定，见 store.ts:459 注释）。 | 小 |
| A2 | 仅改初始默认不加 migrate。 | 极低（1 处） | 老用户 localStorage 已存 `"sim"`，不会变；**对本需求用户（已有持久化态）无效**。 | 极小 |
| A3 | 改由「就绪默认账户存在时自动置 live」的挂载逻辑（在 AccountModeSwitch 内对首次/未显式选择过的态自动升 live）。 | 中（需引入「是否手动选过」标记，避免覆盖用户显式选回 sim） | 中：状态机变复杂，易与守门相互覆盖。 | 中 |

### 症状 B（实盘下单开关默认）

| 方案 | 改动 | 侵入性 | 风险 | 工作量 |
|---|---|---|---|---|
| **B1（推荐）** | 把 `config.py:45` 默认改 `live_trading_enabled: bool = True`；并在 `.env.example` 补 `LIVE_TRADING_ENABLED`（及其余 `LIVE_*`）文档与「设为 false 可一键回退安全姿态」说明。 | 低（1 处默认 + 文档） | 高（安全）：默认具备真实下单能力；但护栏（二次确认 / 名义上限 / 标的与反向单拦截）仍在，且 env 可随时覆盖回 `false`。测试不受影响（见影响面）。 | 小 |
| B2 | 保持代码默认 `False`，改为「环境注入」：dev 在 `services/quant/.env` 写 `LIVE_TRADING_ENABLED=true`；桌面端把它种入 `userData/agent.env`（`desktop/src/lib/config.js` 已会透传 `LIVE_*`）。 | 中（多处 env / 打包种子） | 中：不改全局红线；但**达不到「开箱默认开启」**——每台机器都需带正确 env，且桌面 agent-env 种子需新增该键。 | 中 |
| B3 | B1 + 显式保留 env 覆盖（pydantic 本就支持 env 覆盖代码默认，无需额外代码）。 | 低 | 同 B1，但回退路径文档化更清晰。 | 小 |

---

## 六、推荐方案

**症状 A：采用 A1**；**症状 B：采用 B1（即 B3 形态——翻默认 + 文档化 env 回退）。**

### 理由
1. **直达需求**：本轮需求是「实盘模式**默认开启** + 实盘下单可用」。只有翻「初始默认值」才能做到开箱即默认实盘；B2 的纯 env 方案无法满足「默认」语义。
2. **改动最小、blast radius 可控**：A1 仅动 `store.ts:181` + 新增一条 migrate + 版本号；B1 仅动 `config.py:45` + `.env.example` 文档。已确认现有前后端测试均以显式 override / mock 配置该标志，**翻默认不破坏测试**（影响面表）。
3. **安全护栏不被削弱**：
   - A1 保留 `AccountModeSwitch.tsx:33` 水合守门——无就绪默认账户者仍自动落模拟盘，不会「静默进入无凭证可用的实盘」。
   - B1 保留逐单二次确认 + 风险勾选、本地名义上限 1000 USDT、标的 / net_mode 反向单本地拦截。env `LIVE_TRADING_ENABLED=false` 仍是一键安全阀。
4. **诚实标注红线**：B1 把「real trading disabled」红线的默认姿态从关翻到开，属产品方显式决策；须在 `.env.example` 与提交信息中写明「默认已开启实盘下单，设 `LIVE_TRADING_ENABLED=false` 可回退」，让运维 / 后续维护者知情可回退（对应 `config.py:46` 原注释的更新）。

### 修复点清单（交给修复节点 Subtask #2）
- `apps/web/lib/store.ts:181`：`accountMode: "sim"` → `"live"`。
- `apps/web/lib/store.ts`：persist `version` 15 → 16，新增 `if (version < 16) next = { ...next, accountMode: "live" }`（令老用户持久化态升 live；守门仍兜底）。
- `services/quant/app/config.py:45`：`live_trading_enabled: bool = False` → `True`；同步更新 `config.py:46`-`47` 注释（说明默认已开、env 可回退）。
- `.env.example`：在「后端」段补 `LIVE_TRADING_ENABLED=true`（默认开启，设 false 回退安全姿态），并补充 `LIVE_MAX_ORDER_NOTIONAL_USD` / `LIVE_DEFAULT_TD_MODE` / `LIVE_DEFAULT_LEVERAGE` 说明。
- **保留**：`AccountModeSwitch.tsx:33` 水合守门、所有下单护栏，不得删除。

### 建议回归测试（对应 CLAUDE.md「修 bug 先写复现测试」）
- 前端 store：新增用例断言「全新初始化 `accountMode === "live"`」，以及「version 15 持久化态经 migrate 升到 `"live"`」。
- 前端 `AccountModeSwitch`：断言「有就绪默认账户时保持 live；无就绪账户时水合后回落 sim」（守门不被破坏）。
- 后端：保留 `test_trading_api.py` 的 403 用例（其显式传 `live_enabled=False`，证明 env/override 关时仍能正确守门）；可补一条「默认 `Settings()` 下 `live_trading_enabled is True`」的断言，锁定本次默认翻转。

---

## 七、评审结论（供 Subtask #1 复核）

- 双根因均有**代码与运行实证**支撑，非臆测：
  - A：`store.ts:181`（初始默认 sim）+ `:376` 持久化 + `:410` 迁移回落 + `AccountModeSwitch.tsx:33` 守门。
  - B：`config.py:45`（默认 False）+ `python -c Settings()` 实测 `False` + `trading.py:47/100/110` 守门 + `okx/trading.py:254` 下发 + `LiveTradePanel.tsx:151` 横幅逐字对应用户引用。
- 推荐方案改动面最小、与现有架构 / 命名一致、未引入新依赖；安全红线翻转已显式标注并提供 env 一键回退。
- 待修复节点确认项：A1 改为初始默认 `"live"` 后，需手动跑一次复现步骤，确认「有就绪账户 → 默认落实盘且下单面板可用」「无就绪账户 → 守门回落模拟盘」两条路径均符合预期，且 boot 期无 hydration 告警。

---

## 八、评审结论（Review Verdict · Subtask #1 评审节点）

> 本节由「分析报告评审」节点（Subtask #1）撰写，复核第七节（Subtask #0 自检清单）。评审方式：**直接打开每一处被引代码逐字核对行号与文本**（非转述），并对「翻默认不破坏测试」结论做全仓 grep 验证。

### 8.1 根因证据复核：通过（airtight）

逐条核对，全部命中，行号与文本与源码**逐字一致**：

| 断言 | 复核结果 |
|---|---|
| 症状 A 初始默认 sim | ✅ `apps/web/lib/store.ts:181` 实为 `accountMode: "sim",` |
| 持久化白名单含 accountMode | ✅ `store.ts:376` `accountMode: s.accountMode,`（persist `version: 15`，line 364）|
| 迁移回落 sim | ✅ `store.ts:410` `if (version < 5) { … accountMode: "sim" … }`；v5 之后**无任何迁移再触碰** accountMode → 老用户持久化态恒为 `"sim"` |
| 水合守门 | ✅ `AccountModeSwitch.tsx:33`-`36`：`isSuccess && accountMode==="live" && !hasReadyDefault → setAccountMode("sim")` |
| 下游消费 | ✅ `ChartWorkspace.tsx:305` `{accountMode === "live" ? <LiveBottomPanel/> : …}` |
| 症状 B 后端默认 False | ✅ `config.py:45` `live_trading_enabled: bool = False` |
| 写接口守门 | ✅ `trading.py:49` `if not settings.live_trading_enabled: raise 403`；应用于 `/leverage`（line 100）与 `/order`（line 110）；读接口 config/positions/fills（line 70/79/87）**不调用**守门 |
| config 下发 | ✅ `okx/trading.py:254` `"liveTradingEnabled": settings.live_trading_enabled,` |
| 前端横幅 | ✅ `LiveTradePanel.tsx:151`-`156`，文案与用户原文**逐字一致** |
| 设计背景（有意默认全关） | ✅ `config.py:43` 注释「首跨 real trading disabled 红线，故默认全关」+ spec §7.3「真实账户联调（opt-in，手动/网络标记，不进 CI）」 |

**无反例 / 无替代解释遗漏**：症状 A 两条路径（初始化器 + 持久化恢复）均落 sim；症状 B 为「config 默认 False → 守门链 → UI 横幅」单一传导链，证据闭合，能完整解释两个观察症状。第七节的自检结论予以确认。

### 8.2 候选方案复核与一处补正

**B1（症状 B）—— 复核通过，最小改动成立。** `config.py:45` 改 `True` 即足。边界完备：①env `LIVE_TRADING_ENABLED=false` 仍一键覆盖（pydantic env 覆盖代码默认，字段名即 env 名、大小写不敏感）；②全仓 grep `live_trading_enabled` 仅 4 处（config/okx-trading/api-trading/test），**无任何测试以裸 `Settings()` 依赖默认 `False`**（`test_trading_api.py:39` 每例显式构造，已 grep 核验 `tests/` 内无 `Settings()` 裸调用）；③护栏未被触碰（逐单二次确认 `LiveTradePanel.tsx:323`、名义上限 `config.py:48`=1000、标的/net_mode 本地拦截）。

**A1（症状 A）—— 方向正确，但 Subtask #0 的「不破坏测试」结论有一处遗漏，须补正：**

- ⚠️ **`apps/web/lib/store.test.ts:51` 会被打破**。用例 `migrates v4 persisted state to v5 with account defaults`（line 41-56）注入 `version: 4` 持久化态，`await persist.rehydrate()` 后断言 `expect(s.accountMode).toBe("sim")`。重水合顺序执行**全部**迁移块；新增的 `if (version < 16) accountMode: "live"` 处于迁移链末尾，会把期望的 `"sim"` 覆写为 `"live"`，断言失败。
- 这是全仓**唯一**受影响的前端用例（已 grep：仅 `store.test.ts` 出现 accountMode 断言；`:36/:38` 为 setter 用例不依赖默认值，`:121` 仅作迁移输入未断言；**不存在** `AccountModeSwitch.test.tsx`）。
- **修复节点（Subtask #2）须同步更新该用例**——推荐方案：让 v5 用例只断言其真正新增的默认 `expect(s.simBalance).toBe(DEFAULT_SIM_BALANCE)`（移除其 accountMode 断言），把 accountMode 断言挪入一条新增的「v15→v16 迁移升 `live`」用例（语义更清晰，避免 v5 用例承载 v16 行为）；次选方案为就地把 line 51 期望改为 `"live"` 并加注释。

- **一处需在提交说明/注释中如实标注的语义影响（非阻塞）**：v16 为**无差别强制翻转**——已主动选过 `"sim"` 且持有就绪默认账户的老用户，也会被一次性升到 `"live"`（水合守门只兜「无就绪账户」一类，不会把这类用户弹回 sim）。持久化态无法区分「默认 sim」与「用户主动选 sim」，此为达成「默认开启」需求的可接受取舍，须写入 commit message 以便后续维护者知情。

### 8.3 最终推荐修复（单一裁定）

**采用 A1 + B1，并强制附带 `store.test.ts` 的同步更新。** 完整修复点（交付 Subtask #2，按此为准）：

1. `apps/web/lib/store.ts:181`：`accountMode: "sim"` → `"live"`。
2. `apps/web/lib/store.ts:364` persist `version` `15` → `16`；`migrate`（`store.ts:397`-`457`）链尾新增 `if (version < 16) next = { ...next, accountMode: "live" };`。
3. `services/quant/app/config.py:45`：`live_trading_enabled: bool = False` → `True`，并同步更新 `config.py:43` 注释（默认已开、env 可一键回退）。
4. `.env.example`「后端」段补 `LIVE_TRADING_ENABLED=true`（默认开启，设 `false` 回退安全姿态）及 `LIVE_MAX_ORDER_NOTIONAL_USD` / `LIVE_DEFAULT_TD_MODE` / `LIVE_DEFAULT_LEVERAGE` 说明。
5. **同步更新 `apps/web/lib/store.test.ts`**（见 8.2）：修正受影响的 accountMode 断言，并新增「v15→v16 迁移升 `live`」回归用例。
6. **不得删除**：`AccountModeSwitch.tsx:33` 水合守门、逐单二次确认（`LiveTradePanel.tsx:323`）、名义上限（`config.py:48`）、标的/net_mode 本地拦截。

**裁定理由**：①直达本轮需求（开箱默认实盘 + 实盘下单可用）；②改动面最小，且与现有「逐版迁移」「显式 override 测试」「LIVE_* env 白名单透传」架构一致，未引入新依赖；③安全红线翻转为产品方显式决策，护栏全保留且 `LIVE_TRADING_ENABLED=false` 一键回退；④唯一测试回归已精确定位（`store.test.ts:51`）并给出修法。**否决项**：B2（纯 env）无法满足「默认」语义；A2（仅改初始默认不迁移）对已有持久化态的老用户（含本次报告者）无效；A3（状态机自动升 live）需引入「是否手动选过」标记、与守门相互覆盖、复杂度过高。

### 8.4 Definition of Done 核对
- [x] 证据链闭合（8.1，逐字核对、无遗漏、无反例）。
- [x] 证据不足处已补全（补 `store.test.ts:51` 回归引用 + grep 核验范围，并修正第四节「测试影响」行）。
- [x] 评审结论给出**单一**推荐方案 + 显式理由（8.3）。
- 交付 Subtask #2 的待确认项：落 A1 后手动跑两条复现路径（有/无就绪账户），确认 boot 期无 hydration 告警，并跑 `apps/web` vitest（含 `store.test.ts`）与 `services/quant` pytest 全绿。

