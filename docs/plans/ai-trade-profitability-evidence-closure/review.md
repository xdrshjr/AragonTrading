# 第三轮收益证据闭环终审

结论：工程终审通过。基线为 `b42f9dd`，逐项核对 v2 的 F01–F58 和
A01–A58，修复本次发现的问题，使用独立提交副本完成验证。
收益目标仍为证据不足，不能以测试通过推导真实盈利。

## 本次终审修复

| 编号 | 等级 | 问题、修改与验证 |
|---|---|---|
| CR3-01 | P1 | 回测空资金费窗口仍计算名义额，None 抛异常，NaN/Inf 通过零乘法污染后续累计。空窗口直接保留零增量，有结算点才读取价格。三个失败用例均在资金费155项回归中通过。 |
| CR3-02 | P1 | 结构异常仅在全局出现，未进入可确定的标的、周期、档位切片。传递同一dataset的run维度与问题，保留零往返问题行；未知维度及退出来源不伪造归属。纯函数与真实SQLite链路均通过。 |
| CR3-03 | P2 | 覆盖组件使用了与真实后端不一致的原因码，经济缺口和孤立平仓显示英文内部码。补齐实际经济与结构原因映射，测试改用真实原因码并确认中文显示。 |
| CR3-04 | P2 | 切片收到总体和排除数量却只显示可计样本数。补齐类型与显示，保留旧接口兼容，结构问题单列；全排除ETH样本和窄容器浏览器验收通过。 |
| CR3-05 | P2 | 新样本装配函数超过50行，部分注释仍描述旧零费率关闭和缺维度丢弃行为。拆出金额投影、同步相关注释，金额算法和资格不变；收益费用171项复验通过。 |

全部修复写入现有计划文件，无未解决的本轮阻断问题，无新增依赖或数据库迁移。

## 设计覆盖与相邻回归

收益总体使用一个LEFT JOIN SELECT，调用者事务所有权保留；按成交id归并，
正常未平仓、结构问题及被排除往返分别披露。手续费使用同笔冻结分解与原始
多腿证据配对，旧版本不冒充新预测。公共资金费预计率与实测率分离，
历史前缀及到期覆盖缺口不使用未来点。战绩作为第四段冻结材料贯穿发送、
重试、反思与摘要，旧摘要继续标为legacy。前端未知、零、负数分别呈现。

F01–F58的逐文件落点见下方清单。A01–A56的行为由计划内21个可独立提交的
后端文件与前端套件覆盖；A57在验收脚本中保留每次原生命令退出码并隔离
子进程环境。A58继承并核对上游固定性能证据：300个run、10000条成交、
5个标的、20%费用缺口，新旧中位耗时比0.90918，SQL由2次降至1次。
本节点未重跑该性能基准，不将其表述为新测量；本次新增切片问题归属不新增SQL。

已核对外部契约：预计fundingRate与实际realizedRate、毫秒fundingTime、
after向旧数据分页，均与[OKX官方文档](https://app.okx.com/docs-v5/zh/#public-data-rest-api-get-funding-rate-history)一致。
单SELECT方案避免依赖SQLite旧事务模式中SELECT自动BEGIN的假设，符合
[SQLAlchemy官方说明](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#legacy-transaction-mode-with-the-sqlite3-driver)。
查阅日期：2026-09-07。

## 本节点实际验证

所有工程测试使用合成数据，测试副本位于
`tmps/final-evidence-review/checkout`，由基线和本功能精确投影组成。

| 验证 | 结果 |
|---|---|
| 收益、费用、真实SQLite接口与归并 | 171通过 |
| 资金费解析、因果、计提与成本 | 155通过 |
| 冻结上下文、摘要、回测、版本兼容 | 268通过 |
| 多标的可交易性、执行、结算恢复、先验 | 111通过 |
| 后端合计，不重复计算复验 | 705通过，各进程退出0 |
| 前端完整副本 | 236个文件、2733项通过 |
| Next生产构建与TypeScript | 均退出0 |
| 浏览器真实组件与生产CSS | 200/240/360px无外部溢出；中文原因、总体、键盘展开、焦点和页面异常检查通过 |
| Ruff、diff检查、新核心尺寸 | 通过；新核心文件≤800行、函数≤50行 |

复现阶段4个后端断言、3个前端契约断言按预期失败，随后全部修复通过。
首次前端运行因环境继承NODE_ENV=production失败，调整测试环境后再验证真实
断言；首次浏览器解释器缺playwright，改用已安装依赖的全局解释器后通过。
这些环境失败均保留日志，没有隐藏或当作产品测试通过。

上游扩大后端回归曾有既有thin_seam检查失败与Windows原生退出异常。
本节点相关分组均正常退出，但没有运行全后端，因此不宣称全后端通过。

## 真实历史复核

对上游固定快照使用最终提交代码和只读、immutable连接重新归并，
核查前后SHA256均为
`6b120836764562389abbc51272c9aeaf712dc0124902e9ccf2b14a737bdaff38`。
本节点未读取或写入生产库，未新增实盘交易。

总往返310笔，可核算121笔，排除189笔，实测完整0笔。
可核算子集双腿手续费30.996662015 USDT，交易净额−42.313902015 USDT，
不含资金费。保留上游官方资金费绑定和连续观察区间缺口结论，未执行bootstrap。
结论为 **inconclusive**；这些数据不能支持收益达标或未来盈利承诺。

## 提交边界

提交包含57个计划源码/测试文件和spec、review两份文档，共59个路径。
`decider.py`只纳入版本常量接线，`repositories.py`只纳入dataset再导出；
`test_ai_trade_memory_integrity.py`保留工作区Journal专属测试但不提交该段。
F52的整个Journal提示词重建测试尚未跟踪，依赖尚未提交的Journal功能，
故其版本同步保留在原工作区；同一v4协议在本提交的advisory及track_record
测试中独立验证。这样无需将Journal功能、导航、配置、表结构或其他工作混入提交。

暂存逐个使用明确文件路径；重叠文件只暂存已验证投影，随后立即恢复原字节。
未暂存构建输出、运行日志、数据库、tmps或任务提示文件，不绕过Git hooks。
提交后的文件清单、暂存区及剩余改动必须由单独核验记录确认。

命令、退出码、指纹和截图位于`tmps/final-evidence-review/`；
可审计证据通过本节点AgentMesh runlog归档，原始数据库不进入证据包或提交。

## 逐文件核对

下表由v2计划路径生成；“纳入”均已检查实现和对应测试，“保留”说明见提交边界。

| 计划 | 文件 | 处理 |
|---|---|---|
| F01 | `services/quant/app/ai_auto/profitability_sample.py` | 纳入；实现与回归已核对 |
| F02 | `services/quant/app/db/ai_profitability_repositories.py` | 纳入；实现与回归已核对 |
| F03 | `services/quant/app/db/ai_auto_repositories.py` | 纳入；实现与回归已核对 |
| F04 | `services/quant/app/db/repositories.py` | 纳入；实现与回归已核对 |
| F05 | `services/quant/app/ai_auto/profitability.py` | 纳入；实现与回归已核对 |
| F06 | `services/quant/app/ai_auto/calibration.py` | 纳入；实现与回归已核对 |
| F07 | `services/quant/app/ai_auto/track_record_service.py` | 纳入；实现与回归已核对 |
| F08 | `services/quant/app/schemas.py` | 纳入；实现与回归已核对 |
| F09 | `services/quant/app/ai_auto/trip_ledger.py` | 纳入；实现与回归已核对 |
| F10 | `services/quant/app/ai_auto/attribution.py` | 纳入；实现与回归已核对 |
| F11 | `services/quant/app/ai_auto/cost_runtime.py` | 纳入；实现与回归已核对 |
| F12 | `services/quant/app/ai_auto/experience/events.py` | 纳入；实现与回归已核对 |
| F13 | `services/quant/app/ai_auto/backtest_funding.py` | 纳入；实现与回归已核对 |
| F14 | `services/quant/app/ai_auto/funding_ledger.py` | 纳入；实现与回归已核对 |
| F15 | `services/quant/app/ai_auto/funding.py` | 纳入；实现与回归已核对 |
| F16 | `services/quant/app/ai_auto/cost_model.py` | 纳入；实现与回归已核对 |
| F17 | `services/quant/app/ai_auto/backtest.py` | 纳入；实现与回归已核对 |
| F18 | `services/quant/app/ai_auto/track_record.py` | 纳入；实现与回归已核对 |
| F19 | `services/quant/app/ai_auto/advisory_evidence.py` | 纳入；实现与回归已核对 |
| F20 | `services/quant/app/ai_auto/decision_memory.py` | 纳入；实现与回归已核对 |
| F21 | `services/quant/app/ai_auto/prompts.py` | 纳入；实现与回归已核对 |
| F22 | `services/quant/app/ai_auto/decider.py` | 纳入；实现与回归已核对 |
| F23 | `apps/web/lib/aiTradeProfitability.ts` | 纳入；实现与回归已核对 |
| F24 | `apps/web/lib/aiTradeIntegrity.ts` | 纳入；实现与回归已核对 |
| F25 | `apps/web/components/Agent/aiTradeStats/ProfitabilityCoverage.tsx` | 纳入；实现与回归已核对 |
| F26 | `apps/web/components/Agent/aiTradeStats/ProfitabilityCard.tsx` | 纳入；实现与回归已核对 |
| F27 | `apps/web/components/Agent/aiTradeStats/CalibrationCard.tsx` | 纳入；实现与回归已核对 |
| F28 | `services/quant/tests/test_ai_trade_profitability_dataset.py` | 纳入；实现与回归已核对 |
| F29 | `services/quant/tests/test_ai_trade_profitability.py` | 纳入；实现与回归已核对 |
| F30 | `services/quant/tests/test_ai_trade_profitability_api.py` | 纳入；实现与回归已核对 |
| F31 | `services/quant/tests/test_ai_trade_fee_comparison.py` | 纳入；实现与回归已核对 |
| F32 | `services/quant/tests/test_ai_trade_backtest_funding_causality.py` | 纳入；实现与回归已核对 |
| F33 | `services/quant/tests/test_ai_trade_backtest_funding_cost.py` | 纳入；实现与回归已核对 |
| F34 | `services/quant/tests/test_ai_trade_funding_cache.py` | 纳入；实现与回归已核对 |
| F35 | `services/quant/tests/test_ai_trade_track_record.py` | 纳入；实现与回归已核对 |
| F36 | `services/quant/tests/test_ai_trade_track_record_evidence.py` | 纳入；实现与回归已核对 |
| F37 | `services/quant/tests/test_ai_trade_memory_integrity.py` | 纳入；实现与回归已核对 |
| F38 | `apps/web/lib/aiTradeProfitability.test.ts` | 纳入；实现与回归已核对 |
| F39 | `apps/web/components/Agent/aiTradeStats/ProfitabilityCard.test.tsx` | 纳入；实现与回归已核对 |
| F40 | `apps/web/components/Agent/aiTradeStats/CalibrationCard.test.tsx` | 纳入；实现与回归已核对 |
| F41 | `apps/web/lib/aiTradeIntegrity.test.ts` | 纳入；实现与回归已核对 |
| F42 | `services/quant/app/ai_auto/cost_view.py` | 纳入；实现与回归已核对 |
| F43 | `services/quant/app/ai_auto/cost_text.py` | 纳入；实现与回归已核对 |
| F44 | `services/quant/tests/test_ai_trade_cost_text.py` | 纳入；实现与回归已核对 |
| F45 | `services/quant/app/okx/rest_client.py` | 纳入；实现与回归已核对 |
| F46 | `services/quant/tests/test_ai_trade_funding_rest_contract.py` | 纳入；实现与回归已核对 |
| F47 | `services/quant/app/ai_auto/accounting_replay.py` | 纳入；实现与回归已核对 |
| F48 | `services/quant/tests/test_ai_trade_funding_ledger.py` | 纳入；实现与回归已核对 |
| F49 | `services/quant/tests/test_ai_trade_cost_model.py` | 纳入；实现与回归已核对 |
| F50 | `services/quant/tests/test_ai_trade_trip_ledger.py` | 纳入；实现与回归已核对 |
| F51 | `services/quant/tests/test_ai_trade_economics_integrity.py` | 纳入；实现与回归已核对 |
| F52 | `services/quant/tests/test_ai_trade_journal_prompt_rebuild.py` | 保留工作区；不提交Journal依赖 |
| F53 | `services/quant/tests/test_ai_trade_advisory_snapshot.py` | 纳入；实现与回归已核对 |
| F54 | `services/quant/tests/test_ai_backtest_runner.py` | 纳入；实现与回归已核对 |
| F55 | `services/quant/tests/test_ai_trade_edge_gate.py` | 纳入；实现与回归已核对 |
| F56 | `apps/web/components/Agent/aiTradeStats/IntegrityCard.test.tsx` | 纳入；实现与回归已核对 |
| F57 | `services/quant/tests/test_ai_trade_integrity_api.py` | 纳入；实现与回归已核对 |
| F58 | `services/quant/tests/test_ai_trade_prompts.py` | 纳入；实现与回归已核对 |
