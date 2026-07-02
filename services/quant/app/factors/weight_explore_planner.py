"""因子组合探索的纯函数组合生成器（factor-combo-exploration §2.2）。

据策略从因子池生成去重后的组合列表（确定性排序、纯函数、零 IO）。**不做候选数上限**判定（那是
``weight_explore_service`` 层守门，§2.3 ⑥），只负责生成 / 去重 / 成员数边界。前端 ``lib/weightExplore.ts``
的 ``planCombos``/``estimateComboCount`` 与本模块同口径同去重（C-7 平价，由两侧测试守护，改一侧必同步另一侧）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import combinations

# 单个组合的成员数边界（与 specs.MAX_FACTOR_SET_MEMBERS 同口径，池上限亦为 12）。
MIN_COMBO_MEMBERS = 1
MAX_COMBO_MEMBERS = 12


@dataclass(frozen=True)
class ComboPlan:
    """组合生成结果：``combos`` 每项 = 成员快照列表（pool 的子集）；``labels`` 与之等长的展示名。"""

    combos: list[list[dict]]
    labels: list[str]


def _member_identity(member: dict) -> str:
    """成员唯一身份键 = ``factorKey|规范化params|direction``（镜像前端 ``lib/factor.ts::memberIdentity``、
    后端 ``specs.validate_members`` 口径），供组合去重。"""
    params = member.get("params") or {}
    normed = json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"{member.get('factorKey')}|{normed}|{member.get('direction')}"


def _combo_key(indices: list[int], pool: list[dict]) -> tuple[str, ...]:
    """组合去重键 = 组合内成员 identity 的排序元组（跨策略同一组合只留一次，保先出现者顺序）。"""
    return tuple(sorted(_member_identity(pool[i]) for i in indices))


def _combo_label(indices: list[int], pool: list[dict]) -> str:
    """组合展示名 = 成员 factorKey 以「·」连接（如 ``mom_roc·rev_rsi``）；空组合回落空串（调用方已滤空）。"""
    return "·".join(str(pool[i].get("factorKey", "")) for i in indices)


def _valid_sizes(raw: object, pool_size: int) -> list[int]:
    """规整 ``params.sizes``：取 1..pool_size 的整数、去重、**保输入顺序**；越界 / 非整数一律跳过（不报错）。"""
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    seen: set[int] = set()
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if 1 <= value <= pool_size and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _rank_order(pool_size: int, rank_ic: object) -> list[int]:
    """按 ``params.rankIc`` 降序排 pool 下标（tie 用 pool 序）；缺失 / 长度不匹配 → 回落 pool 顺序（确定性、不报错）。"""
    if not isinstance(rank_ic, list) or len(rank_ic) != pool_size:
        return list(range(pool_size))
    scores: list[float] = []
    for value in rank_ic:
        try:
            scores.append(float(value))
        except (TypeError, ValueError):
            scores.append(float("-inf"))
    return sorted(range(pool_size), key=lambda i: (-scores[i], i))


def _index_combos(pool_size: int, strategy: str, params: dict) -> list[list[int]]:
    """据策略生成组合的 pool 下标列表（未去重、未过滤成员数；空组合由调用方按策略处置）。

    Raises:
        ValueError: 未知策略；manual 下标越界 / 显式空组合。
    """
    if strategy == "sizes":
        return [list(combo) for k in _valid_sizes(params.get("sizes"), pool_size)
                for combo in combinations(range(pool_size), k)]
    if strategy == "topk_prefix":
        order = _rank_order(pool_size, params.get("rankIc"))
        return [order[:k] for k in _valid_sizes(params.get("sizes"), pool_size)]
    if strategy == "leave_one_out":
        out = [list(range(pool_size))]                      # 全池 1 个
        if pool_size >= 2:                                  # |pool|==1 时「去一」得空组合 → 跳过，仅留全池（P2-2）
            out.extend([j for j in range(pool_size) if j != i] for i in range(pool_size))
        return out
    if strategy == "manual":
        return _manual_index_combos(pool_size, params.get("combos"))
    raise ValueError(f"未知探索策略：{strategy}")


def _manual_index_combos(pool_size: int, raw: object) -> list[list[int]]:
    """手动策略：``params.combos`` 为显式下标列表数组。下标越界 / 显式空组合 → ``ValueError``；组合内下标去重保序。"""
    if not isinstance(raw, list) or not raw:
        raise ValueError("手动策略需要非空的 combos 下标数组")
    out: list[list[int]] = []
    for entry in raw:
        if not isinstance(entry, list) or len(entry) == 0:
            raise ValueError("手动组合不可为空")
        idxs: list[int] = []
        seen: set[int] = set()
        for i in entry:
            if isinstance(i, bool) or not isinstance(i, int) or not (0 <= i < pool_size):
                raise ValueError(f"组合下标越界：{i!r}（池大小 {pool_size}）")
            if i not in seen:
                seen.add(i)
                idxs.append(i)
        out.append(idxs)
    return out


def plan_combinations(pool: list[dict], *, strategy: str, params: dict) -> ComboPlan:
    """据策略从因子池生成去重后的组合列表（确定性排序，纯函数、零 IO）。

    strategy:
      - "sizes":        对 params["sizes"] 中每个 k，穷举 pool 的全部 C(|pool|,k) 子集。
      - "topk_prefix":  按 params["rankIc"]（与 pool 等长，缺省回落 pool 序）降序排 pool，取各 k 的前缀嵌套子集。
      - "leave_one_out":全池 1 个 + 逐一去掉单因子的 |pool| 个；|pool|==1 时跳过空组合仅留全池 1 个（P2-2）。
      - "manual":       params["combos"] 为显式的成员下标列表数组（下标越界 / 显式空组合 → ValueError）。

    去重键 = 组合内成员 memberIdentity 排序集合（跨策略同一组合只留一次，保先出现者顺序）。自动策略生成的
    空组合一律**跳过**（不报错）；仅 manual 显式空组合报 ValueError。保留的每个组合须 1..12 成员（越界 → ValueError）。

    Raises:
        HTTPException 由调用方（service）映射；本函数抛 ``ValueError``（非法计划）。
    """
    index_combos = _index_combos(len(pool), strategy, params)
    seen: set[tuple[str, ...]] = set()
    combos: list[list[dict]] = []
    labels: list[str] = []
    for indices in index_combos:
        if not indices:                                     # 自动策略空组合：跳过（manual 空组合已在 _index_combos 报错）
            continue
        if not (MIN_COMBO_MEMBERS <= len(indices) <= MAX_COMBO_MEMBERS):
            raise ValueError(f"组合成员数须在 {MIN_COMBO_MEMBERS}..{MAX_COMBO_MEMBERS} 之间")
        key = _combo_key(indices, pool)
        if key in seen:
            continue
        seen.add(key)
        combos.append([pool[i] for i in indices])
        labels.append(_combo_label(indices, pool))
    return ComboPlan(combos=combos, labels=labels)
