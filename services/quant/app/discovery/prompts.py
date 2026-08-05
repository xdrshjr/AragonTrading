"""发现层 LLM 子代理的中文 prompt 与工具 schema（strategy-factor-discovery §2.11）。

**词表单一真源**：字段 / 算子 / 机制族一律由 ``expression`` / ``expression_text`` /
``mechanisms`` 的表**自动生成**，绝不在 prompt 里手抄一份——手抄的那份迟早与代码漂移，而漂移
之后模型会持续产出解析不了的表达式，表现为「LLM 份额恒空转」。

两个子代理的职责边界写死在 system prompt 里：
- 生成子代理：**只**输出表达式与机制族标签，不要解释性长文。
- 审查子代理：**只有否决权**，不能替代硬编码验证（D-4：达标与否绝不由 LLM 说了算）。
"""
from __future__ import annotations

from ..agent.llm.base import ToolSchema
from .expression import (
    MAX_DEPTH,
    MAX_NODES,
    MIN_NODES,
    OPERATORS,
    WINDOW_LADDER,
)
from .expression_text import field_cn, op_cn, vocabulary
from .mechanisms import MECHANISM_FAMILIES, mechanism_cn

GENERATE_TOOL_NAME = "submit_factor_candidates"
REVIEW_TOOL_NAME = "submit_review_verdicts"

MAX_RECENT_KEPT_IN_PROMPT = 8
MAX_BANNED_IN_PROMPT = 6
MAX_FAILURE_PATTERNS_IN_PROMPT = 6


def _field_table() -> str:
    rows = [f"  {item['key']}（{field_cn(item['key'])}，量纲 {item['dimension']}）"
            for item in vocabulary()["fields"]]
    return "\n".join(rows)


def _operator_table() -> str:
    rows = []
    for key, spec in OPERATORS.items():
        shape = f"{key}(x{',y' if spec.arity == 2 else ''}"
        shape += ",N)" if spec.windowed else ")"
        rows.append(f"  {shape} —— {op_cn(key)}")
    return "\n".join(rows)


def _mechanism_table() -> str:
    rows = []
    for family in MECHANISM_FAMILIES:
        note = f"（注意：{family.proxy_note}）" if family.proxy_note else ""
        rows.append(f"  {family.key}｜{family.cn}：{family.hint}{note}\n"
                    f"    示例：{family.prototypes[0]}")
    return "\n".join(rows)


def build_generate_system_prompt() -> str:
    """生成子代理的 system prompt（词表由代码表自动生成，单一真源）。"""
    return (
        "你是量化因子研究员，在一个 OKX 永续合约的**单标的时序**因子发现系统里工作。\n"
        "你的唯一任务：按给定的机制方向，产出**语法合法**的因子表达式候选。\n\n"
        "【表达式语法】前缀式，参数内联。时序算子的最后一个参数是窗口整数。\n"
        f"可用字段（叶子）：\n{_field_table()}\n\n"
        f"可用算子：\n{_operator_table()}\n\n"
        f"窗口只能取这些值：{', '.join(str(w) for w in WINDOW_LADDER)}\n"
        f"复杂度约束：节点数 {MIN_NODES}–{MAX_NODES}，树深 ≤{MAX_DEPTH}，"
        "同一表达式内不同窗口的种类 ≤4。\n\n"
        "【量纲规则（违反即被系统丢弃）】\n"
        "  price=价格类(close/open/high/low/typical)，volume=成交量，quote=成交额，ratio=无量纲。\n"
        "  add/sub 两侧量纲必须相同；mul 至少一侧为 ratio；div 同量纲得 ratio，"
        "异量纲时分母必须是 ratio；log1p 只接受 ratio。\n"
        "  ts_zscore / ts_rank / ts_skew / roc / ts_corr / sign 恒产出 ratio（归一化出口）。\n\n"
        f"【机制族】\n{_mechanism_table()}\n\n"
        "【市场背景】这是 7×24 连续交易的永续合约，**不存在** A 股意义上的隔夜跳空；"
        "overnight 字段虽然可用，但在本市场先验极弱，不要默认它是强信号源。\n"
        "本系统没有截面样本，**不要**使用任何横截面排名思路，只用时序算子。\n\n"
        "【输出纪律】只调用一次 submit_factor_candidates 并给出候选数组；"
        "**不要**输出解释性长文、不要复述规则、不要给出收益预测。"
    )


def build_review_system_prompt() -> str:
    """审查子代理的 system prompt（只有否决权，绝不替代硬编码验证）。

    ⚠️ 候选可能来自四条轴：``factor`` 是表达式，另三条是**策略**（参数组合 / 因子组合 /
    策略轮动），它们没有表达式、只有中文渲染。不把这件事说清，模型会拿「表达式审查」的尺子去
    量一条策略候选，然后以「看不懂结构」为由否决——而裁决语义是**只减不增**，那等于把整条轴
    误杀在审查层（discovery-strategy-axes §2.7）。
    """
    return (
        "你是量化研究的**经济学含义**审查员。系统已经用确定性规则做过语法、量纲、复杂度与"
        "退化检查，接下来还会用硬编码的统计闸（因子 11 条）或真回测（策略 9 条）做验证。\n\n"
        "候选有两类，**判据不同**：\n"
        "  · 类型 factor：一条价量**表达式**，判断它是否有可解释的经济学含义、边界是否合理。\n"
        "  · 类型 strategy_param / factor_combo / strategy_combo：一条**策略**（模板参数组合 /"
        "因子加权组合 / 策略轮动），它没有表达式，只给中文渲染。判断这套配置在逻辑上是否自相"
        "矛盾（如风控参数互相打架、成员之间完全同质），**不要**因为「没有表达式」而否决。\n\n"
        "你的职责边界（务必严格遵守）：\n"
        "  1. 你**只有否决权**。拿不准就放行（accept=true），把裁决权留给统计验证与回测。\n"
        "  2. 你**不能**替代统计验证。绝不要因为「看起来会赚钱 / 不赚钱」而否决或放行——"
        "达标与否由硬编码的闸决定，不由你决定。\n"
        "  3. 否决的合理理由举例：把不可比的量强行组合、窗口组合自相矛盾、"
        "结构上等价于一个更简单的已知因子、依赖本市场不存在的机制。\n\n"
        "只调用一次 submit_review_verdicts，对每条候选给出 {hash, accept, reason, mechanism}。"
        "reason 用一句中文，20 字以内。"
    )


GENERATE_TOOL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "description": "本轮产出的因子表达式候选",
            "items": {
                "type": "object",
                "properties": {
                    "expr": {"type": "string", "description": "前缀式因子表达式（规范式）"},
                    "mechanism": {"type": "string", "description": "所属机制族 key"},
                    "hypothesis": {"type": "string", "description": "一句话研究假设（≤30 字）"},
                },
                "required": ["expr"],
            },
        }
    },
    "required": ["candidates"],
}

REVIEW_TOOL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "description": "对每条候选的逐条裁决",
            "items": {
                "type": "object",
                "properties": {
                    "hash": {"type": "string", "description": "候选哈希（原样回填）"},
                    "accept": {"type": "boolean", "description": "是否放行（拿不准就 true）"},
                    "reason": {"type": "string", "description": "一句中文理由（≤20 字）"},
                    "mechanism": {"type": "string", "description": "你判断的机制族 key"},
                },
                "required": ["hash", "accept"],
            },
        }
    },
    "required": ["verdicts"],
}


def generate_tool() -> ToolSchema:
    return ToolSchema(name=GENERATE_TOOL_NAME,
                      description="提交本轮生成的因子表达式候选（只调用一次）",
                      input_schema=GENERATE_TOOL_SCHEMA)


def review_tool() -> ToolSchema:
    return ToolSchema(name=REVIEW_TOOL_NAME,
                      description="提交对候选的逐条经济学含义裁决（只调用一次）",
                      input_schema=REVIEW_TOOL_SCHEMA)


def build_generate_prompt(payload: dict) -> str:
    """装配生成 prompt：机制覆盖 + 近期入库因子 + 冻结骨架 + 失败模式 + 本轮要几条。"""
    want = int(payload.get("count", 5) or 5)
    lines = [f"【本轮任务】产出 {want} 条**互不相同**的新因子表达式。"]
    uncovered = list(payload.get("uncoveredFamilies") or [])[:6]
    if uncovered:
        names = "、".join(f"{key}（{mechanism_cn(key)}）" for key in uncovered)
        lines.append(f"【优先方向】以下机制族目前 0 覆盖，请优先从中取材：{names}")
    coverage = payload.get("mechanismCoverage") or {}
    covered = [f"{key}×{count}" for key, count in coverage.items() if int(count or 0) > 0]
    if covered:
        lines.append(f"【已覆盖】{'、'.join(covered[:13])}")
    lines.append(_recent_kept_block(payload.get("recentKept") or []))
    lines.append(_banned_block(payload.get("bannedSkeletons") or []))
    lines.append(_failure_block(payload.get("failurePatterns") or []))
    lines.append("【硬要求】每条 expr 必须严格符合语法、量纲与复杂度约束；"
                 "**不要**产出与「已入库」列表结构雷同的表达式。")
    return "\n".join(part for part in lines if part)


def _recent_kept_block(rows: list) -> str:
    if not rows:
        return "【已入库】暂无（冷启动阶段，请广度优先）。"
    items = []
    for row in rows[:MAX_RECENT_KEPT_IN_PROMPT]:
        items.append(f"  {row.get('expr', '')}  →  {row.get('describe', '')}"
                     f"（{mechanism_cn(str(row.get('mechanism', '')))}）")
    return "【已入库（勿重复其结构）】\n" + "\n".join(items)


def _banned_block(rows: list) -> str:
    if not rows:
        return ""
    items = ", ".join(str(item)[:80] for item in rows[:MAX_BANNED_IN_PROMPT])
    return f"【已冻结骨架（严禁复用该结构）】{items}"


def _failure_block(rows: list) -> str:
    if not rows:
        return ""
    items = ", ".join(f"{str(item)[:60]}" for item in rows[:MAX_FAILURE_PATTERNS_IN_PROMPT])
    return f"【失败模式（这些结构反复不达标，请避开）】{items}"


AXIS_CN: dict[str, str] = {
    "factor": "因子表达式", "factor_combo": "因子组合策略",
    "strategy_param": "常规策略参数", "strategy_combo": "策略轮动组合",
}


def build_review_prompt(payload: dict) -> str:
    """装配审查 prompt：逐条候选的类型 + 表达式（仅因子轴有）+ 中文渲染 + 机制族 + 覆盖摘要。"""
    lines = ["【待审候选】"]
    for row in payload.get("candidates") or []:
        kind = str(row.get("kind") or "factor")
        expr = str(row.get("expr") or "")
        lines.append(f"  hash={row.get('hash', '')}\n"
                     f"    类型：{AXIS_CN.get(kind, kind)}\n"
                     f"    表达式：{expr or '（该类型无表达式，见下方中文解释）'}\n"
                     f"    中文解释：{row.get('describe', '')}\n"
                     f"    系统判定机制族：{mechanism_cn(str(row.get('mechanism', '')))}")
    coverage = payload.get("mechanismCoverage") or {}
    covered = [f"{key}×{count}" for key, count in coverage.items() if int(count or 0) > 0]
    if covered:
        lines.append(f"【本任务已覆盖机制】{'、'.join(covered[:13])}")
    lines.append("请对上面**每一条**给出裁决。拿不准一律 accept=true。")
    return "\n".join(lines)
