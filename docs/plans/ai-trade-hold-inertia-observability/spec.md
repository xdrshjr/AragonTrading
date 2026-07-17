# AI 自动交易 · 反思观测闭环与连续观望自察 设计方案

> Feature slug: `ai-trade-hold-inertia-observability`
> 版本：**v2**（Subtask #1 评审修订版；v1 为 Subtask #0 方案设计初稿）
> 迭代定位：`ai-trade-reflection-hold-inertia-fix`（第 1 轮修复，工作树已实现、**未提交**）的
> 第 2 轮「继续完善」增量包。
> 关联既有特性：ai-trade-pre-trade-reflection（二次反思）、ai-trade-stats-dashboard（统计看板，
> commit a220044）、ai-auto-trading-wake-interval（墙钟定时唤醒）、ai-trade-multi-symbol（分组流）。

---

## 评审记录（Review Notes · Subtask #1，2026-07-16）

核对基础：已逐一验证 §2.0 全部代码锚点——`session.py` 887/809/935/1016/353/753/974 行、
`prompts.py:318 _trigger_suffix`、`decider.py commit_round` 显式取键、`trace.py new_round_trace`
显式取键、`store.ts:761 patchAiTradeMemberStatus` 全量 spread 与 `AI_TRADE_DECISIONS_MAX=50`、
`useAiTradeGroupStream.routeMemberEvent` 的 status/snapshot.status 路由、`desktop config.js:72`
的 `AI_TRADE_` 前缀透传、`AiTradeStatsDetail` 首成员 aiStats 口径与 `Kpi` 组件、`AiTradeStatsLive`
「数据自 … 起记录」提示条、`backtest.py::_build_payload` 自建 payload（永无 `holdStreak`）、
`test_ai_trade_wake_guard.py` 存在、F4c 既有用例现状（无精确事件序列断言，适配面确为最小）——
均与方案描述一致。四维结论：**可行性**成立（全部挂点真实存在，加法键透传链两条均已验证）；
**一致性**无冲突（F4a 冻结范式 / R-04 双口径纪律 / 零迁移约定均沿既有惯例）；**规模合理**（复用
既有管线与环形缓冲，无第二实现点，不新增后端计算面）；**完整性**存在 4 处 P1 级缺口，已按下表
就地修复并升 v2。

| # | 严重度 | 位置 | 问题 | 处置 |
|---|---|---|---|---|
| R2-01 | P1 | §六-8 | 冻结门禁写作「`git diff` 空」不可满足：`reflection.py`/`decider.py`/`backtest.py` 在工作树已携带第 1 轮**未提交** hunk，diff 本就非空；照写会让实施/验收把 pre-existing 差异误判为本轮违规 | 已改为「以第 1 轮落地态为基线的零新增改动」口径（见 §六-8） |
| R2-02 | P1 | §2.2 O3 / §六-6 | 前端镜像口径欠精确：后端 `failClosed` 计数位于 `if verdict is not None` 块**之外**（独立于裁决存在性）；且今日 fail-closed 轮实际带 `verdict="abort"`（`decider._fail_closed` 构造）故**同时计入 runs**。镜像若把 failClosed 嵌进 verdict 非空分支，遇后端形状演化即静默漂移 | O3 镜像注释与 §六-6 用例已补两条字面量钉（fail-closed 轮计入 runs+failClosed；verdict=null 合成样本仍计 failClosed 不计 runs） |
| R2-03 | P1 | §2.1 N2 / §六-1 | streak 三分支未覆盖 `low_confidence`：decide() 的置信度门禁返回 `("hold", "low_confidence")`（decider.py:359），落入「hold 且有降级→不变」分支——语义成立（弱意愿中性处理，既不强化也不打断）但未钉死，实施者可能按「模型已展示意愿→归零」自行走样 | N2 docstring 与 §六-1 表驱动用例已显式加该分支 |
| R2-04 | P1 | §2.1 N3 | 「只影响本轮 prompt」表述不实：`holdStreak` 键经 `commit_round` 对 `build_decision_prompt(payload)` 的重放（decider.py:492）进入 `_context` 该轮 user 消息，注记文本随短窗口存续至裁剪/摘要——与 F4a trigger 注记同范式、有意行为；但原表述会诱导实施者写「_context 不含注记」的错误冻结断言、或给 commit_round 加剥键分支 | 已改写 N3 说明：注记随上下文如实存续；`commit_round` 零改动、不得剥键 |
| R2-05 | P2 | §2.2 O5 | `info()` 不带（本轮也不扩）`wakeSkips`/`holdStreak`：REST 首刷（group_status→info）到 WS snapshot.status 到达之间，live 观测条短暂呈现「—」 | 有意降级（WS 紧随发现即连），O5 已加注说明；不扩 info() 保持最小改动面 |
| R2-06 | P2 | §三 #4/#6 | O1 status 推送用例在文件计划 #4 与 #6 重复列出，归属含糊 | 已统一归 #6（wake_interval 文件），#4 删除该项 |
| R2-07 | P2 | §2.2 O2 | `decisionsDigest` 前端类型缺 `reflectionVerdict`（后端 `_digest_decisions` 已下发，pre-existing 同文件类型缺口） | 可选顺手补齐（一行、零风险）；不入本轮验收，不补不算未完成 |
| R2-08 | P2 | §七 R-6 | `session.py` 1063 行已超 CLAUDE.md 800 行阈值（pre-existing），本轮净增 ~25 行继续加深；该豁免未在 `.claude-index/config.md` 显式记录 | 维持本轮不拆的判断（拆分属独立重构轮）；建议后续把 finalize 富化纯函数群拆出并补记录 |

无 P0。四处 P1 已全部按处置列在正文就地修复；P2 为记录性意见，不阻塞实施。

---

## 一、Overview（概述）

第 1 轮修复（F1 反思文案校准 / F2 否决意图入 journal / F3 摘要反自强化 / F4 重复数据轮去重 /
F5 aiStats 反思四计数）已在工作树完整落地：`session.py` 的 `_compute_ai_stats` 产出
`reflectionRuns/reflectionConfirms/reflectionAborts/reflectionFailClosed`（R-04 分流口径），
status 事件携带 `wakeSkips`，`_finalize_run` 把 `wakeSkips` 落进 `metrics_json.aiStats`。但这些
观测出口**在前端零引用**：统计看板（`AiTradeStatsDetail` 的「AI 决策概况」卡、`AiTradeStatsLive`）
仍只展示旧五键（决策轮数/观望占比/平均置信度/共识降级/动作徽标），用户既看不到「反思否决率是否
已从 7/7=100% 降下来」，也看不到「唤醒守卫抑制了多少次重复决策」。第 1 轮 DoD 的量化验收指标
（否决率显著低于 1.0）目前只能靠人工查 DB 的 `metrics_json`——修复效果对用户不可见，闭环没有关上。

本轮做两件事。**其一（O 组，观测闭环）**：把第 1 轮埋好的计数呈现到统计看板——历史详情的
「AI 决策概况」卡新增「反思复核」观测行（全成员合计），「当前」页新增「运行观测」条（逐成员实时
反思计数 + 唤醒抑制 + 连续观望轮数）；前端按与后端**逐字相同的口径**从既有 decision 事件环形缓冲
（两端同为 50 上限）派生 live 反思计数，不新增任何后端计算。**其二（N 组，连续观望自察）**：给
「记忆惯性」补一道确定性的、可配置的自察触发器——会话追踪「连续真实观望轮数」（`hold_streak`，
第一阶段非 hold 即重置、降级轮不计），当连续观望达到阈值（默认 10 轮，`0` 关闭）时在决策 prompt
中注入一条**对称措辞**的自察注记：既明说「历史观望不构成继续观望的理由」，也明说「这不是必须
开仓的指令」，并要求若继续观望须指名当前最关键的一条反对开仓的具体证据。这是第 1 轮 F3「摘要反
自强化」的兜底：即使摘要仍被行文美化，一条基于确定性计数的醒目注记也能打断路径依赖。

与第 1 轮同一条纪律：**全部确定性安全层逐字不变**——本轮对 `reflection.py`、`decider.py`、
`wake_guard.py`、`backtest.py` **零改动**；prompt 注入沿用 F4a 的「payload 键缺失 → 输出逐字节
不变」范式，回测 payload 永不携带新键，回测行为天然冻结。所有 wire 变更均为加法可选键，
零 DB 迁移、零 REST/WS 破坏、旧前端与老历史记录全部优雅降级。

---

## 二、Technical design（技术设计）

### 2.0 现状关键代码路径（已逐一核对源码）

```
后端（第 1 轮已落地，未提交）：
  session._status_payload (session.py:887)          # 已含 wakeSkips
  session._emit_decision  (session.py:809)          # data 含 degradeReason/reflectionVerdict → _decisions(≤50) + WS
  session._finalize_run   (session.py:935)          # metrics["aiStats"] = _compute_ai_stats(...) + wakeSkips
  session._compute_ai_stats (session.py:1016)       # 反思四计数（R-04 分流：aborts 只计真实否决）
  session._count_wake_skip (session.py:353)         # R-02 节流：每唤醒周期至多 +1（纯计数，不推事件）
  session._build_decision_payload (session.py:753)  # 决策 prompt 的唯一装配点
  prompts._trigger_suffix (prompts.py:318)          # F4a 范式：payload 键值分支、缺失即逐字节不变
  _REFLECTION_FAIL_CLOSED_REASONS (session.py:974)  # 四个 fail-closed 码（frozenset）

前端（stats-dashboard 已提交 a220044；分组流已提交）：
  useAiTradeGroupStream.routeMemberEvent            # status/snapshot.status → patchAiTradeMemberStatus
  store.patchAiTradeMemberStatus (store.ts:761)     # {...m.session, ...status} 全量 spread —— 加法键自动透传
  AiTradeGroupMemberState.decisions                 # decision 事件环形（AI_TRADE_DECISIONS_MAX=50，与后端同窗）
  AiTradeStatsDetail.AiStatsCard                    # 「AI 决策概况」卡（现仅旧五键）
  AiTradeStatsLive                                  # 「当前」页（现仅 PnL 汇总）
  aiTradeHistoryTypes.AiTradeRunMetrics.aiStats     # 现仅旧五键类型
```

两条透传链路均已验证是「加法键免费直达」：status/snapshot.status 走全量 spread 合并进
`member.session`；decision 事件原样入 `member.decisions` 环形。因此 O 组前端工作以类型声明 +
纯派生函数 + 渲染为主，后端只需补一处「守卫命中时推 status」（见 2.2）。

### 2.1 N — 连续观望自察（hold-streak 追踪 + 条件化 prompt 注记）

**N1 配置**（`config.py`）：

```python
# AI 自动交易连续观望自察阈值（hold-inertia-observability N1）：第一阶段连续真实观望（非降级轮）
# 达到该轮数后，决策 prompt 注入一条对称措辞的自察注记；0 = 完全关闭（prompt 逐字节回退现行为）。
ai_trade_hold_streak_note_after: int = Field(default=10, ge=0)
```

env 名 `AI_TRADE_HOLD_STREAK_NOTE_AFTER` 已被 desktop `config.js:72` 的 `AI_TRADE_` 前缀透传
白名单覆盖，**desktop 零改动**；config.py 必须真的声明（`extra="ignore"` 会静默丢弃未声明 env）。

**N2 会话追踪**（`session.py`）：

- `__init__` 增 `self.hold_streak = 0`、`self.max_hold_streak = 0`。
- 新私有方法（放 `_count_wake_skip` 旁，语义注释齐全）：

```python
def _update_hold_streak(self, stage1_action: str, stage1_degrade: str | None) -> None:
    """连续真实观望计数（hold-inertia-observability N2）。

    以**第一阶段**（decider.decide 直接返回值，共识门禁/反思之前）为准：
    - 非 hold → 归零（模型已展示交易意愿——即使随后被共识门禁或反思否决，惯性已在第一阶段打破）；
    - hold 且无降级 → +1（模型真实观望）；
    - hold 且有降级 → 不变（既非充分意愿也非真实观望，不计入亦不打断）。该分支覆盖两类：
      llm_error/timeout 等安全回退，以及 low_confidence 风险门禁改写轮——decide() 对置信度
      不达标的开仓意愿返回 ("hold", "low_confidence")（decider.py:359），弱意愿按中性处理，
      **不归零**（R2-03 语义钉，勿按「已展示意愿→归零」走样）。
    """
    if stage1_action != "hold":
        self.hold_streak = 0
    elif stage1_degrade is None:
        self.hold_streak += 1
        self.max_hold_streak = max(self.max_hold_streak, self.hold_streak)
```

- 调用点：`_run_decision_round` 中 `decider.decide(...)` 返回后**立即**调用
  `self._update_hold_streak(decision.action, degrade)`（在共识门禁与反思之前——此时的
  `decision/degrade` 正是第一阶段原始输出）。in-flight 跳过轮（`_emit_decision_skipped`）不经过
  `_run_decision_round`，计数天然不变；决策轮异常在 decide 之前抛出则本轮不计。

**N3 payload 条件注入**（`session.py::_build_decision_payload`）：

```python
# N3：连续观望达到阈值才注入（0=关闭）。payload 在 decide 之前装配，故值 = 截至上一轮的连续观望数。
threshold = self.executor.settings.ai_trade_hold_streak_note_after
if threshold > 0 and self.hold_streak >= threshold:
    payload["holdStreak"] = self.hold_streak
```

阈值未达 / 关闭 / 回测（`backtest.py` 自建 payload，永无此键）→ 键缺失 → prompt 逐字节不变
（F4a 冻结范式）。`holdStreak` 不进 journal（`commit_round` 显式取键，未取它）、不进轨迹
（`trace.py::new_round_trace` 显式取键）。**范围澄清（R2-04）**：注记文本除本轮决策 prompt 外，
还会经 `commit_round` 对 `build_decision_prompt(payload)` 的重放（decider.py:492）进入 `_context`
中该轮的 user 消息——与 F4a trigger 注记同范式（模型上下文如实保留「它当轮看到的输入」），随
窗口裁剪 / 滚动摘要自然消退。这是有意行为：`commit_round` **零改动**，不得为剥键引入新分支，
测试亦不得断言「_context 不含注记」。

**N4 prompt 注记**（`prompts.py`）：新模块级纯函数，镜像 `_trigger_suffix` 范式：

```python
def _format_hold_streak(hold_streak) -> str:
    """连续观望自察注记（hold-inertia-observability N4）：payload 无键/非正整数 → 空串（逐字节冻结）。

    措辞必须**对称**（R-01 教训的正向应用）：既打断「历史观望→继续观望」的路径依赖，也明确
    「不是必须开仓的指令」——防止注记本身变成过度交易的推手；要求给出具体证据与 F1 的
    「有名有姓才否决」同构，方向相反。
    """
    if not isinstance(hold_streak, int) or hold_streak <= 0:
        return ""
    return (f"连续观望自察：截至上一轮，你已连续 {hold_streak} 轮维持观望（不含系统降级轮）。"
            "历史观望不构成继续观望的理由，这也不是必须开仓的指令——请只以本轮最新证据独立重估"
            "开多/开空/平仓/观望；若研判后仍选择观望，请在研判中指名当前最关键的一条反对开仓的"
            "具体证据（不得以「此前观望被验证正确」作为理由）。")
```

`build_decision_prompt` 在会话盈亏行（`parts` 第三条）之后装配：

```python
streak_line = _format_hold_streak(payload.get("holdStreak"))
if streak_line:                          # 键缺失时整段省略（零回归；回测 payload 无此键）
    parts.append(streak_line)
```

**N5 观测出口**（`session.py`，全部加法）：

- `_emit_decision` 的 `data` 增 `"holdStreak": self.hold_streak`（decide 后已更新，语义 =
  「截至本轮」；每轮随 decision 事件下发，是前端 live 值的最新真源）。
  `_emit_decision_skipped` **不加**此键（跳过轮不是决策，前端键可选）。
- `_status_payload` 增 `"holdStreak": self.hold_streak`（重连 snapshot.status 兜底）。
- `_finalize_run` 富化 try 块内、`wakeSkips` 行旁追加
  `metrics["aiStats"]["maxHoldStreak"] = self.max_hold_streak`（环形样本算不出全程最大值，
  与 `wakeSkips` 同为会话级直取；绝不抛的既有 try/except 覆盖）。

### 2.2 O — 观测闭环（后端一处补推 + 前端呈现）

**O1 守卫命中可见性**（`session.py`，后端唯一行为变更）：第 1 轮的 `_count_wake_skip` 只改内存
计数、不推事件——守卫命中期间恰恰**没有任何事件**发出，live 面板的 `wakeSkips` 只能停留在连接时
snapshot 的旧值。改 `_count_wake_skip` 返回 `bool`（本次是否计数），`_maybe_interval_wake` 在
计数成功时补推一次 status：

```python
if self._count_wake_skip(now):
    await self._push({"type": "status", "data": self._status_payload()})
return
```

推送频率被 R-02 节流天然封顶（每唤醒周期至多一次，3 分钟周期 = 每小时至多 ~20 条小事件），
不新增节流机制。`_count_wake_skip` 本体除 `return True/False` 外逐字不变。

**O2 前端类型**（全部加法可选，老数据缺键即优雅降级）：

- `aiTradeTypes.ts`：`AiTradeStatusEvent` 与 `AiTradeSessionRow` 增
  `wakeSkips?: number; holdStreak?: number;`（第 1 轮后端已发 `wakeSkips`，本轮补齐类型）；
  `AiTradeDecision` 增 `holdStreak?: number;`。
- `aiTradeHistoryTypes.ts`：`AiTradeRunMetrics.aiStats` 增六个可选键：
  `reflectionRuns?/reflectionConfirms?/reflectionAborts?/reflectionFailClosed?/wakeSkips?/
  maxHoldStreak?: number`。

**O3 前端口径派生纯库**（**新建** `apps/web/lib/aiTradeObservability.ts`）：

```ts
// 与后端 session._compute_ai_stats / _REFLECTION_FAIL_CLOSED_REASONS 逐字镜像的口径（勿单侧改动）：
// aborts 只计模型真实否决（verdict==="abort" 且 degradeReason==="reflection_abort"，R-04 分流）；
// failClosed 计四个基础设施码，且判定**独立于 reflectionVerdict 是否存在**——镜像后端分支位置：
// 该计数在 `if verdict is not None` 块之外（R2-02，绝不可嵌进 verdict 非空分支）。注意今日
// fail-closed 轮实际带 verdict="abort"（decider._fail_closed 构造），故同时计入 runs——两条
// 均由 §六-6 字面量用例钉死。计数窗口 = decision 环形 ≤50 条，与后端 _decisions 同窗同口径。
export const REFLECTION_FAIL_CLOSED_REASONS: ReadonlySet<string> = new Set([
  "reflection_timeout", "reflection_llm_error", "reflection_no_tool", "reflection_invalid"]);

export interface ReflectionCounts { runs: number; confirms: number; aborts: number; failClosed: number; }
export function computeReflectionCounts(decisions: AiTradeDecision[]): ReflectionCounts;

// 历史详情「全成员合计」：五个计数键求和（缺键按 0）、maxHoldStreak 取最大值；
// 没有任何成员带任何新键（老会话记录）→ null（呈现层据此隐藏整行，不显示一排误导性的 0）。
export interface AiObservability {
  reflectionRuns: number; reflectionConfirms: number; reflectionAborts: number;
  reflectionFailClosed: number; wakeSkips: number; maxHoldStreak: number;
}
export function aggregateAiObservability(members: AiTradeHistoryMember[]): AiObservability | null;

// live 连续观望取值：decision 环形自尾向头首个带 holdStreak 的条目（最新真源）→ 回退
// session.holdStreak（snapshot/status 合并值）→ null（老后端，呈现「—」）。
export function latestHoldStreak(
  decisions: AiTradeDecision[], session?: { holdStreak?: number } | null): number | null;
```

三个函数均为无副作用纯函数、不改入参。同步在 `session.py` 的
`_REFLECTION_FAIL_CLOSED_REASONS` 注释补一行镜像点指引
（「前端 apps/web/lib/aiTradeObservability.ts 镜像本口径，改动须双侧同步」），防单侧漂移。

**O4 历史详情呈现**（`AiTradeStatsDetail.tsx`）：`DetailBody` 计算
`observability = aggregateAiObservability(detail.members)` 并传入 `AiStatsCard`；卡内在既有
四 KPI 行与动作徽标之间插入（`observability` 为 null 时整块不渲染）：

```tsx
{observability && (
  <div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-6">
    <Kpi label="反思轮数" value={`${observability.reflectionRuns}`} />
    <Kpi label="反思放行" value={`${observability.reflectionConfirms}`} />
    <Kpi label="真实否决" value={`${observability.reflectionAborts}`} />
    <Kpi label="故障保守" value={`${observability.reflectionFailClosed}`} />
    <Kpi label="唤醒抑制" value={`${observability.wakeSkips}`} />
    <Kpi label="最长连续观望" value={`${observability.maxHoldStreak} 轮`} />
  </div>
)}
```

并在块下加一行 10px 注脚：「反思与否决为近 50 轮保留样本口径；故障保守 = 反思子调用基础设施
失败后的安全跳过，不代表模型判断。」注意既有卡对旧五键沿用「首个带 aiStats 成员」的口径
（pre-existing，本轮不动）；新行是**全成员合计**——两种口径并存已在注脚与代码注释中写明。

**O5 当前页运行观测条**（`AiTradeStatsLive.tsx`）：在「数据自 … 起记录」提示条之后插入
「运行观测」小节，逐成员一行（成员来自 `group.members`，与既有 useMemo 同源）：

```
BTC-USDT-SWAP   反思 3（放行 2 · 否决 1 · 故障 0）· 唤醒抑制 5 · 连续观望 12 轮
```

- 反思计数 = `computeReflectionCounts(m.decisions)`（环形 ≤50，与后端同窗）；
- 唤醒抑制 = `m.session?.wakeSkips`，缺失呈现「—」（老后端 / 尚未收到 status）；
- 连续观望 = `latestHoldStreak(m.decisions, m.session)`，null 呈现「—」。

实现为文件内小组件 `MemberObservabilityRow`（纯呈现，无状态），样式沿用该文件既有
border/surface token。分组停止后本条随「当前」页整体消失，历史口径见 O4。

**首刷降级注（R2-05）**：REST 首次发现（group_status→`info()`）不带 `wakeSkips`/`holdStreak`
（本轮**不扩** `info()`，保持最小改动面），两键在 WS 连接后的 snapshot.status 即补齐——期间的
短暂「—」为有意降级，与老后端缺键走同一呈现路径，无需特判。

### 2.3 明确不做（Non-goals）

- **不**改 `reflection.py` / `decider.py` / `wake_guard.py` / `backtest.py`——确定性安全层与
  回测决策脑零触碰；回测 payload 无 `holdStreak` 键，prompt 逐字节冻结。
- **不**改回测统计口径（`reflection_aborts` 合计口径维持，第 1 轮 R-04 已注明不得引入第三种口径）。
- **不**做「已污染会话摘要自愈」（沿第 1 轮 R-7：升级后重启在跑 AI-trade 会话，含分组全部成员）。
- **不**动第 1 轮任何既有文案与守卫语义；`_count_wake_skip` 仅加返回值。
- **不**改 REST 端点 / DB models（零迁移；`maxHoldStreak` 走自由格式 `metrics_json`）。
- **不**给 `_emit_decision_skipped` 加 `holdStreak`（跳过轮不是决策）。
- **不**在前端把旧五键卡改成全成员合计（pre-existing 首成员口径，另行迭代）。

---

## 三、File / module change plan（文件 / 模块变更计划）

| # | 文件 | 操作 | 一句话意图 |
|---|---|---|---|
| 1 | `services/quant/app/config.py` | 修改 | N1 声明 `ai_trade_hold_streak_note_after: int = Field(default=10, ge=0)`（0=关闭）。⚠混合文件：工作树内另有他特性在制 hunk（`ai_trade_enabled` 翻转），提交须 hunk 级精确暂存 |
| 2 | `services/quant/app/ai_auto/session.py` | 修改 | N2 `hold_streak/max_hold_streak` + `_update_hold_streak`（decide 后立即调用）；N3 payload 条件注入；N5 decision/status 事件加 `holdStreak` + `_finalize_run` 富化 `aiStats.maxHoldStreak`；O1 `_count_wake_skip` 返回 bool、命中计数即推 status；O3 `_REFLECTION_FAIL_CLOSED_REASONS` 注释补前端镜像点指引 |
| 3 | `services/quant/app/ai_auto/prompts.py` | 修改 | N4 新纯函数 `_format_hold_streak` + `build_decision_prompt` 条件装配（键缺失 → 输出逐字节不变） |
| 4 | `services/quant/tests/test_ai_trade_session.py` | 修改 | N2 streak 规则表驱动（非 hold 归零/真实 hold +1/降级轮不变——含 low_confidence 分支，R2-03）；N3 阈值注入三分支（达阈/未达/关闭）；N5 decision 事件与 `_status_payload` 带 `holdStreak`、finalize `aiStats.maxHoldStreak`（O1 推送用例统一归 #6，勿双处重复，R2-06） |
| 5 | `services/quant/tests/test_ai_trade_prompts.py` | 修改 | N4 注记锚点断言（「连续观望自察」+ 轮数 + 对称锚点「这也不是必须开仓的指令」「若研判后仍选择观望」）；无 `holdStreak` 键时输出不含「连续观望自察」且既有全部锚点原样通过（冻结钉） |
| 6 | `services/quant/tests/test_ai_trade_wake_interval.py` | 修改 | O1 适配：若既有 F4c 用例对事件序列有精确断言，按「命中计数轮多一条 status」如实更新；节流语义断言（同窗至多一条）新增 |
| 7 | `apps/web/lib/aiTradeTypes.ts` | 修改 | O2 `AiTradeStatusEvent`/`AiTradeSessionRow` 增 `wakeSkips?/holdStreak?`；`AiTradeDecision` 增 `holdStreak?` |
| 8 | `apps/web/lib/aiTradeHistoryTypes.ts` | 修改 | O2 `aiStats` 增六个可选观测键 |
| 9 | `apps/web/lib/aiTradeObservability.ts` | **新建** | O3 纯派生库：`computeReflectionCounts` / `aggregateAiObservability` / `latestHoldStreak` / `REFLECTION_FAIL_CLOSED_REASONS`（后端口径逐字镜像） |
| 10 | `apps/web/components/Agent/aiTradeStats/AiTradeStatsDetail.tsx` | 修改 | O4 「AI 决策概况」卡新增「反思复核」六 KPI 观测行（全成员合计，null 隐藏）+ 口径注脚 |
| 11 | `apps/web/components/Agent/aiTradeStats/AiTradeStatsLive.tsx` | 修改 | O5 「运行观测」小节：逐成员反思计数（环形派生）+ 唤醒抑制 + 连续观望（缺失「—」） |
| 12 | `apps/web/lib/aiTradeObservability.test.ts` | **新建** | O3 口径用例：真实否决与故障保守分流、confirm 计数、verdict 为 null 忽略、四码集合逐字断言、aggregate 求和/取最大/全缺 null、latestHoldStreak 三级回退 |
| 13 | `apps/web/components/Agent/AiTradeStatsPanel.test.tsx` | 修改 | O4/O5 渲染断言：live 运行观测条呈现计数与「—」降级；历史详情带新键显示观测行、老记录（无新键）不显示 |

**明确不改**：`services/quant/app/ai_auto/reflection.py`、`decider.py`、`wake_guard.py`、
`backtest.py`、`services/quant/app/api/*`、`desktop/src/lib/config.js`（`AI_TRADE_` 前缀透传已
覆盖新 env）、`.env.example`（工作树他特性在制，避免加深混合文件）、DB models（零迁移）、
`useAiTradeGroupStream.ts` / `store.ts`（全量 spread 已自动透传加法键）。

**提交范围注记（Subtask #3 必读）**：第 1 轮（`ai-trade-reflection-hold-inertia-fix`，14 文件）
在工作树**尚未提交**，且与本轮改动同文件叠加（`session.py`/`prompts.py`/两个 test 文件）。两轮
同属一条修复脉络，Subtask #3 应把**两轮 hunk 合并为一个 feat 提交**；混合文件仅 `config.py`
（剔除他特性 `ai_trade_enabled` 翻转 hunk，行级 `git apply --cached` 精确暂存），其余他特性脏文件
（`.env.example`、`desktop/src/lib/config.js`、`app/api/live_auto.py`、`engine/factors/*`、
`tests/test_factor_*`、`tests/test_config.py`、`desktop/scripts/*`）一律不入本提交。

---

## 四、Interface design（接口设计）

**REST：零变更。**

**WebSocket（全部加法，旧前端忽略未知字段）**：

- `decision` 事件增 `holdStreak: number`（截至本轮的连续真实观望轮数；跳过轮事件无此键）。
- `status` 事件 / `snapshot.status` 增 `holdStreak: number`（`wakeSkips` 第 1 轮已有，本轮仅补
  前端类型）；守卫命中计数时新增一次 status 推送（频率 ≤ 每唤醒周期一次）。
- `metrics_json.aiStats` 增 `maxHoldStreak: number`（会话级全程最大连续观望）。

**内部 Python 签名**（均私有/模块级，无对外破坏）：

```python
# session.py（新增私有方法；_count_wake_skip 增返回值，逻辑逐字不变）
def _update_hold_streak(self, stage1_action: str, stage1_degrade: str | None) -> None: ...
def _count_wake_skip(self, now: float) -> bool: ...

# prompts.py（新增模块级纯函数，镜像 _trigger_suffix 范式）
def _format_hold_streak(hold_streak) -> str: ...
```

**TypeScript 公开签名**（`apps/web/lib/aiTradeObservability.ts`，见 §2.2 O3 全文）：
`computeReflectionCounts(decisions) -> ReflectionCounts`、
`aggregateAiObservability(members) -> AiObservability | null`、
`latestHoldStreak(decisions, session?) -> number | null`。

**配置 / 环境变量**：`AI_TRADE_HOLD_STREAK_NOTE_AFTER`（int；默认 `10`；`0` 关闭自察注记；
desktop 经 `AI_TRADE_` 前缀白名单自动透传，无需改 desktop）。

---

## 五、Data model（数据模型）

**零 DB 迁移。** 仅内存 / 自由格式 JSON / wire 形状加法：

1. 会话内存（`session.py`）：`hold_streak: int`、`max_hold_streak: int`（会话生命周期内有效，
   重启归零——与滚动摘要同为运行期态，符合「修复随新会话生效」的既有运维口径）。
2. 决策 payload（仅达到阈值的轮）：`{"holdStreak": int}`——不进 journal、不进轨迹、不落库
   （注记文本作为该轮 prompt 的一部分随 `_context` user 消息存续，见 §2.1 N3 范围澄清，R2-04）。
3. `decision` 事件 / `_decisions` 环形条目：增 `holdStreak: int`（跳过轮条目无此键）。
4. `status` / `snapshot.status`：增 `holdStreak: int`。
5. `metrics_json.aiStats` 加法键：`maxHoldStreak: int`（与第 1 轮 `wakeSkips` 同为
   `_finalize_run` 富化直取，非 `_compute_ai_stats` 样本口径）。
6. 前端瞬态派生（不落任何存储）：`ReflectionCounts`、`AiObservability`（形状见 §2.2 O3）。

---

## 六、Testing & acceptance criteria（测试与验收标准）

**先红后绿（新行为用例）：**

1. `test_hold_streak_rules`（session，表驱动直测 `_update_hold_streak`）：
   `("hold", None)` → +1 且 max 跟随；`("open_long", None)` → 归零；`("open_long", 任意)` → 归零
   （被门禁/反思否决的轮以第一阶段意愿为准）；`("hold", "llm_error")` → 不变；
   `("hold", "low_confidence")` → 不变（decide() 置信度门禁改写轮，弱意愿中性处理，R2-03）。
2. `test_payload_hold_streak_threshold`：显式 `Settings(ai_trade_hold_streak_note_after=2)`（防
   默认值漂移，沿 R-03 显式构造范式）——`hold_streak=2` 时 `_build_decision_payload` 含
   `holdStreak=2`；`=1` 时无该键；`Settings(...=0)` 且 `hold_streak=99` 时仍无该键。
3. `test_decision_prompt_hold_streak_note`（prompts）：`payload["holdStreak"]=12` → 输出含
   「连续观望自察」「12 轮」与对称锚点「这也不是必须开仓的指令」「若研判后仍选择观望」；
   无键 payload → 输出不含「连续观望自察」（冻结钉），既有全部锚点断言（含「决策工具。**」收尾）
   原样通过。
4. `test_decision_event_and_status_carry_hold_streak`（session 集成，桩 LLM 连续 hold）：
   decision 事件 `data["holdStreak"]` 随轮递增；`_status_payload()["holdStreak"]` 同步；
   `_finalize_run` 后 `metrics["aiStats"]["maxHoldStreak"]` == 历史最大值。
5. `test_wake_skip_pushes_status_once_per_window`（wake_interval）：守卫命中且计数成功 → 恰推
   一条 status（含递增后的 `wakeSkips`）；同一唤醒周期窗口内再次命中 → 不再推送、计数不再递增
   （R-02 节流语义对事件与计数一致）。
6. `aiTradeObservability.test.ts`（新建，前端口径镜像）：
   - 真实否决（verdict="abort"+degradeReason="reflection_abort"）计入 aborts；
     fail-closed 四码计入 failClosed 且**不**计入 aborts；confirm 计入 confirms；
     `reflectionVerdict` 为 null/undefined 的轮不计 runs；
   - **分支位置镜像双钉（R2-02）**：真实 fail-closed 轮形状
     （verdict="abort"+degradeReason="reflection_timeout"）→ runs+1 且 failClosed+1 且 aborts
     不计；合成样本（reflectionVerdict=null 但 degradeReason 为 fail-closed 码）→ failClosed+1
     且 runs 不计（镜像后端 failClosed 判定位于 `if verdict is not None` 块之外）；
   - `REFLECTION_FAIL_CLOSED_REASONS` 与后端四码**逐字**相等（字面量断言，防双侧漂移）；
   - `aggregateAiObservability`：跨成员求和、`maxHoldStreak` 取最大、缺键按 0、全体成员均无
     任何新键 → null；
   - `latestHoldStreak`：取环形最尾带键条目 → 回退 session.holdStreak → 双缺 null。
7. `AiTradeStatsPanel.test.tsx`：live「运行观测」条按 decisions/session 呈现计数、老后端字段
   缺失呈现「—」；历史详情 aiStats 带新键 → 显示「反思复核」行（合计值正确）、老记录无新键 →
   不渲染该行（无一排 0）。

**零回归冻结门禁：**

8. 本轮对 `reflection.py`/`decider.py`/`wake_guard.py`/`backtest.py` **零新增改动**。注意
   （R2-01）：其中 `reflection.py`/`decider.py`/`backtest.py` 在工作树已携带第 1 轮**未提交**
   hunk，`git diff` 本就非空——门禁口径 = 以「第 1 轮落地后的工作树」为基线，本轮实施前后这
   四个文件的 `git diff` 输出**逐字节一致**（实施者开工前对四文件 diff 存档 / 记 hash，收工后
   比对）；`test_ai_trade_reflection.py`/`test_ai_trade_decider.py`/`test_ai_backtest_runner.py`/
   `test_ai_trade_wake_guard.py` **一字不改**全绿。
9. `Settings(ai_trade_hold_streak_note_after=0)` 或阈值未达时，`build_decision_prompt` 输出与
   升级前**逐字节相同**（payload 无键路径复用第 1 轮 F4a 冻结断言风格）；回测全链路（payload
   自建、永无新键）行为零漂移。
10. 既有 `test_ai_trade_wake_interval.py` 用例中若有对事件条数的精确断言，仅按 O1「命中计数轮
    多一条 status」如实适配，断言不得删除或放松；其余用例一字不改。

**验收 DoD：**

- 后端 `services/quant` 全量 pytest 零回归（工作树已知的 factor-expansion 孤儿测试红为
  pre-existing 他特性遗留，报告中列明而非静默）。
- 前端触达套件（`NODE_ENV=test` 运行，防 React act() 环境误报）全绿；
  `WeightBatchArena.test` 的 tsc 报错为 pre-existing，列明而非静默。
- 启动一个 AI-trade 会话：「当前」页出现运行观测条且守卫命中后 `唤醒抑制` 在一个唤醒周期内
  自增；连续观望达阈值后 agent_trace 输入快照可见自察注记文本；停止会话后「历史」详情出现
  「反思复核」六 KPI（含 `maxHoldStreak`），老历史会话详情无此行且不报错。

---

## 七、Risks & mitigations（风险与缓解）

| # | 风险 | 缓解 |
|---|---|---|
| R-1 | 自察注记矫枉过正 → 诱导过度交易 | 措辞对称（明写「不是必须开仓的指令」+ 要求指名反对开仓的具体证据）；仅注入 prompt，不触碰任何确定性护栏（置信度/共识门禁/反思/档位/预算全部不变）；阈值可调、`0` 一键关闭；O 组计数持续观测开仓率可随时回调 |
| R-2 | 双端口径漂移（后端 Python 四码/分流规则 vs 前端 TS 镜像） | 双侧字面量断言（§六-6 四码逐字相等）+ 双侧镜像点注释互指；口径逻辑集中在各自单一纯函数，无第二实现点 |
| R-3 | 守卫命中补推 status 打破既有事件序列断言 | 推送被 R-02 节流封顶（每唤醒周期 ≤1 条）；文件计划 #6 显式列出适配方式（如实 +1 条 status，断言不得放松）；除此之外零事件变更 |
| R-4 | streak 语义歧义（降级轮/否决轮怎么算）导致实现走样 | `_update_hold_streak` docstring 三分支表述 + §六-1 表驱动测试钉死；以第一阶段输出为准的理由（惯性在第一阶段打破）写入注释 |
| R-5 | 老历史记录 / 旧后端缺新键 → 前端 NaN/一排 0 误导 | 全部键可选 + `aggregateAiObservability` 全缺返 null 隐藏整行 + live 缺失呈现「—」；§六-7 渲染降级用例守门 |
| R-6 | `session.py`（1063 行，pre-existing 越限）继续膨胀 | 本轮净增 ~25 行（两个小方法 + 五处单行加法）；文案与派生逻辑全部下沉 `prompts.py` 与前端纯库；不新增编排分支 |
| R-7 | 两轮未提交改动叠加，提交时误带他特性 hunk | §三提交范围注记：仅 `config.py` 为混合文件（剔 `ai_trade_enabled` hunk），其余本特性文件在第 1 轮实施前为干净态；沿用 position-ladder/limit-orders 已验证的行级 `git apply --cached` 暂存法 |
| R-8 | live 反思计数窗口（≤50）被误读为全程累计 | 详情注脚与 live 小节标签均标注「近 50 轮样本口径」；`maxHoldStreak`/`wakeSkips` 为会话级全程值，两种口径在注脚区分 |

---

## 八、实施过程发现的方案缺陷（预留给 Subtask #2 回填）

Subtask #2（2026-07-16）实施完毕，**未发现方案与仓库现状不一致处，零偏差落地**。备案三条：

1. **冻结门禁（§六-8 / R2-01）已按基线口径执行**：开工前对 `reflection.py`/`decider.py`/
   `wake_guard.py`/`backtest.py` 四文件记 sha256 基线，收工后 `sha256sum -c` 全部 OK——四个
   安全层文件相对第 1 轮落地态**零新增改动**；四个冻结测试文件一字未改全绿（92 passed）。
2. **R2-07（P2，可选）已顺手补齐**：`aiTradeHistoryTypes.ts` 的 `decisionsDigest` 条目补
   `reflectionVerdict?: string | null`（一行、零风险，评审已背书）。
3. **验证记录**：后端 `pytest -k "ai_trade or ai_backtest or ai_auto"` 347 passed；前端触达
   套件（`NODE_ENV=test`）21+79 passed；`tsc --noEmit` 仅剩 pre-existing 的
   `WeightBatchArena.test.tsx` 报错（他特性遗留，非本轮回归）。全量套件按评审条件 3 留待
   Subtask #3 终审执行（工作树 factor-expansion 孤儿测试红为 pre-existing，届时列明勿静默）。

---

## 评审结论（Review Verdict · Subtask #1）

**有条件通过（approved with conditions）。**

四维评估：方案**可行**（§2.0 全部代码锚点经逐一核对真实存在；加法键经 status/decision 两条已
验证的透传链免费直达前端）；**一致**（F4a「键缺失即逐字节冻结」范式、R-04 双口径纪律、零迁移 /
加法 wire 约定均沿既有惯例，与 CLAUDE.md 无冲突）；**尺度得当**（观测闭环复用既有环形与 spread
合并、不新增后端计算面；自察注记走确定性计数 + 可配置阈值 + `0` 一键关闭，未触碰任何确定性
安全层）。评审发现的 4 处 P1（R2-01 冻结门禁基线口径、R2-02 镜像分支位置双钉、R2-03
low_confidence 分支语义、R2-04 注记上下文存续澄清）已全部就地修复，正文即 v2 基准；无 P0，
无未解决 P1。

**条件（Subtask #2/#3 必须遵守）：**

1. **按 v2 落实，勿按 v1**：尤其 R2-02 的两条字面量镜像用例（§六-6）、R2-03 的
   `("hold", "low_confidence") → 不变` 表驱动分支（§六-1）、R2-04 的「`commit_round` 零改动、
   不得剥键、不得断言 _context 不含注记」约束（§2.1 N3）。
2. **冻结门禁按 R2-01 基线口径执行**（§六-8）：四个安全层文件以「第 1 轮落地后的工作树」为
   基线做零增量比对，而非断言 `git diff` 为空；四个冻结测试文件一字不改全绿。
3. **终审必跑全量**（第 1 轮 / limit-orders 已验证的教训：跨文件镜像断言只有全量才逮得住）；
   工作树 factor-expansion 孤儿测试红与 `WeightBatchArena.test` tsc 报错为 pre-existing 他特性
   遗留，报告中列明而非静默。
4. **提交范围严格执行 §三注记**（Subtask #3）：两轮 hunk 合并为一个 feat 提交；混合文件仅
   `config.py`（剔 `ai_trade_enabled` 翻转 hunk，行级 `git apply --cached`）；其余他特性脏文件
   一律不入本提交。

P2（R2-05 至 R2-08）为记录性意见：R2-07（`decisionsDigest` 补 `reflectionVerdict` 类型）可选
顺手补齐，其余不要求本轮动作。
