"""基因体与 ``Searcher`` 协议（strategy-factor-discovery §2.4）。

**范围（D-6 / 评审条件 1）**：v1 只交付 ``factor`` 单轴闭环，故本模块只落地
``ExprGenome``；``factor_combo`` / ``strategy_param`` / ``strategy_combo`` 三轴是 v1.1+ 的加法
增量。它们仍列在 ``ALL_AXES`` 里——**只为了让 API 能给出「该轴尚未实现」的准确 400**，
绝不出现在 ``IMPLEMENTED_AXES`` 里，UI 也不渲染它们（D-6 强制推论：严禁「能选、能启动、
静默不产候选」的空转轴）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .expression import Node, expr_hash, skeleton, to_text
from .expression_text import describe_text
from .mechanisms import classify_mechanism

# 四条搜索轴（顺序即预算分配优先级：上游轴先跑，下游轴从上游库存取材，§2.4）。
ALL_AXES: tuple[str, ...] = ("factor", "factor_combo", "strategy_param", "strategy_combo")
# 本轮真正实现的轴（D-6）。``GET /discovery/config/defaults`` 回它，启动器只渲染它，
# ``POST /discovery/runs`` 收到其它轴一律 400 ``DISCOVERY_AXIS_UNSUPPORTED``。
IMPLEMENTED_AXES: tuple[str, ...] = ("factor",)

# 候选来源（五维演化 + 冷启动种子）。
SOURCES: tuple[str, ...] = ("seed", "random", "mutate", "crossover", "perturb", "llm")


@dataclass(frozen=True)
class ExprGenome:
    """因子轴基因：一棵表达式树（不可变，可哈希）。"""

    node: Node

    @property
    def text(self) -> str:
        return to_text(self.node)


@dataclass(frozen=True)
class Candidate:
    """一个待审查 / 待验证的候选（生成层产物，落库前的内存形态）。"""

    kind: str
    genome: ExprGenome
    source: str
    genome_hash: str
    expr_text: str
    skeleton: str
    mechanism: str
    describe: str


def make_factor_candidate(node: Node, source: str) -> Candidate:
    """由表达式树装配因子轴候选（哈希 / 骨架 / 机制 / 中文渲染在此一次性算齐）。

    这些派生字段全程只在此处计算一次：候选表、FSA、去重、prompt、UI 读的都是同一份，
    杜绝「同一棵树在不同地方算出不同骨架」。
    """
    return Candidate(
        kind="factor", genome=ExprGenome(node), source=source,
        genome_hash=expr_hash(node), expr_text=to_text(node), skeleton=skeleton(node),
        mechanism=classify_mechanism(node), describe=describe_text(node))


@runtime_checkable
class Searcher(Protocol):
    """搜索器协议：五维演化的每种操作各一个方法（§2.4）。

    实现方必须保证产物**合法**（过 ``validate_node`` 的复杂度与量纲护栏）或返回 ``None``，
    绝不把非法基因丢给下游——审查层的规则是第二道防线，不是第一道。
    """

    kind: str

    def seed(self, rng, ctx) -> ExprGenome | None:
        """冷启动 / 随机探索：不受既有假设约束地生成一个新基因。"""

    def mutate(self, rng, parent: ExprGenome, ctx) -> ExprGenome | None:
        """变异：替换表达式树中的叶子或子树，保留结构骨架。"""

    def crossover(self, rng, a: ExprGenome, b: ExprGenome, ctx) -> ExprGenome | None:
        """交叉：交换两个父本的子树分支，产生混合结构。"""

    def perturb(self, rng, parent: ExprGenome, ctx, momentum) -> ExprGenome | None:
        """参数扰动：结构不变，只沿动量方向在窗口梯子上走 1–4 格。"""

    def from_llm(self, payload: dict, ctx) -> ExprGenome | None:
        """解析 LLM 产出为基因；非法一律返回 ``None``（绝不放宽护栏迁就模型）。"""
