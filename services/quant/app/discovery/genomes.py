"""基因体与 ``Searcher`` 协议（strategy-factor-discovery §2.4 / discovery-strategy-axes §2.3）。

四条轴的基因体全部在此定义，派生字段（哈希 / 骨架 / 机制 / 中文渲染）一律**只在本模块算一次**：
候选表、FSA、去重、prompt、UI 读的都是同一份，杜绝「同一个基因在不同地方算出不同骨架」。

**基因哈希必须先规范化再哈希**：不规范化时 ``0.1+0.2`` 与 ``0.30000000000000004`` 会算出两个
哈希，同一个基因被重复测无数次而去重集合永远命中不了——表现为「搜索效率很低」，不报错。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .expression import Node, expr_hash, parse_expression, skeleton, to_text
from .expression_text import describe_text
from .mechanisms import classify_mechanism

# 四条搜索轴（顺序即预算让渡的接收优先级，§2.8）。
ALL_AXES: tuple[str, ...] = ("factor", "factor_combo", "strategy_param", "strategy_combo")
# 本轮（D-7）交付三条策略轴，四轴全部实现：``IMPLEMENTED_AXES == ALL_AXES``。
IMPLEMENTED_AXES: tuple[str, ...] = ALL_AXES
STRATEGY_AXES: tuple[str, ...] = ("factor_combo", "strategy_param", "strategy_combo")

# 候选来源（五维演化 + 冷启动种子）。
SOURCES: tuple[str, ...] = ("seed", "random", "mutate", "crossover", "perturb", "llm")

GENOME_HASH_LEN = 32
FLOAT_DIGITS = 6


@dataclass(frozen=True)
class ExprGenome:
    """因子轴基因：一棵表达式树（不可变，可哈希）。"""

    node: Node

    @property
    def text(self) -> str:
        return to_text(self.node)


@dataclass(frozen=True)
class ParamGenome:
    """常规策略轴：注册表模板 key + **完整**参数（已与默认值合并并按 schema 夹取）。"""

    template_key: str
    params: dict

    def payload(self) -> dict:
        return {"templateKey": self.template_key, "params": dict(self.params)}


@dataclass(frozen=True)
class ComboMemberRef:
    """因子组合的一个成员（方向一律**冻结继承**，绝不写 ``auto``）。"""

    factor_key: str
    params: dict
    direction: str
    name: str

    def payload(self) -> dict:
        return {"factorKey": self.factor_key, "params": dict(self.params),
                "direction": self.direction, "name": self.name}


@dataclass(frozen=True)
class ComboGenome:
    """因子组合轴：2–5 个成员 + 权重（严格归一）+ 三个阈值。"""

    members: tuple
    weights: tuple
    thresholds: dict

    def payload(self) -> dict:
        return {"members": [member.payload() for member in self.members],
                "weights": [round(float(w), FLOAT_DIGITS) for w in self.weights],
                "thresholds": dict(self.thresholds)}

    def baked_members(self) -> list[dict]:
        """**BL-12（评审 P0-3）**：权重逐成员烘焙进 ``weight`` 键，再交给边界校验器。

        ``factors/specs.py::validate_members`` 对缺 ``weight`` 的成员填 **0.0** 且不报错，
        而 ``FactorScoreStrategy`` 在无顶层 ``weights`` 时走的正是成员烘焙路径——两件事叠起来
        就是「复合得分恒 0 ⇒ 零成交 ⇒ 全轴卡 S1」，看起来像市场没有 alpha。
        """
        return [{**member.payload(), "weight": round(float(weight), FLOAT_DIGITS)}
                for member, weight in zip(self.members, self.weights)]


@dataclass(frozen=True)
class RotationGenome:
    """策略组合轴：2–4 个成员（模板 key + 参数）+ 轮动策略参数。"""

    members: tuple
    policy: dict

    def payload(self) -> dict:
        return {"members": [member.payload() for member in self.members],
                "policy": dict(self.policy)}

    def member_specs(self) -> list[dict]:
        """转成 ``combo_specs.build_members`` 吃的形态（同模板可重复，展示 key 由它生成）。"""
        return [{"templateKey": member.template_key, "params": dict(member.params)}
                for member in self.members]


@dataclass(frozen=True)
class Candidate:
    """一个待审查 / 待验证的候选（生成层产物，落库前的内存形态）。"""

    kind: str
    genome: object
    source: str
    genome_hash: str
    expr_text: str
    skeleton: str
    mechanism: str
    describe: str


# ============================ 规范化哈希 ============================
def _rounded(value):
    if isinstance(value, float):
        return round(value, FLOAT_DIGITS)
    if isinstance(value, dict):
        return {str(key): _rounded(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rounded(item) for item in value]
    return value


def canonical_json(payload) -> str:
    """规范式 JSON（键排序 + 无空格 + 浮点先 round(6)）——哈希与幂等键的唯一口径。"""
    return json.dumps(_rounded(payload), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def genome_hash(payload) -> str:
    """基因哈希（去重的物理键）= ``sha256(canonical_json)[:32]``。"""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:GENOME_HASH_LEN]


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


# ============================ 骨架（FSA / variantCap 键，§2.6.4） ============================
def member_skeleton_key(member: ComboMemberRef) -> str:
    """**BL-10（评审 P0-1）**：``expr`` 成员取**表达式骨架**，库内因子取 ``factorKey``。

    直接用 ``factorKey`` 拼接会退化成一个常量——所有挖掘因子的 ``factor_key`` 都是字面量
    ``"expr"``。后果两条且都不报错：``variant_cap``(6) 从第 7 个入库候选起全量拒死该轴；
    FSA 再把这唯一骨架永久冻结。表现是「这条轴挖了两天一个都没产出」。
    """
    if member.factor_key != "expr":
        return member.factor_key
    return f"expr#{skeleton(parse_expression(str(member.params.get('expr') or '')))}"


def combo_skeleton(members: tuple) -> str:
    return _short_hash("|".join(sorted(member_skeleton_key(m) for m in members)))


def rotation_skeleton(members: tuple) -> str:
    return _short_hash("|".join(sorted(member.template_key for member in members)))


# ============================ 候选装配 ============================
def make_factor_candidate(node: Node, source: str) -> Candidate:
    """由表达式树装配因子轴候选（哈希 / 骨架 / 机制 / 中文渲染在此一次性算齐）。"""
    return Candidate(
        kind="factor", genome=ExprGenome(node), source=source,
        genome_hash=expr_hash(node), expr_text=to_text(node), skeleton=skeleton(node),
        mechanism=classify_mechanism(node), describe=describe_text(node))


def make_param_candidate(genome: ParamGenome, source: str, *, family: str) -> Candidate:
    from .describe_strategy import describe_param_genome

    return Candidate(
        kind="strategy_param", genome=genome, source=source,
        genome_hash=genome_hash(genome.payload()), expr_text="",
        skeleton=genome.template_key, mechanism=family,
        describe=describe_param_genome(genome))


def make_combo_candidate(genome: ComboGenome, source: str) -> Candidate:
    from .describe_strategy import describe_combo_genome

    return Candidate(
        kind="factor_combo", genome=genome, source=source,
        genome_hash=genome_hash(genome.payload()), expr_text="",
        skeleton=combo_skeleton(genome.members), mechanism="factor_combo",
        describe=describe_combo_genome(genome))


def make_rotation_candidate(genome: RotationGenome, source: str) -> Candidate:
    from .describe_strategy import describe_rotation_genome

    return Candidate(
        kind="strategy_combo", genome=genome, source=source,
        genome_hash=genome_hash(genome.payload()), expr_text="",
        skeleton=rotation_skeleton(genome.members), mechanism="rotation",
        describe=describe_rotation_genome(genome))


# ============================ 反序列化（续跑重建用） ============================
def param_genome_from_payload(payload: dict) -> ParamGenome:
    return ParamGenome(template_key=str(payload.get("templateKey") or ""),
                       params=dict(payload.get("params") or {}))


def combo_genome_from_payload(payload: dict) -> ComboGenome:
    members = tuple(ComboMemberRef(
        factor_key=str(item.get("factorKey") or ""), params=dict(item.get("params") or {}),
        direction=str(item.get("direction") or "long"), name=str(item.get("name") or ""))
        for item in (payload.get("members") or []))
    return ComboGenome(members=members,
                       weights=tuple(float(w) for w in (payload.get("weights") or [])),
                       thresholds=dict(payload.get("thresholds") or {}))


def rotation_genome_from_payload(payload: dict) -> RotationGenome:
    members = tuple(param_genome_from_payload(item)
                    for item in (payload.get("members") or []))
    return RotationGenome(members=members, policy=dict(payload.get("policy") or {}))


GENOME_LOADERS = {"strategy_param": param_genome_from_payload,
                  "factor_combo": combo_genome_from_payload,
                  "strategy_combo": rotation_genome_from_payload}


@runtime_checkable
class Searcher(Protocol):
    """搜索器协议：五维演化的每种操作各一个方法（§2.4）。

    实现方必须保证产物**合法**（过各轴的护栏）或返回 ``None``，绝不把非法基因丢给下游——
    审查层的规则是第二道防线，不是第一道。
    """

    kind: str

    def seed(self, rng, ctx):
        """冷启动 / 随机探索：不受既有假设约束地生成一个新基因。"""

    def mutate(self, rng, parent, ctx):
        """变异：替换基因中的一个片段，保留结构骨架。"""

    def crossover(self, rng, a, b, ctx):
        """交叉：交换两个父本的片段，产生混合结构。"""

    def perturb(self, rng, parent, ctx, momentum):
        """参数扰动：结构不变，只沿动量方向在梯子上走 1–4 格。"""

    def from_llm(self, payload: dict, ctx):
        """解析 LLM 产出为基因；非法一律返回 ``None``（绝不放宽护栏迁就模型）。"""
