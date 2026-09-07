"""中文证据标签 → 英文术语对照表（ai-trade-deep-reasoning-chain 轴 A-4）。

本特性把**指令层**切成英文，而 ~150KB 的**证据渲染层**（`market_text` / `wake_text` /
`cost_text` / `sizing_text` / `profit_guard_text` / `hold_horizon_text` / `acceptance_text` /
`discipline_view` 与 `prompts._format_*`）保持中文（spec §1.2 非目标）。语言混排的风险由
两件事兜住：这份对照表，以及协议阶段 S9「用英文复述每一条已装配铁律及其阈值」。

**这个模块存在的唯一理由是可审计性**，因此它带一条会真的判红的漂移门禁（用例 G-2′）：
`scan_chinese_labels` 从上述模块的**字符串字面量**里抽出「【…】」段标题与 `=` 前的中文标签，
断言它们全部出现在 `GLOSSARY` 左列。

> **判红之后的唯一正确动作是把新标签登记进 `GLOSSARY` 左列。**
> 放宽正则、加白名单、删用例三种做法一律视为缺陷。这条用例会因为**别的特性**新加一个中文
> 证据标签而判红——那正是它存在的理由（本仓库前科：`.env.example` 无扫描用例，漏登记是
> 静默的）。本用例的 owner 是 ai-trade-deep-reasoning-chain。
"""
from __future__ import annotations

import ast
import re

# 被漂移门禁扫描的模块（相对 `app/ai_auto/`）。新增证据渲染模块时**必须**加进这张表。
SCANNED_MODULES: tuple[str, ...] = (
    "prompts.py", "market_text.py", "wake_text.py", "cost_text.py", "sizing_text.py",
    "profit_guard_text.py", "hold_horizon_text.py", "acceptance_text.py", "discipline_view.py",
)

# 段标题：`【体制】` 这类中文方括号标题（含 `·` 分隔的复合标题）。
_SECTION_RE = re.compile(r"【([一-鿿][^】\n]{0,15})】")
# 数据标签：`方向=long` 这类 `=` 前的中文短词（证据行的键名）。
_LABEL_RE = re.compile(r"([一-鿿][一-鿿]{0,7})[=＝]")

# 中文标签 → 英文术语。左列是**唯一真源**，漂移门禁只认它。
GLOSSARY: tuple[tuple[str, str], ...] = (
    # —— 段标题 ——
    ("基础策略", "base strategy hard rule"),
    ("利润保护 · 当前不可用", "profit protection - currently unavailable"),
    ("仓位与风险层 · 系统强制执行", "position & risk layer - system enforced"),
    ("仓位约束", "position constraint"),
    ("交易成本", "trading cost"),
    ("交易成本 · 事前估算", "trading cost - pre-trade estimate"),
    ("交易成本 · 系统确定性扣减", "trading cost - deterministic deduction"),
    ("交易周期 · 换手纪律", "holding horizon - turnover discipline"),
    ("此前执行摘要（你的长期记忆）", "prior execution summary (your long-term memory)"),
    ("本轮程序事实 · 优先于历史叙述", "current program facts - override historical narrative"),
    # —— 持仓 / 盈亏 ——
    ("当前持仓", "current position"),
    ("当前自管持仓", "current self-managed position"),
    ("方向", "side"),
    ("均价", "average entry price"),
    ("张数", "contracts"),
    ("仓位占比", "size fraction"),
    ("浮盈", "unrealised P&L"),
    ("会话累计盈亏", "session P&L"),
    ("预算", "budget"),
    ("会话止盈阈值", "session take-profit threshold"),
    ("止损阈值", "session stop-loss threshold"),
    ("止盈", "take-profit"),
    ("止损", "stop-loss"),
    # —— 行情 / 因子 ——
    ("最新价", "last price"),
    ("趋势", "trend"),
    ("趋势结构", "trend structure"),
    ("均线", "moving average"),
    ("均线通道", "moving-average band"),
    ("上轨", "upper band"),
    ("中轨", "mid band"),
    ("下轨", "lower band"),
    ("区间位置", "position inside range"),
    ("候选支撑", "candidate support"),
    ("候选阻力", "candidate resistance"),
    ("量比", "volume ratio"),
    ("放缩趋势", "volume expansion/contraction trend"),
    ("波动", "volatility"),
    ("柱", "bar"),
    ("连阴", "consecutive down bars"),
    ("整体", "overall"),
    ("事件", "event"),
    ("体制", "market regime"),
    ("体制桶", "regime bucket"),
    ("一致度", "alignment score"),
    ("缺失周期", "missing timeframe"),
    # —— 信号 / 共识 / 先验 ——
    ("本轮信号", "signal of this round"),
    ("立场", "stance"),
    ("净立场", "net stance"),
    ("同时成立", "simultaneously true"),
    ("共识", "consensus"),
    ("先验", "prior"),
    ("样本", "sample size"),
    ("置信度", "confidence"),
    ("理由", "reason"),
    ("战术周期", "tactical frame"),
    # —— 唤醒 / 纪律 ——
    ("唤醒原因", "wake reason"),
    ("触发源", "wake trigger"),
    ("利润保护", "profit protection"),
    ("保本线", "breakeven line"),
    ("移动止盈", "trailing take-profit"),
    ("换手迟滞", "turnover hysteresis"),
    ("最短持仓", "minimum hold"),
    ("离场连续确认", "exit confirmation streak"),
)

# 渲染进系统 prompt 的紧凑块（每行 4 组，`|` 分隔），只在英文档下注入。
_GLOSSARY_COLUMNS = 4


def _glossary_lines() -> list[str]:
    pairs = [f"{zh}={en}" for zh, en in GLOSSARY]
    return [" | ".join(pairs[i:i + _GLOSSARY_COLUMNS])
            for i in range(0, len(pairs), _GLOSSARY_COLUMNS)]


GLOSSARY_BLOCK = ("# GLOSSARY (labels you will see in the evidence blocks)\n"
                  + "\n".join(_glossary_lines()))

GLOSSARY_KEYS = frozenset(zh for zh, _ in GLOSSARY)


def scan_chinese_labels(source: str) -> set[str]:
    """从一份 Python 源码的**字符串字面量**里抽出中文段标题与数据标签（漂移门禁用）。

    只扫字面量、不扫注释：注释里的中文是给人读的散文，把它们纳入门禁只会逼着下一个人去
    放宽正则——而放宽正则正是这条门禁最怕的失效方式。

    Args:
        source: 待扫描模块的源码文本。

    Returns:
        命中的中文标签集合（段标题 + `=` 前短词）。

    Raises:
        SyntaxError: 源码无法解析（调用方应视为门禁失败，而不是跳过该文件）。
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.update(_SECTION_RE.findall(node.value))
            found.update(_LABEL_RE.findall(node.value))
    return found
