# AI 自动交易 · 承诺退出线棘轮（ai-trade-profit-protection-commitment）设计方案 v2

> 迭代循环 `tge-4ad08bb1`（全局目标：**让 AI 自动交易在合适的时候保住利润**）第 3/3 轮设计。
> 前两轮均已交付并合入：
> - 第 1 轮 `2da0131`（32 文件）：`ai_auto/profit_guard.py` 确定性保护层（峰值追踪 → 移动止盈 /
>   保本 / 硬止盈 → 实时价判定 → 市价平仓）+ prompt 宪章 + UI + 可观测。下称 **v1 层**。
> - 第 2 轮 `0065b93`（53 文件）：自适应（利润阶梯 + ATR 归一回吐距离 + 入场价夹紧）+ 保护性平仓
>   冷却期 + AI 回测接入（bar 内极值保守成交序）+ 利润捕获率。下称 **v2 层**。
> - 上游设计基线：`docs/plans/ai-trade-profit-protection/spec.md`、
>   `docs/plans/ai-trade-profit-protection-adaptive/spec.md`。
>
> 本轮**不推翻**任何 v1/v2 不变式，只在其上加一件事：把「模型每轮被要求给出、却从不被系统采集与执行的
> 退出线」变成**结构化、可采集、单调棘轮、由执行层确定性兑现的第三条保护线**——这是诊断建议里唯一
> 只做了「提示」半边、没做「执行」半边的一条（根因 ⑤ / 修复优先级 ③），也是全局目标的最后一块缺口。

---

## 〇、评审记录（v2，Subtask #1 完成）

> 评审方法：把方案里每一处 `file:line`、每一个新符号、每一条不变式在**真实代码**上逐条核对
> （`profit_guard.py` / `profit_guard_runtime.py` / `profit_guard_text.py` / `decider.py` / `prompts.py` /
> `session.py` / `manager.py` / `backtest.py` / `profit_guard_bar.py` / `config.py` + 前端
> `types.ts` / `aiTrade.ts` / `tradeExplain.ts` / `AiTradePanel.tsx` / `AiTradeTraceRound.tsx`）。
> 四维度：可行性 / 完整性 / 一致性 / 规模适配。全部 P0/P1 已就地修复，版本升 **v2**；P2 见文末结论条件。
>
> **核对通过、无需改的关键落点**（记录以便下游放心复用）：
> - `ARMED_STATUSES = (STATUS_ARMED, STATUS_BREAKEVEN_ARMED)` 确在 `profit_guard.py:63`，且已被
>   `profit_guard_runtime.py:189` 复用——`record_commitment` 引用它是真的。
> - `record_commitment` 要读的快照键 `livePx`/`entryPx`/`breakevenPx`/`atrPx`/`side`/`status` **全部**
>   是 `build_snapshot`（`profit_guard.py:558-586`）真实下发的键，且 `entryPx`/`livePx`/`breakevenPx`/
>   `atrPx` 均已 `_round_px`——精度口径与判定侧自洽（方案 §2.2.2 成立）。
> - 插桩点 `session.py:561-563`（`_update_profit_hold_streak` 旁、stage-1 决策处）、`payload["profitGuard"]`
>   由 `profit_guard_runtime.py:191` 写入、`_maybe_profit_exit`（`session.py:942`）调
>   `evaluate_profit_guard(snap, rule)` **无 `allow=`**（三线全放行，承诺码自动纳入判定）——方案 §2.2.4
>   「`_maybe_profit_exit` 一字不改」成立。
> - `evaluate_profit_guard` 现为「硬止盈 → trail → breakeven」定序（`profit_guard.py:638-644`），且
>   多头恒 `trail_px ≥ breakeven_px`（`_clamp_trail_to_floor` 保证），故「先 trail 后 breakeven 固定序」
>   **数学等价**「取最靠近现价者」——§2.2.3 的 G-3 逐字节等价论证**成立**（committed=None 时）。
> - 前端 `inferKindFromReason`（`tradeExplain.ts:60`）按 `reason.startsWith("profit_guard_")` 前缀归类，
>   新码 `profit_guard_committed_exit` **自动**归入 `profit_guard` kind，分类零改（方案 §2.4 成立）。
> - 恒等断言 `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA` 确在
>   `test_ai_trade_position_step.py:132`；短路条件加 `and not allow_hold_exit` 后仍成立（§2.1.2 成立）。

### P0（阻断，必须修复后方可进入开发）

- **P0-1 · 承诺线未绑定 `(side, seq)` epoch 身份，反手/重开会继承前仓的旧承诺线并当场强平新仓
  （一致性 / 完整性 / 实盘安全）。**
  证据：epoch 身份是 `(side, seq)`，`advance_state`（`profit_guard.py:366`）在 `state is None
  or state.side != facts.side or state.seq != facts.seq` 时**新建 epoch、peak 从 entry 起算**——这正是
  v1 [P0-1] 花一个 P0 才修好的东西。但本方案（§2.2.2 + §五）把 `committed_exit_px`/`committed_exit_basis`
  放在**运行时对象**上，且只靠 `clear_state()` 清空，**并未随 `advance_state` 的新 epoch 复位**。
  `clear_state()` 现实只在两处触发：`_refresh_profit_state` 见 `projected=="flat"`（`session.py:930` 附近）、
  `_report_profit_exit` 确认平仓（`session.py:1001`）。而 executor **支持原子反手**：
  `executor.py:226-229`（`reverse = projected != "flat"; if reverse: append("close",…); append("open",…)`）
  会在**一次 `apply` 内** long→short，中间**不经过 flat**。此路径下：`advance_state` 因 side 变化复位
  `peak_px`，但 `clear_state()` 未被调用 ⇒ **多头 epoch 的旧 committed 线（一个低于当时现价的价位）原样
  带进新空头 epoch**。空头判定 `_touched` 是 `px >= line`（`profit_guard.py:593`），旧低价线恒在现价下方
  ⇒ `px >= line` 恒真 ⇒ **过了 `min_hold_s` 即把刚反手的空头在一个毫无关系的价位强平**。更糟：棘轮对空头
  「更紧=更低」，模型本轮想报的正确空头线（现价上方、更高）会因「不比旧低价线更紧」而**被棘轮拒绝**——
  bug 在 epoch 复位前**不可自愈**。这与 v1 [P0-1] 是**同一类**实盘缺陷，§五「`ProfitGuardState` 不动，
  承诺线与 entry/peak 无关，无需进 epoch」的判断**是错的**：承诺线**是** epoch 作用域状态，必须与
  `peak_px` 共用 `(side, seq)` 身份，随 `advance_state` 的新 epoch 一并复位。
  修复（已落 §2.2.2 / §2.2.3 / §五 / §3.1 / §6.1 T-6）：把承诺线的生命周期**绑定到 epoch 身份**——
  推荐将 `committed_exit_px`/`committed_exit_basis` 随「当前 state 的 `(side, seq)`」标记；`refresh` 在
  `advance_state` 之后若发现 `state is None` 或 `(side, seq)` 变化，**先把承诺线清零再注入**（等价于让
  承诺线随 `peak_px` 同生死）。新增 **T-6** 钉死：反手（side 变）与同向重开（seq 变）后承诺线必为 `None`。

### P1（必须修复后方可进入开发）

- **P1-1 · 实盘与回测的「承诺可采纳」武装阈值不一致，破坏 C5「同一纯核心」与 G-4 平价（一致性 / 完整性）。**
  证据：`record_commitment`（§2.2.2）先按 `snapshot.get("status") not in ARMED_STATUSES` 门控——而
  `ARMED_STATUSES=(armed, breakeven_armed)`（`profit_guard.py:63`）对应 `peak_return ≥
  breakeven_arm_return_pct`（默认 **3%**）——随后又给 `clamp_commitment(..., armed=True, ...)` **硬传 True**，
  绕过 clamp 的第 0 步。而回测（§2.5）直接 `clamp_commitment(..., armed=(peak_return ≥ arm_return_pct), …)`
  ——用 **5%** 阈值。于是 `peak ∈ [3%,5%)` 时**实盘会记承诺、回测不会**，两条路径对同一断面给出不同行为，
  C5「复用同一纯核心」名不副实，G-4 平价被破。修复（已落 §2.2.1 / §2.2.2 / §2.5）：把「是否武装到可采纳
  承诺」定为**唯一谓词** `is_commit_armed(status) := status in ARMED_STATUSES`，实盘与回测**都**据它先门控
  再调 clamp；`clamp_commitment` 退化为**纯几何**（方向 + 抗噪 + 保本底线），**不再自带武装判定**（去掉第 0
  步的 arm 阈值分支），武装与否是调用方的单一职责。

- **P1-2 · 单调棘轮判据在实盘 `record_commitment` 与回测 `_run_decision` 各写一份，安全关键规则重复实现有
  漂移风险（一致性 / 规模适配）。**
  证据：`backtest.py` 无 `committed_exit_px` 字段（grep 零命中），§2.5 要在 `_run_decision` 内**内联**
  `self._committed_exit_px` 的「只收紧不放松」比较，与 §2.2.2 `record_commitment` 里的同一比较**两处副本**。
  本仓库贯穿「单一真源」纪律（限价意图、归一契约、仓位档位……皆抽纯函数单点）。修复（已落 §2.2.2 / §2.5 /
  §3.1）：抽纯函数 `ratchet_commitment(prev_px, prev_basis, cand_px, cand_basis, *, side) ->
  tuple[float|None, str|None]`（`None` 前值即直接采纳候选；否则 long 取更高 / short 取更低），实盘与回测
  **同调此函数**，棘轮语义只此一处。

- **P1-3 · 有效退出线的「当前由哪条线定住」归因来源二义（后端键 vs 前端就近推断），前端按三个已 round 的
  价位重推可能与后端 `_effective_exit` 的并列优先级不一致（一致性 / 完整性）。**
  证据：§2.3.2 与 §4.3 都写「`effectiveExitSource` 快照可选加此键，或前端据三线值就近推断」。而 `_effective_exit`
  在后端已经算出归因码（§2.2.3），前端拿三个 `_round_px` 后的价位重比，遇「承诺线==移动止盈线」这类并列时
  的 tie-break 可能与后端**不一致**，导致面板/轨迹显示的「由 XX 线定住」与实际平仓归因对不上（对模型是轻度
  误导，[R-3] 教训同源）。修复（已落 §2.2.3 / §2.3.2 / §4.3）：`effectiveExitSource` **强制后端单点真源**——
  `build_snapshot` 直接取 `_effective_exit` 返回的 `reason_code` 下发（`unavailable`/`flat`/无线 ⇒ `None`，
  并进 unavailable-None 块），**删除「前端就近推断」这条备选**，前端只读不推。

### P2（建议修复；未阻断，列为放行条件）

- **P2-1 · `clamp_commitment` 安全底线步在 `breakeven_px` 与 `entry_px` 皆 `None` 时 `line < floor_line`
  比较会 `TypeError`（fail-closed，违 fail-open）。** 实盘 `record_commitment` 路径因已 `status ∈
  ARMED_STATUSES` 门控、`entryPx` 必非 None 而不可达；但回测直调 clamp，且 fail-open 是硬纪律。已在 §2.2.1
  末补一句：`floor_line is None ⇒ 返回 (None, "no_floor")`（绝不抛）。
- **P2-2 · `_effective_exit` 伪代码注释「元组比较：价相等时按列表先后」措辞会误导实现者。** 真实语义是
  `max/min(cands, key=lambda c: c[0])`——**按价取极值、并列时取列表首项**（Python `max` 返回首个达标元素），
  **不是**元组比较。若实现者据注释「简化」成 `max(cands)`（真元组比较），并列时会按 reason 字符串字典序
  tie-break（`breakeven < committed < trailing`），空头 `min` 会错选 breakeven——归因错乱。已在 §2.2.3
  改正注释并显式警告禁用 `max(cands)`。另：当前「只查最靠近线一次」依赖三条下方线的 `allow` **一致放行**
  （现有调用方 live=None / 回测回撤三线全放 / 回测目标只放硬止盈，均满足）；已注明该不变式，若未来出现
  非一致 `allow` 需改为「按靠近程度遍历，返回首个 allowed 且 touched 者」。
- **P2-3 · `_map_decision` 无字面 `action == "hold"` 分支。** hold/close/cancel_order 同走
  `decider.py:371` 的 fall-through 返回；解析 `holdExitPx` 必须**显式以 `action == "hold"` 为条件**
  （close/cancel_order ⇒ `hold_exit_px=None`），否则残留 `holdExitPx` 会泄漏到非持有决策。已在 §2.1.1
  把「hold 分支」措辞改为「line 371 fall-through 处按 `action=="hold"` 守卫解析」。
- **P2-4 · 宪章承诺段应显式让位于既有 fail-open 条款**，避免「会被兑现」被读成无条件保证（承诺线是**进程内
  内存态**，进程崩溃即失，只剩开仓随单 OCO——与 v1 [P1-5] / v2 [RV-1] 道德风险先例同源）。既有宪章
  （`profit_guard_text.py:45-55`）已两层披露 fail-open + 道德风险；已在 §2.3.1 给承诺段加「在保护层可用的
  前提下」限定，不与其冲突。
- **P2-5 · basis 字面命名与既有不一致。** §4.3 列 `adopted`，但仓库既有基线 basis 是 `giveback`（无
  `"adopted"` 字面）；且承诺抑制码 §写 `suppressed_at_floor`，v2 trail 侧是 `suppressed_at_entry`。已在
  §2.2.1 / §4.3 明确：承诺线用**独立命名空间** `COMMIT_BASIS_*`（`adopted`/`atr_floor`/`clamped_breakeven`），
  前端 `describeCommittedExit` 须为这些字面各配后缀（否则 `TRAIL_BASIS_SUFFIX` 无对应键会静默渲染空串）。
- **P2-6 · `session.py` 现 1393 行、已越 CLAUDE.md 800 行红线（既有欠账，非本轮引入）。** 本轮对它只加
  一个 5 行方法 + 一行调用，delta 最小、可接受；但**不得**再在其上堆更多。`profit_guard.py` 现 646 行，
  加本轮增量（clamp≈25 + `_effective_exit`≈+2 + 键/字段≈+15）后约 690<800，**故 R-8 的拆分本轮判为不需要**，
  `clamp_commitment` 留在 `profit_guard.py` 即可（去掉 R-8 的「留评审判」二义）。
- **P2-7 · `describe_exit_outcome` 对承诺线平出须沿用 pnl 符号派生文案、不得硬编「锁定利润」。** 承诺线夹到
  ≥ 保本线，成交在保本线附近扣费/滑点后可能微负；既有 `describe_exit_outcome`（`profit_guard_text.py:130-139`）
  的 `>0 → 锁定利润 / else → 按保护线离场` 已诚实覆盖。已在 §2.3.3 明确「复用不新增分支」。

---

## 一、概述

### 1.1 为什么第 1、2 轮之后还需要这一轮

诊断报告（`.bug-diagnosis/27942cda…/report.md`）列了 5 条根因与 5 条修复优先级。前两轮把其中 4 条落到了
**确定性代码**里：执行层的峰值 / 保本 / 移动止盈（优先级 ①，v1）、把 peakPnl / 回撤 / 持仓时长 / 有效
退出线喂给 AI（优先级 ②，v1）、按实时价判定不等收线（优先级 ④，v1）、UI 显示并记住实际生效阈值
（优先级 ⑤，v1）。唯独 **优先级 ③——「持仓盈利后，每轮必须重新论证继续持有，并给出明确退出线」**
（对应根因 ⑤：现有 prompt 只让 AI 解释「反对开仓的证据」，从不要求它解释「为什么允许利润继续回吐」）
——只做了**一半**：

- **已做的一半（prompt 侧）**：`ai_auto/profit_guard_text.py:233` 的 `format_profit_hold_note` 已经在
  每轮盈利持仓时向模型索要「① **你的退出线**（具体价位或条件），② 为什么此刻继续承担利润回吐风险优于
  兑现」。也就是说，系统**已经在问**这条退出线了。
- **缺失的一半（执行侧）**：模型给出的那条退出线只是 `reason` 里的一句**自然语言散文**——系统从不
  采集它、从不校验它、更从不据它平仓。模型完全可以在第 R 轮写「跌破 0.0743 我就走」，到第 R+3 轮价格
  真的跌破 0.0743 时，又用一句新说辞把它推翻、继续持有。这正是整条 loop 要修的「盈利了不平仓、偏要等
  跌下去了才平」的惯性本身，只是这一次它藏在**模型对自己承诺的背弃**里，而不是藏在「没有任何机制」里。

换句话说：v1/v2 建成了唯一真正有牙齿的防线——那条**由系统按峰值回吐算出来的移动止盈线**；而模型
**自己许下的**退出承诺始终是一句没有执行力的话。本轮补上这颗牙：**把模型每轮给出的退出线结构化采集
下来，做成一条单调只收紧（永不放松）的保护线，交给已有的执行层去兑现。** 这是「不能完全依赖 LLM」
（v1 §1.2 的立层理由、【基础策略】铁律层的范式）在优先级 ③ 上的贯彻：模型负责**说出**退出线，
系统负责**确定性执行**它——两者都不可或缺，但把话说了不算数，等于没修。

### 1.2 方案要旨

本轮交付一个特性、四个部件，全部落在 `ai_auto/` 与前端，**不新增对交易所的任何写路径、不改
`live_auto/executor.py` 的下单逻辑**（沿用 v1 的隔离结论）：

- **C1 结构化承诺退出线 `holdExitPx`**：把 `submit_trade_decision` 工具里那条本已用散文索要的退出线，
  升级为一个**可选数值字段**，只在利润保护层装配且承诺功能开启时露出（镜像 `allow_limit` 的门控范式，
  保证零回归）。`action=hold` 时解析，非法值一律忽略（绝不把 hold 降级）。
- **C2 承诺棘轮 + 确定性兑现**：新纯函数 `clamp_commitment` 把模型的原始报价夹成一条**安全、抗噪、
  合乎方向**的保护线；`ProfitGuardRuntime` 持有它并**单调棘轮**（同一 epoch 内只能向现价收紧、绝不放松）；
  它作为**第三条下方保护线**进入 `_effective_exit`（有效退出线取三线中最靠近现价者）与
  `evaluate_profit_guard`（新触发码 `profit_guard_committed_exit`）。因它只会让有效退出线**更紧**、
  且被夹到**不劣于保本线**，故：① 相对 v2 **永不更松**（never-worse），② **永不做净亏强平**
  （复用 v2 P0-1 的入场价夹紧 / 抑制范式），③ 未给承诺时**逐字节等价 v2**（G-1）。
- **C3 每轮问责闭环**：宪章披露「你给的退出线会被系统采集并强制兑现、且只能收紧」；每轮的
  「利润保护」段如实显示当前承诺线与它被哪条约束定住；模型自己许下的线一旦触发平仓，`lastExit` 回执
  会明确写「由你的承诺退出线平出」——形成「说 → 兑现 → 复盘」的问责回路。
- **C4 wire / UI**：快照新增 `committedExitPx` / `committedExitBasis`（纯加法）；面板、轨迹、K 线标记
  的解释映射同步显示这条线。
- **C5 回测接入**：v2 已把利润保护层与 LLM 决策一并接进 AI 回测；本轮令回测决策器同样露出 `holdExitPx`、
  回测 runner 复用**同一** `clamp_commitment` 棘轮，承诺线经**同一** `evaluate_profit_guard` 的
  `allow=` 白名单进入 bar 内极值保守成交序。未给承诺时回测逐字节等价 v2。

### 1.3 目标 / 非目标

**目标（本轮交付）**

- C1：决策工具新增可选 `holdExitPx`（仅装配 + 开关开启时露出）；`TradeDecision.hold_exit_px` 承载。
- C2：`clamp_commitment` 纯函数 + `ProfitGuardRuntime` 棘轮状态 + `_effective_exit` 三线合一 +
  `evaluate_profit_guard` 新触发码；实盘经 `_maybe_profit_exit` 既有路径兑现（零新增平仓路径）。
- C3：宪章承诺披露段 + 每轮承诺线渲染 + `lastExit` 归因；`ProfitGuardRule.commit_enabled` 门控。
- C4：快照两键 + 前端类型 / 面板 / 轨迹 / explain 映射。
- C5：回测决策器露出 `holdExitPx` + runner 棘轮字段 + bar 保守成交序纳入承诺线。

**非目标（本轮明确不做，理由随附）**

- **不把承诺线同步到交易所侧。** 与 v2 §1.3 同——`okx/trade_client.py` 仍只有只读
  `get_algo_pending_orders`，无 `order-algo` / `amend-algos` / `cancel-algos` 任何写端点；承诺线在桌面端
  进程关闭后不生效，仅剩开仓随单 OCO 兜底，宪章 fail-open 披露继续成立。交易所侧算法单生命周期是独立
  一轮的工作量，且孤儿算法单的失败模式比本轮任何缺陷都严重，仍留作后续候选。
- **不让承诺线放松已有的确定性保护。** 棘轮**只收紧不放松**是硬约束；模型不能用一条更松的承诺去覆盖
  v2 算出的移动止盈线（那恰恰是本轮要修的「为回吐找借口」）。模型任何时候都能直接 `close`（永不被拦），
  但**不能松动一条已许下的保护承诺**。
- **不做承诺线的强制填写 / 不因缺失而降级 hold。** 缺 `holdExitPx` 的盈利持仓仍由 v2 移动止盈线兜底；
  prompt 每轮索要，但**给不给是模型的判断**，绝不因为没给就把 hold 变成别的动作（hold 已是最安全动作，
  凭「没许退出线」去动它违背 fail-open）。
- **不引入承诺线的会话级 / 交易所级止盈止损联动**，不改
  `ai_trade_default_sl_pct=0.05 / tp_pct=0.10` 数值（理由同 v1/v2：它们是进程崩溃时唯一还生效的兜底）。
- **不新增 WS 事件类型**（承诺线搭既有 `profitGuard` 快照下发，触发搭既有 `status` / `lastExit`）。
- **不做承诺线的独立可观测指标**（捕获率 F4 已能反映锁定效果；承诺线是否比纯移动止盈锁得更多，靠回测
  A/B 与捕获率间接观察，不再单列一个 KPI）。

---

## 二、技术设计

### 2.1 C1：结构化承诺退出线 `holdExitPx`

#### 2.1.1 决策模型与解析（`ai_auto/decider.py`）

- `TradeDecision`（`decider.py:106`）新增**末位带默认值**字段 `hold_exit_px: float | None = None`
  ——全部既有 `TradeDecision(action, size, conf, reason)` 位置构造与 `(action, None, conf, reason,
  ord_type, limit_px)` 六元构造**零改动**（与 `ord_type`/`limit_px` 当初的加法范式一致，§2.2）。
- `AiTradeDecider.__init__`（`decider.py:155`）新增 keyword-only 形参 `allow_hold_exit: bool = False`
  （镜像 `allow_limit`），透传给 `build_decision_tool_schema(..., allow_hold_exit=self.allow_hold_exit)`
  与 `build_decision_tool_description(..., allow_hold_exit=...)`。默认 `False` 是**硬约束**：既有恒等
  断言 `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA`（`test_ai_trade_position_step.py:132`）
  据此原样通过。
- `_map_decision`（`decider.py:334`）**[P2-3] 注意：并无字面 `action == "hold"` 分支**——hold /
  close / cancel_order 同走 `decider.py:371` 的 fall-through 返回。故解析 `holdExitPx` 必须**显式以
  `action == "hold"` 为条件**：`hold_exit_px = _finite_px(raw.get("holdExitPx")) if action == "hold"
  else None`，在构造 line 371 的 `TradeDecision` 前算好。`_finite_px`（新私有：`float()` 成功且
  `math.isfinite` 且 `>0` → 值，否则 `None`）。**非法值一律 `None`，绝不返回 degrade**——hold 已是安全
  动作，一条读不懂的退出线不该把它降级（与 `orderType/limitPrice` 对 hold 的忽略同理，`decider.py:351`
  的 F-2 注释先例）。`open_*`（line 370 分支）/ `close` / `cancel_order` 恒 `hold_exit_px=None`
  （承诺退出线只对「继续持有」有语义）。
- `commit_round`（`decider.py:471`）的 `decision_input` 落痕加 `"holdExitPx": decision.hold_exit_px`
  ——否则滚动摘要会丢掉「模型许过什么退出线」，问责回路在摘要重置后断裂（与 `orderType`/`limitPrice`
  当初落 journal 同理，`decider.py:488` 的 M-9 先例）。

#### 2.1.2 工具 schema 与描述（`ai_auto/prompts.py`）

- 模块常量 `DECISION_TOOL_SCHEMA`（`prompts.py:17`）**一字不改**（冻结形状，供既有断言）。
- `build_decision_tool_schema`（`prompts.py:155`）签名加 `allow_hold_exit: bool = False`；短路条件
  由 `step <= 0 and not allow_limit` 改为 `step <= 0 and not allow_limit and not allow_hold_exit`
  ——三者皆假时仍返回模块常量（恒等断言零改）。`allow_hold_exit` 为真时，在**调用方自己的 props
  浅拷贝**上补：

  ```python
  props["holdExitPx"] = {
      "type": "number", "exclusiveMinimum": 0,
      "description": "（可选）继续持有时你承诺的退出价位：价格向此价靠拢并触及即由系统市价平仓。"
                     "多头填低于现价的止盈保护价、空头填高于现价的价；系统只会据它把保护线收得更紧、"
                     "绝不放松，且不会劣于保本线。给了它等于让系统替你守住这条线，你无需每轮盯盘。"}
  ```

- `build_decision_tool_description`（`prompts.py:144`）加 `allow_hold_exit`，为真时在描述尾部追加一句
  `holdExitPx` 的散文说明（与 `_limit_tool_description_suffix` 同范式，基础文案一字不改 ⇒ 关闭时
  逐字节回退）。
- **门控真源**：`AiTradeDecider` 的 `allow_hold_exit` 由装配点从
  `profit_rule is not None and profit_rule.commit_enabled` 派生（见 §2.4 `ProfitGuardRule.commit_enabled`
  与 §2.5 回测）。承诺总闸关或未装配保护层 ⇒ `allow_hold_exit=False` ⇒ schema / 描述逐字节等价 v2。

### 2.2 C2：承诺棘轮与确定性兑现

#### 2.2.1 三步夹紧（新纯函数 `clamp_commitment`，`ai_auto/profit_guard.py`）

`clamp_commitment` 是**纯几何**函数：把模型的**一次原始报价**夹成一条可采纳的保护线，或判定「本次不采纳」
（返回 `None`）。**它不做武装判定、也不做棘轮**——武装门控是调用方（`record_commitment` / 回测）的单一
职责（[P1-1] 修订：见 §2.2.2 的 `is_commit_armed`），棘轮在 `ratchet_commitment`（[P1-2]，§2.2.2）。
签名与语义（keyword-only 聚合，满足「形参 ≤ 5」用聚合入参不堆位置参）：

```
clamp_commitment(proposed_px, *, side, mark_px, entry_px, breakeven_px,
                 atr_px, atr_floor_mult) -> tuple[float | None, str]
```

判定顺序（**顺序不可换**，每一步都对应一条 v1/v2 已确立的安全不变式）：

0. **输入合法性**：`proposed_px` 非有限 / ≤0 ⇒ 返回 `(None, "not_finite")`。（武装与否由调用方在此之前
   门控，clamp 不再自带 arm 阈值——[P1-1]。）
1. **方向必须是保护性的**：多头须 `proposed_px < mark_px`（现价下方的止损保护），空头须
   `proposed_px > mark_px`。否则模型给的是**目标位**而非**保护位**，`(None, "not_protective")` 不采纳。
2. **抗噪下界（复用 v2 ATR 尺度）**：承诺线离现价太近会被分钟级噪音扫掉（v2 §2.1.1 第 2 条同一失败
   模式）。`atr_px` 可用且 `atr_floor_mult>0` 时，要求 `|mark_px − line| ≥ atr_floor_mult × atr_px`；
   过近则**把线推离现价至恰好该下界**（多头下移、空头上移），basis=`COMMIT_BASIS_ATR_FLOOR`（`atr_floor`）。
3. **安全底线（复用 v2 入场价夹紧，v1 P0-1 的止血范式）**：`floor_line = breakeven_px if breakeven_px
   is not None else entry_px`。**[P2-1] fail-open 守卫**：若 `floor_line is None`（保本线关且 `entry_px`
   也缺）⇒ 直接返回 `(None, "no_floor")`，**绝不抛异常**（回测直调 clamp，None 比较会 `TypeError`）。
   否则多头若 `line < floor_line`：保本线在 ⇒ 夹到保本线（basis=`COMMIT_BASIS_CLAMPED_BREAKEVEN`，
   `clamped_breakeven`）；保本线关（`breakeven_arm=0`）⇒ **抑制**，返回 `(None, "suppressed_at_floor")`
   ——绝不采纳一条会把持仓在入场价之下强平的「保护」线（与 v2 `_clamp_trail_to_floor` 逐条同构）。
   空头方向取反。

未被任何一步修改的报价直接采纳，basis=`COMMIT_BASIS_ADOPTED`（`adopted`）。返回 `(line, basis)`；未采纳
返回 `(None, reason)`。**basis 用独立命名空间 `COMMIT_BASIS_*`（[P2-5]，勿复用 trail 侧字面），前端
`describeCommittedExit` 须为 `adopted`/`atr_floor`/`clamped_breakeven` 各配后缀**（`adopted` ⇒ 空后缀）。

#### 2.2.2 单调棘轮（`ProfitGuardRuntime`，`ai_auto/profit_guard_runtime.py`）

运行时新增**三个** epoch 级状态字段。**[P0-1] 关键：承诺线必须与 `peak_px` 共用 `(side, seq)` epoch
身份**，第三个字段 `committed_exit_epoch` 就是这个身份戳；仅靠 `clear_state()` 清空**不够**（原子反手
`long→short` 不经过 flat、`clear_state()` 不触发，见 [P0-1]），必须在 `refresh` 里随 `advance_state`
产出的新 epoch 主动作废：

```
self.committed_exit_px: float | None = None       # 已棘轮的承诺退出线（None=本 epoch 尚无承诺）
self.committed_exit_basis: str | None = None       # 当前承诺线被哪条约束定住（展示用）
self.committed_exit_epoch: tuple | None = None     # 承诺线所属 epoch 的 (side, seq)——[P0-1] 身份戳
```

`clear_state()`（`profit_guard_runtime.py:116`）除既有 `self.state = None` 外，**同步三清**：
`self.committed_exit_px = self.committed_exit_basis = self.committed_exit_epoch = None`。

**单一武装谓词（[P1-1]，模块级）**——实盘与回测**同一份**：

```python
def is_commit_armed(status: str | None) -> bool:
    return status in ARMED_STATUSES            # peak_return ≥ breakeven_arm；实盘/回测同阈值
```

**纯棘轮判据（[P1-2]，模块级，实盘与回测同调）**——只收紧不放松，安全关键规则只此一处：

```python
def ratchet_commitment(prev_px, prev_basis, cand_px, cand_basis, *, side):
    if cand_px is None:
        return prev_px, prev_basis                      # 候选不可采纳：前值原样保留
    if prev_px is None:
        return cand_px, cand_basis                      # 本 epoch 首次承诺：直接采纳
    tighter = cand_px > prev_px if side == "long" else cand_px < prev_px
    return (cand_px, cand_basis) if tighter else (prev_px, prev_basis)   # 更松/相等 ⇒ 拒绝
```

`record_commitment(self, proposed_px, snapshot)`（会话侧 / 回测侧在拿到本轮决策后调用）：

```python
def record_commitment(self, proposed_px, snapshot):
    if self.rule is None or not self.rule.commit_enabled or not snapshot:
        return
    if not is_commit_armed(snapshot.get("status")):       # [P1-1] 单一武装门控（clamp 不再自带 arm 判定）
        return
    line, basis = clamp_commitment(                        # [P1-1] clamp 纯几何：不传 armed
        proposed_px, side=snapshot.get("side"),
        mark_px=snapshot.get("livePx"), entry_px=snapshot.get("entryPx"),
        breakeven_px=snapshot.get("breakevenPx"),
        atr_px=snapshot.get("atrPx"), atr_floor_mult=self.rule.atr_floor_mult)
    self.committed_exit_px, self.committed_exit_basis = ratchet_commitment(   # [P1-2] 唯一棘轮真源
        self.committed_exit_px, self.committed_exit_basis, line, basis, side=snapshot.get("side"))
```

- **读的是本轮快照**（模型据以决策的那一份），故承诺线与模型看到的现价 / 保本线 / ATR 同源、可解释。
- 快照字段均为**已 round** 的展示值，承诺线也据此 round；兑现时 `_touched(livePx, committedExitPx)`
  两侧同精度，判定自洽（与 v2 三线判定同精度口径一致）。
- `proposed_px is None`（模型本轮没给退出线）⇒ 会话侧直接不调 `record_commitment`（见 §2.2.4 的
  `_record_profit_commitment` 守卫）；**前值原样保留**——承诺一旦许下，除非被更紧的覆盖或 epoch 结束，
  否则持续有效（棘轮的执行力正来自此）。

`refresh()`（`profit_guard_runtime.py:120`）**先按新 epoch 身份作废跨 epoch 的旧承诺（[P0-1]）**，
再用 `dataclasses.replace` 注入当前棘轮值（`replace` 需在本文件新增 `from dataclasses import replace`）：

```python
self.state = advance_state(self.state, facts)             # 既有：身份变化 ⇒ 新 epoch（peak 从 entry 起算）
epoch = (self.state.side, self.state.seq)
if self.committed_exit_epoch != epoch:                    # [P0-1] 反手/重开：承诺线随 peak 一并复位
    self.committed_exit_px = self.committed_exit_basis = None
    self.committed_exit_epoch = epoch
facts = replace(facts, committed_exit_px=self.committed_exit_px,
                committed_exit_basis=self.committed_exit_basis)
snapshot = build_snapshot(self.rule, self.state, facts, unavailable_reason=reason)
```

**`committed_exit_epoch` 的唯一维护者是 `refresh`**（`record_commitment` 不碰它）：位置一开仓，`refresh`
就先于任何承诺被记录而运行（`_post_round` / 5s `_monitor_tick`），把 `committed_exit_epoch` 置为当前
`(side, seq)`；而承诺只在 `hold` 轮记录、必晚于开仓。故首次 `record_commitment` 时 `committed_exit_epoch`
已是本 epoch 身份、与承诺同 epoch；此后任何 epoch 变化都由 `refresh` 的比对捕获并作废旧承诺——**单点维护、
无双写**，「谁的承诺归谁的 epoch」自洽。

#### 2.2.3 第三条保护线进入纯核心（`ai_auto/profit_guard.py`）

- `PositionFacts`（`:126`）新增两字段：`committed_exit_px: float | None = None`、
  `committed_exit_basis: str | None = None`（默认 None ⇒ 既有构造零改、判定零变化）。
- 新触发码与标签：`REASON_COMMITTED_EXIT = "profit_guard_committed_exit"`；
  `_REASON_LABEL[REASON_COMMITTED_EXIT] = "承诺退出线"`。
- **有效退出线三线合一**（把 `_effective_exit_px` 升级为 `_effective_exit`，返回 `(px, reason_code)`，
  成为「哪条线在兜底」的**唯一归因真源**）：

  ```python
  def _effective_exit(side, trail_px, breakeven_px, committed_px):
      # 候选顺序即同价并列时的归因优先级：移动止盈 > 承诺线 > 保本线（与既有固定序兼容，见零回归证明）
      cands = [(trail_px, REASON_TRAILING), (committed_px, REASON_COMMITTED_EXIT),
               (breakeven_px, REASON_BREAKEVEN)]
      cands = [(px, code) for px, code in cands if px is not None]
      if not cands:
          return None, None
      pick = max if side == "long" else min                 # 最靠近现价的下方线
      # [P2-2] **必须**用 key=按价取极值：Python max/min 在 key 并列时返回**列表首个**达标元素，
      # 故 tie-break 恰按 cands 的先后=上面的优先级。**严禁**写成 `pick(cands)`（真元组比较）——
      # 那会在价相等时按 reason 字符串字典序 tie-break（breakeven<committed<trailing），空头 min 会错选。
      return pick(cands, key=lambda c: c[0])
  ```

  - **[P2-2] `allow` 一致性不变式**：`evaluate_profit_guard` 改用「取最靠近线一次判定」的前提是三条
    下方线码在 `allow` 中**一致放行**。现有调用方均满足（实盘 `allow=None`；回测回撤
    `allow=(TRAILING,BREAKEVEN,COMMITTED_EXIT)` 三线全放；回测目标 `allow=(HARD_TAKE_PROFIT,)` 仅硬止盈，
    下方块两侧都返回 None）。因多头恒 `最靠近线=最高线`、`_touched` 是 `px≤line`，「最高线未触及 ⇒
    更低线必未触及」，故单次判定与逐线判定**等价**。**若未来出现非一致 `allow`**（如只放 BREAKEVEN 不放
    TRAILING），须改为「按靠近程度遍历候选、返回首个 `allowed(code) and _touched` 者」以保逐线语义。

  - `build_snapshot`（`:536`）：`exit_px, exit_src = _effective_exit(side, d["trailStopPx"],
    d["breakevenPx"], facts.committed_exit_px)`——`exit_px` 供 `effectiveExitPx`（键与值语义不变，只是多
    考虑一条线）；**[P1-3] `exit_src`（归因码）直接下发为 `effectiveExitSource`，是「当前由哪条线定住」
    的唯一真源**，前端只读不推（删除「前端就近推断」备选）。`unavailable`/`flat` ⇒ `effectiveExitSource=None`。
  - `evaluate_profit_guard`（`:610`）：硬止盈分支不变（上方目标位，不参与 max/min）；把原先分别对
    `trailStopPx` / `breakevenPx` 的两次 `_touched` 检查，替换为对 `_effective_exit(...)` 结果的**单次**
    检查，命中即以其 `reason_code` 出裁决。`allow` 白名单同步支持 `REASON_COMMITTED_EXIT`（供 F3 保守
    成交序按 `allow=(TRAILING, BREAKEVEN, COMMITTED_EXIT)` 只判下方三线）。
  - **零回归证明（G-3，逐字节等价 v2 的 evaluate）**：`committed_px=None` 时候选退化为
    `[(trail,TRAILING),(breakeven,BREAKEVEN)]`；多头下 `trail_px ≥ breakeven_px` 恒成立（trail near peak、
    breakeven near entry），故 `max` 取 `trail`⇒`TRAILING`（与旧「先查 trail、命中即 TRAILING」等价），
    `trail_px is None` 时取 `breakeven`⇒`BREAKEVEN`（与旧「trail 缺才 BREAKEVEN」等价）。空头对称。
    因旧代码「先 trail 后 breakeven」的固定序恰好等于「取最靠近现价者」，重构前后判定与归因逐字节一致。
- **快照新键（纯加法，`build_snapshot` 与 unavailable-None 块各补三键——[P1-3]）**：
  `committedExitPx`（`_round_px(facts.committed_exit_px)`）、`committedExitBasis`
  （`facts.committed_exit_basis`）、`effectiveExitSource`（`_effective_exit` 的归因码）。
  `unavailable` / `flat` 时与三条线同待遇——**一律 None**（绝不显示一条算得出却不作数的价位，
  §v2 2.1.3 的诚实约束）。注意 `build_snapshot` 的 unavailable-None 块（`profit_guard.py:546-550`）
  是**显式键列表**，三个新键须**手动**加进该块（否则 unavailable 时会漏键 / 带出脏值）。

#### 2.2.4 实盘兑现路径（`ai_auto/session.py`，零新增平仓路径）

承诺线经**已有**快照通路进入判定，故 `_maybe_profit_exit`（`:942`）**一字不改**——它调
`evaluate_profit_guard(snap, rule)`，`snap` 现已带 `committedExitPx`，命中即出 `REASON_COMMITTED_EXIT`
裁决，走**同一** `_apply_profit_exit` → `executor.apply([close])`。唯一新增是「在决策后把承诺记进运行时」：

`_run_decision_round`（`:562-563`，`_update_profit_hold_streak` 旁）新增一行：

```python
self._record_profit_commitment(decision.action, decision.hold_exit_px,
                               payload.get("profitGuard"))
```

新方法：

```python
def _record_profit_commitment(self, stage1_action, hold_exit_px, snapshot):
    """把模型本轮承诺的退出线记进运行时棘轮（仅 hold + 有值时）；未装配/未开启 ⇒ 运行时内部短路。"""
    if stage1_action != "hold" or hold_exit_px is None:
        return
    self._guard.record_commitment(hold_exit_px, snapshot)
```

- 以**第一阶段** `decision.action`/`hold_exit_px` 为准（门禁 / 反思**之前**），与 `_update_hold_streak` /
  `_update_profit_hold_streak` 同落点：门禁只会把 `open_*` 降级成 hold，绝不会凭空造出 `hold_exit_px`，
  故用第一阶段真实意愿采集承诺，语义最纯。
- 承诺记进运行时后，**下一次** `_refresh_profit_state`（`_post_round` 与 5s `_monitor_tick` 都会调）
  即把它带进快照并纳入判定；同轮 `_post_round` 的 refresh 也会带上——但承诺线是现价**下方**的止损，
  记录时 `line < mark`，同轮不可能已触及（除非记录后价格立刻穿越，那正是我们希望它兑现的场景）。
- 承诺线触发平仓后，`_report_profit_exit`（`:989`）的 `closed` 分支已 `clear_state()`，承诺随
  `committed_exit_px` 一并清空（epoch 结束）；`pending_protect_note` 带 `verdict.label="承诺退出线"`
  下发，形成问责回执（§2.3.3）。

### 2.3 C3：每轮问责闭环（`ai_auto/profit_guard_text.py`）

#### 2.3.1 宪章披露（`format_profit_guard_charter` + 新 `_charter_commitment`）

`ProfitGuardRule.commit_enabled` 为真时，宪章追加一段（关闭 ⇒ 空串 ⇒ 逐字节回退，G-2）：

> **你许下的退出线会被兑现（在保护层可用的前提下）**：当你选择继续持有并给出 `holdExitPx`（退出价位）
> 时，系统会把它采集为一条保护线，价格触及即**由系统直接市价平仓**。这条线**只能随你收紧、不能放松**
> （同一持仓周期内你可以报一条更靠近现价的新线覆盖旧线，但报更松的线不会生效），且**绝不会劣于保本线**。
> 它不是对你的惩罚，而是替你守住你自己的判断——给了它，你就不必每轮盯盘等回吐；不给它，则仅由系统的
> 移动止盈线兜底。被你自己的承诺线平出，说明价格确实走到了你说要离场的位置。
> **[P2-4] 但它和移动止盈线一样只是进程内的兜底**：与上文 fail-open 条款一致，行情/读数异常时保护层会
> 主动不动作，进程若关闭则这条承诺线随之失效、只剩开仓随单 OCO——所以它**不是**一条你可以无条件依赖的
> 保证，你的退出计划必须在没有它时也成立。

两层诚实语义**不可删**（同 v1 P1-5 的道德风险约束）：① 明说「只收紧不放松」，否则模型会以为可以随时
放宽而据一条它以为可变的线延长持有；② 明说「仅由移动止盈线兜底」而非暗示还有别的防线。

#### 2.3.2 每轮承诺线渲染（`_format_lines` / 新 `_format_committed_line`）

`format_profit_guard_for_prompt`（`:187`）的保护线段与有效退出线段，在 `committedExitPx` 非空时插一行：

```
你的承诺退出线 {committedExitPx}{basis 说明}——价格触及即由系统平仓（这是你上一轮许下的）
```

`committedExitBasis` 的中文后缀用**承诺线自己的映射** `_COMMIT_BASIS_SUFFIX`（[P2-5]，勿直接复用
trail 侧变量以免语义漂移）：`atr_floor`→「（已按波动率放宽至下界）」、`clamped_breakeven`→「（已夹至
保本线）」、`adopted`→**无后缀**（须显式含此键，否则 adopted 会走 `.get(default)` 兜底——只要兜底也是空串
即可，但须在测试里钉死三种 basis 各自的渲染）。`_format_effective_exit` 无需改（它读 `effectiveExitPx`，
已含承诺线的贡献）；当**有效退出线正是承诺线**时，据后端 `effectiveExitSource`（**[P1-3] 唯一真源，
不前端推断**）在有效退出线一行注明「（当前由你的承诺线定住）」。

#### 2.3.3 承诺触发的复盘回执（`describe_exit_outcome` + `_format_last_exit`）

`_format_last_exit`（`:142`）已渲染 `lastExit`，`label` 取自 `verdict.label`；承诺线触发时
`label="承诺退出线"`，故回执自然读作「上一笔持仓已由利润保护层强制平仓（**承诺退出线**）」。措辞仍由
`describe_exit_outcome`（pnl 符号派生）决定「锁定利润 / 按保护线离场」——承诺线被夹到 ≥ 保本线，通常为
正 ⇒「锁定利润」，诚实。**无需新分支**（既有 pnl-sign 派生天然覆盖）。

#### 2.3.4 盈利持仓自察段（`format_profit_hold_note` 微调）

`format_profit_hold_note`（`:233`）保持每轮索要退出线的既有文案，仅当 `committedExitPx` 非空时把
「① 你的退出线」一句改为提示模型「你当前的承诺退出线是 {committedExitPx}，如需收紧请给出更靠近现价的
`holdExitPx`」——把「散文索要」升级为「结构化收紧」的引导。触发阈值仍由
`ai_trade_profit_guard_note_after_rounds`（默认 1 ⇒ 首个盈利持仓轮即注入）决定，本轮不改。

### 2.4 C4：wire 与前端

- 快照两键 `committedExitPx` / `committedExitBasis`（+ 可选归因键 `effectiveExitSource`）已在 §2.2.3 定义。
- `ProfitGuardRule` 新增 `commit_enabled: bool = False`（末位默认 ⇒ 既有构造零回归）；
  `resolve_profit_guard_rule`（`:292`）从 `settings.ai_trade_profit_guard_commitment_enabled` 赋值；
  `rule_wire`（`:343`）加 `"commitEnabled": rule.commit_enabled`（前端据此决定是否展示承诺线区块）。
- 前端（详见 §三变更表）：`lib/types.ts` 的 `ProfitGuardSnapshot`（`:372`）加
  `committedExitPx?` / `committedExitBasis?` / `effectiveExitSource?`；`lib/aiTrade.ts`（`:612` 移动止盈
  文案旁）加 `describeCommittedExit`；`components/Chart/AiTradePanel.tsx`（`:173` 有效退出线旁）与
  `components/Agent/AiTradeTraceRound.tsx`（`:274`）显示承诺线；`lib/tradeExplain.ts` 的
  `inferKindFromReason` 映射 `profit_guard_committed_exit`（K 线标记 / 解释同源）。

### 2.5 C5：AI 回测接入（复用同一纯核心，绝不第二套判定）

v2 已把利润保护层与 LLM 决策接进 AI 回测（`backtest.py` 的 `AiBacktestRunner`：F3 bar 内极值保守成交序
`profit_guard_bar.py`、决策器带 `profit_rule` 宪章、`_run_decision` 产出 `TradeDecision`）。本轮：

- 回测决策器（`backtest.py:216`）`allow_hold_exit=(self.profit_rule is not None and
  self.profit_rule.commit_enabled)`（`allow_limit` 恒 False 不变）。
- `AiBacktestRunner` 新增棘轮字段 `self._committed_exit_px` / `self._committed_exit_basis`
  （init 在 `self._guard_state`/`self._cooldown` 旁，`backtest.py:205-206`）。**[P0-1] 它们必须在
  `self._guard_state = None` 的每一处同步清零**——即 `_apply_profit_guard_bar` 的 flat 复位
  （`backtest.py:399-401`）与保护平仓后复位（`backtest.py:413-414`）——否则回测里反手/重开也会继承旧
  承诺线，与实盘同缺陷。
- `_run_decision`（v2 已在此接冷却门禁）在拿到 `decision` 后，若 `decision.action=="hold"` 且
  `decision.hold_exit_px is not None`，先 `is_commit_armed(bar_snapshot["status"])` 门控（**[P1-1] 与
  实盘同一谓词、同一阈值**），再用**同一** `clamp_commitment`（纯几何、不传 armed）+ **同一**
  `ratchet_commitment`（**[P1-2] 唯一棘轮真源**）更新 `self._committed_exit_px`/`_basis`（bar 断面提供
  `mark/entry/breakeven/atr`）。**绝不在此内联另写一份收紧比较。**
- `profit_guard_bar.py` 的保守成交序把承诺线并入**下方保护线**白名单：第 1 步（先判回撤）改为
  `allow=(TRAILING, BREAKEVEN, COMMITTED_EXIT)`，bar facts 带 `committed_exit_px=self._committed_exit_px`。
  跳空成交、`min_hold`、`epoch=entry_bar` 等 v2 既定语义一字复用。
- **回测差异清单**（v2 §2.3.5 的免责段）追加一句：承诺线在回测中同样为 bar 内极值保守近似，且回测里
  的 `holdExitPx` 来自回测 LLM 的决策，与实盘的模型判断可能不同；**回测用于验证「承诺线一旦给出、系统
  会如何兑现」的机制，不保证实盘模型会给出相同的线**。
- **零回归**：`profit_rule=None` 或 `commit_enabled=False` 或模型不给 `holdExitPx`
  ⇒ `self._committed_exit_px` 恒 None ⇒ bar 快照无承诺线 ⇒ 判定逐字节等价 v2（G-4）。

### 2.6 一个可反算的完整算例（多头，所有数字须能被构造式反算，沿用 v1/v2 用例纪律）

`DOGE-USDT-SWAP`，`side=long`，`entry=0.07245`，`lev=5`，`qty_coin=1000`，`ATR=0.00080`，
默认参数（`arm=5%`、阶梯 `0.10:0.35,…`、`breakeven_arm=3%`、`breakeven_offset=0.2%`、`atr_floor_mult=0.8`）：

`breakeven_px = 0.07245×(1+0.002/5) = 0.0724790`；`atr_floor = 0.8×0.00080 = 0.00064`。

| 轮 | peak | mark | v2 移动止盈线 trail | 模型 `holdExitPx` | clamp 过程 | 棘轮后 committed（basis） | 有效退出线（归因） |
|---|---|---|---|---|---|---|---|
| R1 | 0.07500 | 0.07495 | tier≥10%→0.35：0.07500−0.00255×0.35=**0.0741075** | 0.07470 | 保护✓（<mark）；抗噪：mark−0.07470=0.00025<0.00064 ⇒ 推离至 0.07495−0.00064=**0.07431**；安全✓（>保本） | 无前值 ⇒ **0.07431**（atr_floor） | max(0.0741075, 0.07431, 0.0724790)=**0.07431**（**承诺线**） |
| R2 | 0.07520 | 0.07500 | 0.07520−0.00275×0.35=0.0742375 | 0.07485（收紧） | 保护✓；抗噪：0.07500−0.07485=0.00015<0.00064 ⇒ 推离至 0.07500−0.00064=**0.07436**；安全✓ | max(0.07431,0.07436)=**0.07436**（棘轮上移，atr_floor） | max(0.0742375, 0.07436, …)=**0.07436**（承诺线） |
| R3 | 0.07520 | 0.07480 | 同 R2=0.0742375 | 0.07400（试图放松） | 保护✓；抗噪：0.07480−0.07400=0.00080>0.00064 不推离，留 0.07400；安全✓ | max(0.07436,0.07400)=**0.07436**（更松被拒，前值原样） | max(0.0742375,0.07436,…)=**0.07436**（承诺线） |
| R4 | 0.07520 | **0.07434** | 0.0742375 | —（不给） | 前值原样 | **0.07436** | 现价 0.07434 ≤ 0.07436 ⇒ **触及承诺线 ⇒ 市价平仓**，锁定 (0.07436−0.07245)×1000=**1.91 USDT** |

对照 v2（无承诺线）：R4 的有效退出线只有 `max(trail 0.0742375, 保本 0.0724790)=0.0742375`，现价 0.07434
**未触及**，v2 会继续持有，直到价格跌到 0.0742375 才平（锁定 1.7875 USDT）。**本轮因兑现了模型自己在
R2 许下并棘轮到 0.07436 的承诺线，多锁了约 0.12 USDT，且提前一档离场。** 这正是「把模型的话变成有牙齿
的线」的收益，也正是回测（C5）要能复现的行为。

**这一格是本轮最易做错处，须反着读一遍**：R3 模型报 0.07400 试图放松——**棘轮必须拒绝**，否则等于允许
模型为「让利润再飞一会」找借口、把已锁的 0.07436 松回 0.07400，正是本轮要修的 bug 本身。任何让
`record_commitment` 在更松报价上覆盖前值的实现都破坏了棘轮的执行力（T-3 钉死）。

---

## 三、文件 / 模块变更计划

### 3.1 后端源文件（services/quant/app）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `ai_auto/profit_guard.py` | 修改 | 新增 `REASON_COMMITTED_EXIT` + `_REASON_LABEL`；`COMMIT_BASIS_*` 常量（[P2-5]）；`ProfitGuardRule.commit_enabled` + `resolve_profit_guard_rule`/`rule_wire` 透出；`PositionFacts` 两字段；**纯几何** `clamp_commitment`（[P1-1] 不含 arm 判定 + [P2-1] None-floor 守卫）；模块级 `is_commit_armed`（[P1-1]）与 `ratchet_commitment`（[P1-2]）；`_effective_exit`（三线合一 + 归因）替换 `_effective_exit_px`；`evaluate_profit_guard` 单次有效线判定 + `allow` 支持承诺码；`build_snapshot` **三键**（含 unavailable 块补键，[P1-3]） |
| `ai_auto/profit_guard_runtime.py` | 修改 | `committed_exit_px`/`committed_exit_basis`/**`committed_exit_epoch`**（[P0-1]）状态 + `clear_state` 三清 + `record_commitment`（调 `is_commit_armed`+`clamp_commitment`+`ratchet_commitment`）+ `refresh` **先按 epoch 比对作废旧承诺再** 用 `replace` 注入（新增 `from dataclasses import replace`） |
| `ai_auto/profit_guard_text.py` | 修改 | 宪章 `_charter_commitment`（`commit_enabled` 门控）；`_format_committed_line` + 有效退出线归因注明；`format_profit_hold_note` 结构化收紧引导 |
| `ai_auto/decider.py` | 修改 | `TradeDecision.hold_exit_px` 末位字段；`AiTradeDecider(allow_hold_exit=False)`；`_map_decision` 的 hold 分支解析 `holdExitPx`（非法即 None，不降级）+ `_finite_px`；`commit_round` 落 journal |
| `ai_auto/prompts.py` | 修改 | `build_decision_tool_schema`/`build_decision_tool_description` 加 `allow_hold_exit`（短路条件同步）；模块常量不动 |
| `ai_auto/manager.py` | 修改 | 实盘决策器 `allow_hold_exit=req.profit_rule.commit_enabled`（`:386`）；宪章经既有 `profit_rule` 通路自动渲染承诺段 |
| `ai_auto/backtest.py` | 修改 | 回测决策器 `allow_hold_exit`（`:216`）；runner `_committed_exit_px`/`_basis` 字段（**[P0-1] 在 `_guard_state=None` 两处同步清零**，`:399-401`/`:413-414`）；`_run_decision` 采集 `decision.hold_exit_px` 走**同一** `is_commit_armed`+`clamp_commitment`+`ratchet_commitment`（[P1-1]/[P1-2]） |
| `ai_auto/profit_guard_bar.py` | 修改 | 保守成交序把 `COMMITTED_EXIT` 并入下方保护线 `allow`；bar facts 带承诺线 |
| `ai_auto/session.py` | 修改 | `_record_profit_commitment` 新方法 + 决策后一行调用（`:562` 旁）；`_maybe_profit_exit` 无需改（承诺线经快照自动生效） |
| `config.py` | 修改 | 新增 `ai_trade_profit_guard_commitment_enabled: bool = True`（承诺功能总闸；False 逐字回退 v2） |

### 3.2 后端测试（services/quant/tests）

| 文件 | 动作 | 覆盖 |
|---|---|---|
| `test_ai_trade_profit_guard.py` | 修改 | `clamp_commitment` 纯几何各路（非有限/非保护/抗噪推离/夹保本/抑制/**no_floor 守卫**/正常采纳）；`is_commit_armed` 真值表 + `ratchet_commitment` 单调（首采/收紧覆盖/放松拒绝，多空各一，T-7）；`_effective_exit` 三线归因（**含并列 tie-break 用 key 而非元组比较**，[P2-2]）；`evaluate` 承诺线触发；**G-1/G-3 逐字节等价**（无承诺时快照与判定不变） |
| `test_ai_trade_decider.py` | 修改 | `holdExitPx` 解析（合法/非法/非 hold 忽略/不降级）；`allow_hold_exit` schema 恒等断言（False 时 `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA`） |
| `test_ai_trade_session.py` | 修改 | `_record_profit_commitment` 接线（hold+有值才记、门禁前落点）；承诺线触发经 `_maybe_profit_exit` 平仓；**T-6 反手/重开后承诺线归零（[P0-1]，须先红）**；epoch 清空；未开启零键 |
| `test_ai_trade_prompts.py` | 修改 | 宪章承诺段（开/关逐字节）；工具描述 `allow_hold_exit` 后缀；每轮承诺线渲染 |
| `test_ai_backtest_runner.py` | 修改 | 回测 `holdExitPx` 经同一 `clamp_commitment` 棘轮触发一次承诺平仓；`profit_rule=None`/不给承诺逐字节等价 v2（G-4）；差异 notes 含承诺句 |

### 3.3 前端（apps/web）

| 文件 | 动作 | 意图 |
|---|---|---|
| `lib/types.ts` | 修改 | `ProfitGuardSnapshot` 加 `committedExitPx?`/`committedExitBasis?`/`effectiveExitSource?`；rule 回执类型加 `commitEnabled?` |
| `lib/aiTrade.ts` | 修改 | `describeCommittedExit`（承诺线一行文案）+ **独立** `COMMIT_BASIS_SUFFIX` 映射（[P2-5]，须含 `adopted`/`atr_floor`/`clamped_breakeven` 三键，勿直接复用 `TRAIL_BASIS_SUFFIX`）；面板/轨迹的「由承诺线定住」标注**只读 `effectiveExitSource`**（[P1-3]，不比价推断） |
| `lib/tradeExplain.ts` | 修改 | `inferKindFromReason` 映射 `profit_guard_committed_exit`→承诺退出线（K 线标记 / explain 同源） |
| `components/Chart/AiTradePanel.tsx` | 修改 | 有效退出线区块（`:173`）下显示承诺线与「当前由承诺线定住」标注 |
| `components/Agent/AiTradeTraceRound.tsx` | 修改 | 轨迹保护段（`:274`）显示承诺线 |
| `lib/aiTrade.test.ts` | 修改 | `describeCommittedExit` 正常 / 缺省；`inferKindFromReason` 承诺码 |
| `components/Chart/AiTradePanel.test.tsx` | 修改 | 承诺线渲染 / 缺省不渲染 |
| `components/Agent/AiTradeAgentTrace.test.tsx` | 修改 | 承诺线在轨迹保护段可见 / 缺省 |
| `lib/tradeExplain.test.ts` | 修改 | 承诺退出线 kind 推断 |

---

## 四、接口设计

### 4.1 决策工具 `submit_trade_decision`（承诺开启时）

`input_schema.properties` 追加（其余字段不变）：

```jsonc
"holdExitPx": { "type": "number", "exclusiveMinimum": 0,
  "description": "（可选）继续持有时你承诺的退出价位……只会据它把保护线收得更紧、绝不放松，且不劣于保本线" }
```

`required` 不变（仍为 `["action", "reason"]`）——承诺退出线永远可选。

### 4.2 配置（`config.py`，`Settings`）

| 键 | 类型/默认 | 语义 |
|---|---|---|
| `ai_trade_profit_guard_commitment_enabled` | `bool = True` | 承诺退出线总闸；`False` ⇒ schema/宪章/判定逐字节回退 v2 |

复用（不新增）：`ai_trade_profit_guard_atr_floor_mult`（承诺线抗噪下界）、
`ai_trade_profit_guard_note_after_rounds`（每轮问责阈值）、`ai_trade_profit_guard_enabled`（保护层总闸；
关则本特性亦不生效）。

### 4.3 wire 快照新键（`profitGuard` 对象内，camelCase）

| 键 | 类型 | 语义 |
|---|---|---|
| `committedExitPx` | `number \| null` | 已棘轮的承诺退出线；无承诺 / unavailable / flat ⇒ null |
| `committedExitBasis` | `string \| null` | `adopted` / `atr_floor` / `clamped_breakeven`（被哪条约束定住） |
| `effectiveExitSource` | `string \| null` | `profit_guard_trailing` / `profit_guard_committed_exit` / `profit_guard_breakeven`（有效退出线当前由哪条线定住，供前端归因，后端单点真源） |

`rule_wire` 加 `commitEnabled: boolean`。

---

## 五、数据模型

- **无 DB 迁移、无新表、无新列。** 承诺退出线是**会话内存态**（`ProfitGuardRuntime.committed_exit_px`），
  随 epoch 生灭；触发平仓走既有 `trades` 写路径，`trade.reason=profit_guard_committed_exit` 复用既有
  `reason` 列，捕获率 F4 的 `trades.peak_pnl` 一列亦复用（承诺线触发同样落峰值浮盈）。
- **内存态**：`ProfitGuardRuntime` 新增 `committed_exit_px: float|None`、`committed_exit_basis: str|None`
  **以及 `committed_exit_epoch: tuple|None`（[P0-1] 身份戳）**。`ProfitGuardState` 仍不加字段，但
  **承诺线不是「与 epoch 无关」的**——恰恰相反，它**是 epoch 作用域状态，必须与 `peak_px` 共用
  `(side, seq)` 身份**（v1 [P0-1] 的教训）。之所以不塞进 `ProfitGuardState`，是因为 v2 起会话侧未持有
  可写的 state 引用来记承诺；改为在 `refresh` 里用 `committed_exit_epoch` 与 `advance_state` 产出的新
  epoch 比对、不一致即作废（见 §2.2.2）——效果等价于「随 peak 同生死」，但不改动 `advance_state`。
  **（原 v1 稿此处误判承诺线与 epoch 无关，是本轮 P0-1 的根因，已订正。）**
- **规则态**：`ProfitGuardRule` 新增 `commit_enabled: bool = False`（frozen dataclass 末位默认）。
- **决策态**：`TradeDecision` 新增 `hold_exit_px: float|None = None`（末位默认）。
- **wire 态**：见 §4.3。前端 `ProfitGuardSnapshot` 三个可选键 + 规则回执 `commitEnabled`。

---

## 六、测试与验收标准

### 6.1 必须先红后绿的用例（T-x，改业务码之前先写）

- **T-1（承诺线兑现平仓）**：构造 §2.6 R4 断面（trail=0.0742375、committed=0.07436、mark=0.07434），
  `evaluate_profit_guard` 返回 `REASON_COMMITTED_EXIT` 且 `exit_px=0.07436`；把 committed 置 None 时
  返回 `None`（同断面 v2 不平）。构造式断言，不写字面价。
- **T-2（抗噪推离 + 夹保本）**：`clamp_commitment` 对「离现价 0.0002 的报价」推离到 `mark−0.8×ATR`；
  对「低于保本线的报价」在保本线在时夹到保本线、在保本线关时返回 `(None, "suppressed_at_floor")`。
- **T-3（棘轮单调）**：先记 0.07436，再报更松的 0.07400 ⇒ 承诺线仍 0.07436（更松被拒）；再报更紧的
  0.07460（经抗噪后）⇒ 上移。epoch 变化（`clear_state`）后归零。
- **T-4（非保护 / 未武装忽略）**：`holdExitPx > mark`（多头）⇒ 不采纳；未武装快照 ⇒ 不采纳；
  `action≠hold` ⇒ session 不调 `record_commitment`。
- **T-5（回测承诺线触发）**：回测 LLM 返回带 `holdExitPx` 的 hold ⇒ 经同一棘轮，后续 bar 现价穿越即
  由 `profit_guard_bar` 保守成交序平出，`aiStats.profitGuardExits` +1；不给承诺时逐字节等价 v2。
- **T-6（承诺线随 epoch 复位，[P0-1] 必须先红）**：在多头 epoch 记下 committed=X 后，
  ① **反手** `long→short`（同一 `refresh` 内 `advance_state` 的 side 变化，不经 flat）⇒ 断言
  `runtime.committed_exit_px is None` 且新空头快照 `committedExitPx is None`、`evaluate_profit_guard` **不**
  据旧线平仓；② **同向重开**（seq 变化）⇒ 同样归零。回测侧对称补一条（反手 bar 后 `self._committed_exit_px
  is None`）。**这是全轮最易回归的实盘安全点**：若实现只在 `clear_state()` 里清承诺、未在 `refresh` 按
  epoch 比对，此用例必红。
- **T-7（`ratchet_commitment` / `is_commit_armed` 纯函数直测，[P1-1]/[P1-2]）**：`ratchet_commitment`
  首次采纳、更紧覆盖、更松/相等拒绝（多空各一）；`is_commit_armed` 对 `armed`/`breakeven_armed` 为真、
  `idle`/`unavailable`/`flat`/`None` 为假——实盘与回测**共用**这两个纯函数（同一 import，杜绝双份判据）。

### 6.2 零回归门禁（G-x，全绿方可提交）

- **G-1**：`commit_enabled=False`（或模型不给 `holdExitPx`）⇒ 快照键值、`evaluate` 判定、prompt 文本、
  工具 schema 与 v2 逐字节一致；`build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA` 恒真。
- **G-2**：宪章 / 工具描述在 `commit_enabled=False` 下逐字节回退 v2（grep 承诺段关键词零命中）。
- **G-3**：`_effective_exit` 重构在 `committed_px=None` 下与旧 `_effective_exit_px`/`evaluate` 双 for
  分支判定与归因逐字节一致（§2.2.3 证明的可执行化）。
- **G-4**：回测 `profit_rule=None` 或不给承诺 ⇒ 与 v2 基线同一 run 结果逐字节一致。
- **G-5（never-worse）**：任意断面下，含承诺线的有效退出线**不劣于**（多头 ≥、空头 ≤）v2 的有效退出线
  （承诺线只进 max/min 的候选、且 ≥ 保本线）——形式化断言 + 随机化对拍用例。**注意口径**：never-worse 指
  「退出**价位**不更松」，**不是**「不会更早离场」——更早离场（承诺线比 trail 更紧时提前一档平仓）正是本轮
  的收益。当 v2 有效线为 `None`（无线可守）而承诺线存在时，承诺线因恒 ≥ 保本/入场价（G-6）而不构成净亏
  劣化，此 None-基线情形按 G-6 判定，不纳入 G-5 的数值比较。
- **G-6（never 净亏强平）**：`clamp_commitment` 的返回线恒 ≥ `floor_line`（多头）/ ≤（空头），即触发价
  绝不劣于保本线（保本线在时）或入场价（保本线关时，且此时报价 < 入场价一律抑制不采纳）。
- **G-7（epoch 身份，[P0-1]）**：承诺线的 `(side, seq)` 身份与 `peak_px` 恒一致——任何使
  `advance_state` 新建 epoch 的输入（`side` 变 / `seq` 变 / `state is None`），都使 `committed_exit_px`
  归零；T-6 覆盖反手与重开两路径。
- **G-8（单一真源，[P1-1]/[P1-2]）**：全仓库对「承诺是否武装」只有 `is_commit_armed` 一处判定、对
  「棘轮只收紧」只有 `ratchet_commitment` 一处判定；实盘 `record_commitment` 与回测 `_run_decision`
  **同 import 同调用**（grep 确认无第二份内联收紧比较、无第二处 arm 阈值）。

### 6.3 验收标准

1. `spec.md` 存在且 `git status` 可见；本节及以上八节齐备、篇幅 ≥ 800 字。
2. C1：装配 + 开启时决策工具露出 `holdExitPx`；`action=hold` 携带的合法值被 `TradeDecision.hold_exit_px`
   采集，非法值忽略且不降级。
3. C2：模型承诺线经 `clamp_commitment` + 单调棘轮进入有效退出线，触及即由既有 `_maybe_profit_exit` /
   回测保守成交序平仓；更松报价被拒；epoch 结束清空。
4. C3：宪章披露「只收紧不放松、不劣于保本、仅移动止盈兜底」；每轮显示当前承诺线；触发回执归因「承诺
   退出线」，措辞由 pnl 符号派生。
5. C4：快照两键 + 归因键；前端面板 / 轨迹 / K 线解释显示承诺线；未给承诺不渲染该行。
6. C5：回测复用同一纯核心，承诺线可在回测中被兑现；差异清单含承诺免责句；不给承诺逐字节等价 v2。
7. G-1..G-6 全绿；后端 pytest / ruff、前端 vitest / tsc 与合入前同一失败集合（他人在制孤儿用例除外）。

---

## 七、风险与缓解

| 编号 | 风险 | 缓解 |
|---|---|---|
| R-1 | **承诺线制造净亏强平**（v2 P0-1 的同类隐患换位置）：模型报一条 ≤ 入场价的线，系统据它把持仓在亏损处强平并宣称「锁定利润」 | `clamp_commitment` 第 3 步复用 v2 入场价夹紧 / 抑制：保本线在 ⇒ 夹到保本线；保本线关且报价 < 入场价 ⇒ **抑制不采纳**。措辞一律 pnl 符号派生（G-6 + T-2 钉死） |
| R-2 | **承诺线比 v2 移动止盈更松，反而放走利润** | 承诺线只作为**候选**进 max/min，永不替换更紧的线；且棘轮**只收紧不放松**。有效退出线恒 ≥ v2（G-5 never-worse 形式化断言） |
| R-3 | **抗噪下界把承诺线推得离现价太远，模型以为在近处守、系统在远处守**（归因失真，v2 [R-5] 教训） | `committedExitBasis=atr_floor` 如实下发；每轮承诺线一行注明「（已按波动率放宽至下界）」；文案与判定共用同一份快照真源，不前后端各算 |
| R-4 | **模型每轮反复报被拒的更松承诺**（棘轮拒绝后不知情，烧 LLM、污染上下文，`hold-inertia` 同类坑） | 宪章明说「报更松的线不会生效」；每轮显示当前生效承诺线，模型据它判断是否需要收紧，而非盲报 |
| R-5 | **schema 恒等断言被破**：`allow_hold_exit` 默认非 False 会让 `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA` 当场失败 | `allow_hold_exit: bool = False` 默认；短路条件三者皆假才返回模块常量；G-1 恒等断言守门 |
| R-6 | **回测与实盘不同构**（承诺线来自各自 LLM，且回测按根粗粒度） | 复用**同一** `clamp_commitment` + 同一 `evaluate` `allow` 白名单；差异清单追加承诺免责句（§2.5）；回测 `holdExitPx` 来自回测 LLM，明说「验证机制而非保证实盘同样给线」 |
| R-7 | **`_effective_exit` 重构触碰安全关键 `evaluate`** | G-3 逐字节等价证明 + 既有 evaluate 全量用例不改而绿；重构只把「两次固定序 `_touched`」换成「单次取最靠近现价者」，数学等价（trail ≥ breakeven 恒成立） |
| R-8 | **`profit_guard.py` 现 646 行、`session.py` 现 1393 行（后者已越 800 红线），本轮再加逼近 / 超红线** | **[P2-6] 评审定论**：`profit_guard.py` 加本轮增量（clamp≈25 + 两纯函数 `is_commit_armed`/`ratchet_commitment`≈12 + `_effective_exit`/键/字段≈15）后约 700 < 800，**故本轮不拆分**，`clamp_commitment` 等留在 `profit_guard.py`（去掉原「留评审判」二义）。`session.py` 已是既有欠账（非本轮引入），本轮只加一个 5 行方法 + 一行调用、delta 最小，**但不得再在其上堆更多**；渲染增量一律落 `profit_guard_text.py`（现 250 行） |
| R-9 | **承诺线在 in-flight 决策轮的时序**：记录用本轮快照、兑现用下一次 refresh | 承诺线是现价下方止损，记录时 `line<mark`，同轮不可能已触及；`_exec_lock` + 取锁后复检 `projected!=flat` 沿用 v1（承诺平仓与模型决策轮互斥安全） |
| R-10 | **[P0-1] 原子反手/重开继承旧承诺线强平新仓** | 承诺线绑定 `(side, seq)` epoch 身份，`refresh` 按 `advance_state` 新 epoch 主动作废（§2.2.2）；G-7 + T-6 双向钉死（反手 side 变、重开 seq 变） |
| R-11 | **[P1-1/P1-2] 实盘/回测判据双份漂移**（武装阈值不一致、棘轮各写一份） | `is_commit_armed` + `ratchet_commitment` 两模块级纯函数为唯一真源，实盘与回测同 import 同调；`clamp_commitment` 退化为纯几何（不自带武装）；G-8 grep 守门 |
| R-12 | **[P1-3] 归因来源二义致显示与实际平仓码不符** | `effectiveExitSource` 由后端 `_effective_exit` 单点下发，前端只读不推；unavailable/flat ⇒ None |

---

## 八、实现顺序与提交注意

1. **纯核心先行**：`profit_guard.py`（`clamp_commitment` 纯几何 + `is_commit_armed` + `ratchet_commitment`
   + `_effective_exit` + `evaluate` + 快照三键 + `commit_enabled` + `COMMIT_BASIS_*`），先写 T-1..T-4、
   T-7 与 G-1/G-3/G-5/G-6/G-8 并跑绿——这一步不碰任何编排点，风险最低。
2. **运行时棘轮 + epoch 绑定**：`profit_guard_runtime.py`（`committed_exit_epoch` 状态 + `record_commitment`
   调三纯函数 + `refresh` **先按 epoch 比对作废再** `replace` 注入 + `clear_state` 三清），先写 **T-6**
   （反手/重开归零）与 G-7 并跑绿——**这是本轮实盘安全的关键步，务必先红后绿**。
3. **决策与 prompt**：`decider.py`（`hold_exit_px` + `allow_hold_exit` + 解析）、`prompts.py`（schema/描述）、
   `profit_guard_text.py`（宪章 / 渲染），跑 G-1/G-2 恒等与逐字节。
4. **实盘接线**：`session.py` 一行调用 + `manager.py` 门控派生；`config.py` 总闸。
5. **回测接线**：`backtest.py` + `profit_guard_bar.py`，跑 T-5 / G-4。
6. **前端 + 前端用例**：类型 → 文案 → 面板 / 轨迹 / explain → vitest。

**提交注意（Subtask #3）**：`config.py` 与他人在制特性（`ai_trade_enabled` 总闸翻转）重叠，仍须
**按 hunk 行级暂存**——`git diff --no-color -- config.py > full.patch` → **以 bytes 读写**切出承诺配置那一个
hunk（Windows 下 `write_text` 会把 LF 翻 CRLF 弄坏 patch）→ 修 `@@` 头新侧起始行 → `git apply --cached
--recount`。验收：`git diff --cached --stat -- config.py` 纯 insertions；`git diff --cached | grep
ai_trade_enabled` 无命中。`.env.example` / `desktop/src/lib/config.js` / `db/live_auto_repositories.py` /
`engine/factors/*` + 相关 factor-library 孤儿测试**不得一并 add**（它们属其他在制特性）。`git add` 只列
本特性 §三 三张表里的具体路径，**严禁 `git add -A`**。

---

## 九、实施过程发现的方案缺陷（Subtask #2 待填）

> 本节留给代码开发节点：若实现中发现方案与真实代码不符（行号漂移、遗漏调用点、时序反转等），
> 在此逐条记账（编号 D-x），并说明就地修正方式，供终审逐条核对。

- **D-1 · `lib/tradeExplain.ts` 的 `inferKindFromReason` 无需任何逻辑改动**（§3.3 列它为「修改」，但
  §评审记录已判「分类零改」）。真实代码里 `inferKindFromReason`（`tradeExplain.ts:60`）按
  `reason.startsWith("profit_guard_")` 前缀归类，`profit_guard_committed_exit` **自动**归入 `profit_guard`
  kind。就地处理：源文件只把该行注释从「三码」更新为「四码（含 commitment）」以如实反映新码存在；真正的
  回归护栏落在 `tradeExplain.test.ts`——把承诺码加进既有「四个 reason 码」遍历用例。**未新增分类分支。**

- **D-2 · `lib/aiTrade.ts` 的两处标签映射一并补上承诺码（完整性补强，非方案缺陷）**。§3.3 对 aiTrade.ts
  只显式点名 `describeCommittedExit` + `COMMIT_BASIS_SUFFIX` + 「由承诺线定住」标注，但既有的
  `PROFIT_GUARD_REASON_LABELS`（成交/轨迹/回执共用）与 `DEGRADE_REASON_LABELS`（成交原因列）都是**按码
  查表的单一真源**——漏接新码会让 `profit_guard_committed_exit` 在这两处渲染成裸英文（v1 终审 RV-1
  「新中文化 helper 漏接展示点」的同类坑）。故一并补 `profit_guard_committed_exit → 承诺退出线`。**纯加
  法，不改既有码的渲染。**

- **D-3 · 回测侧引入两个小助手 + 快照回填承诺线（对齐实盘 `refresh` 语义，未偏离设计）**。①
  `AiBacktestRunner._reset_guard_epoch()`：把 §2.5 要求的「在每处 `self._guard_state=None` 同步清零承诺线」
  收敛为**单一助手**，在 flat 复位（`backtest.py`）与保护平仓后复位两处同调——避免两份内联三清漂移（本
  仓库单一真源纪律）。② `AiBacktestRunner._record_commitment(decision, snapshot)`：§2.5 的采集逻辑抽成一
  个方法，实盘 `record_commitment` 与它**同调** `is_commit_armed`/`clamp_commitment`/`ratchet_commitment`
  三纯函数（G-8）。③ `_profit_guard_wire` 的 `PositionFacts` 现回填 `committed_exit_px`/`_basis`——否则
  回测模型每轮看不到自己已棘轮的承诺线与有效退出线归因（实盘 `refresh` 用 `dataclasses.replace` 做同一
  件事，§2.2.2）。三者都是把设计意图落地的必要接线，**非行为偏离**。

- **D-4 · rule 回执类型 `commitEnabled` 落在 `lib/aiTradeTypes.ts` 而非 `lib/types.ts`（真实位置校正）**。
  §3.3 表把「rule 回执类型加 commitEnabled?」列在 `lib/types.ts`，但真实代码里承诺规则回执类型
  `AiTradeProfitGuardRule` 定义在 `lib/aiTradeTypes.ts:221`（`ProfitGuardSnapshot` 才在 `types.ts:372`
  且经 aiTradeTypes 再导出）。故三个快照键（`committedExitPx`/`committedExitBasis`/`effectiveExitSource`）
  落 `types.ts`，`commitEnabled?` 落 `aiTradeTypes.ts`——各归其真源，多动一个文件 `aiTradeTypes.ts`，
  行为与设计一致。

---

## 十、评审结论（v2）

**有条件通过（approved with conditions）。**

方案立意正确、落点扎实：诊断优先级 ③（根因 ⑤）确是前两轮唯一「只做提示、没做执行」的缺口，把模型每轮
被索要、却从不被采集/执行的退出线做成结构化、单调棘轮、确定性兑现的第三条保护线，是全局目标「在合适的
时候保住利润」的最后一块。经在真实代码上逐条核对：所有扩展点（`allow_limit` 门控范式、`_charter_*` 助手、
`ARMED_STATUSES`、快照键、`inferKindFromReason` 前缀归类、两处前端插桩点、恒等断言）**均真实存在且可用**，
`_effective_exit` 重构的 G-3 逐字节等价论证**成立**，never-worse / fail-open / 零迁移 / 关闭逐字节回退等
不变式设计合理。**可行性充分，规模适配得当**（一个特性四部件、零 DB 迁移、零新依赖、零交易所写路径）。

评审共提 **1 P0 + 3 P1 + 7 P2**，其中 **P0/P1 全部已在文档正文就地修复**（版本升 v2）：

- **P0-1**（承诺线未绑 `(side, seq)` epoch，反手/重开继承旧线强平新仓）——已改为 epoch 身份戳
  `committed_exit_epoch` + `refresh` 按新 epoch 主动作废，订正 §五 误判，新增 T-6/G-7/R-10。
- **P1-1**（实盘/回测武装阈值不一致）——`clamp_commitment` 退化纯几何，武装收敛到单一谓词 `is_commit_armed`。
- **P1-2**（棘轮双份实现）——抽 `ratchet_commitment` 纯函数，实盘/回测同调，G-8 守门。
- **P1-3**（归因来源二义）——`effectiveExitSource` 强制后端单点真源，删前端推断备选。

### 放行条件（Subtask #2 代码开发节点必须逐条落实，终审逐条核对）

1. **[P0-1 头等] 承诺线必须与 `peak_px` 共用 `(side, seq)` epoch 身份**：`refresh` 在 `advance_state`
   之后按 `committed_exit_epoch != (state.side, state.seq)` 作废旧承诺；`clear_state()` 三清；回测在
   `self._guard_state=None` 两处（`backtest.py:399-401`/`413-414`）同步清零。**T-6 必须先红后绿**——
   若只在 `clear_state` 清承诺、未在 `refresh` 按 epoch 比对，反手（`executor.py:226-229` 原子 long→short）
   会强平新仓，这是全轮最高危回归点。
2. **[P1-1] 单一武装谓词**：`is_commit_armed(status)` 一处定义，实盘 `record_commitment` 与回测
   `_run_decision` 都据它门控；`clamp_commitment` **不含** arm 阈值分支（纯几何）。
3. **[P1-2] 单一棘轮真源**：`ratchet_commitment` 纯函数一处定义，实盘与回测同 import 同调；grep 确认无
   第二份内联收紧比较（G-8）。
4. **[P1-3] 归因单点**：`build_snapshot` 从 `_effective_exit` 取归因码下发 `effectiveExitSource`（含
   unavailable-None 块补键）；前端只读不推。
5. **[P2-1] fail-open**：`clamp_commitment` 在 `floor_line is None` 时返回 `(None,"no_floor")`，绝不抛。
6. **[P2-2] tie-break**：`_effective_exit` 用 `max/min(cands, key=lambda c: c[0])`，**严禁** `max(cands)`
   真元组比较；保留「三下方线码在 `allow` 一致放行」不变式的注释。
7. **[P2-3] hold 守卫**：`holdExitPx` 解析显式以 `action == "hold"` 为条件（close/cancel_order ⇒ None），
   落在 `decider.py:371` fall-through 前。
8. **[P2-4] 宪章不越承诺**：承诺段加「在保护层可用的前提下」，与既有 fail-open 条款一致，不宣称无条件保证。
9. **[P2-5] basis 命名**：承诺线用独立 `COMMIT_BASIS_*`（`adopted`/`atr_floor`/`clamped_breakeven`）+
   前端独立 `COMMIT_BASIS_SUFFIX`（含 `adopted` 键）；测试钉死三种 basis 各自渲染。
10. **[P2-6/P2-7] 体量与文案**：`clamp_commitment` 等留在 `profit_guard.py`（约 700<800，不拆）；`session.py`
    只加 5 行方法 + 一行调用，不再增；`describe_exit_outcome` 复用 pnl 符号派生、不硬编「锁定利润」。
11. **门禁全绿**：G-1..G-8 与 T-1..T-7 全过；后端 pytest/ruff、前端 vitest/tsc 与合入前**同一失败集合**
    （他人在制孤儿用例 `test_factor_library_expansion.py`、既有 `WeightBatchArena.test` tsc 报错除外）。
12. **[提交纪律] 混合文件行级暂存**：`config.py` 与他人在制 `ai_trade_enabled` 翻转重叠，须按 hunk 以
    **bytes 读写** patch 精确暂存（Windows `write_text` 会翻 CRLF 弄坏 patch）；`git add` 只列本特性 §三
    三张表里的具体路径，**严禁 `git add -A`**（§八已述，此处再钉一次）。

以上 12 条全部落实且门禁全绿，即视为满足验收；P0/P1 已在本 v2 正文闭合，Subtask #2 无需再改设计、按图施工即可。
