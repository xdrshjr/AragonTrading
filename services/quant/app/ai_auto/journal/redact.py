"""导出前脱敏的**纯函数**（ai-trade-audit-journal §2.2.5）。

脱敏在**写入时**做，不是导出时才做。导出时才做 ⇒ 库里躺着明文 ⇒ 一次误操作的
``sqlite3 .dump`` 就泄了，而导出文件是会离开这台机器的。

事实核查：现有 ``_build_decision_payload`` 链路里**本就不含任何凭证**——账户密钥只存在于
``okx_accounts`` 表与 ``OKXPrivateClient`` 内部。本模块因此是**纵深防御**，不是补一个已知漏洞。

三条纪律：

1. **绝不改变形状**。键一律保留（只替换值），否则读侧会把「脱敏了」误读成「本来就没有」。
2. **绝不抛**。本模块跑在决策轮的热路径上（写点入队前），一次异常会把一轮已经成交的决策翻成
   ``AI_TRADE_ROUND_FAILED``。故这里连 ``raise`` 都不许出现（由门禁 G-5 用 AST 冻结）。
3. **深广度上限**。病态嵌套（自引用 / 几十万节点）会把写入协程卡在一次递归里，而它跑在
   决策协程内。超限即截断并置 ``_redactTruncated``——**说出来**，不静默。
"""
from __future__ import annotations

import re

REDACTED = "[REDACTED]"

# 键名黑名单（归一后比较：大小写、下划线、连字符、空格全部剥掉）。
_KEY_DENY = frozenset((
    "apikey", "secretkey", "secret", "passphrase", "password", "token",
    "authorization", "cookie", "privatekey", "credential", "credentials",
    "accesstoken", "refreshtoken", "apisecret",
))

# 值形态黑名单：整串替换（不做部分遮蔽——半个凭证仍然是凭证的一半）。
_VALUE_PATTERNS = (
    re.compile(r"Bearer\s+\S+"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
)

MAX_DEPTH = 12
MAX_NODES = 20000
# 截断标记键：出现即表示这棵树没有被完整遍历（超深或超广），读侧据此说明「此处已截断」。
TRUNCATED_KEY = "_redactTruncated"


def normalize_key(key: str) -> str:
    """键名归一：大小写与下划线 / 连字符 / 空格无关。

    Args:
        key: 原始键名。

    Returns:
        小写、去掉 ``_-`` 与空白的形态，供黑名单比较。
    """
    return re.sub(r"[\s_\-]", "", str(key)).lower()


def _redact_value(value: str) -> str:
    """值形态黑名单：命中任一模式即整串替换。"""
    for pattern in _VALUE_PATTERNS:
        if pattern.search(value):
            return REDACTED
    return value


class _Budget:
    """遍历预算（节点数）。用可变对象承载，避免在递归里回传计数。"""

    __slots__ = ("nodes", "truncated")

    def __init__(self) -> None:
        self.nodes = 0
        self.truncated = False

    def spend(self) -> bool:
        """消费一个节点名额；返回 ``False`` 表示预算已耗尽。"""
        self.nodes += 1
        if self.nodes > MAX_NODES:
            self.truncated = True
            return False
        return True


def _walk(obj, depth: int, budget: _Budget):
    if depth > MAX_DEPTH:
        budget.truncated = True
        return REDACTED
    if isinstance(obj, dict):
        out: dict = {}
        for key, value in obj.items():
            if not budget.spend():
                return out
            if normalize_key(key) in _KEY_DENY:
                out[key] = REDACTED          # 键仍在：形状不变是本模块的第一条纪律
                continue
            out[key] = _walk(value, depth + 1, budget)
        return out
    if isinstance(obj, (list, tuple)):
        out_list: list = []
        for item in obj:
            if not budget.spend():
                return out_list
            out_list.append(_walk(item, depth + 1, budget))
        return out_list
    if isinstance(obj, str):
        return _redact_value(obj)
    return obj


def redact(obj):
    """递归脱敏（键名黑名单 + 值形态黑名单 + 深广度上限）。**形状不变、绝不抛**。

    Args:
        obj: 任意可 JSON 化的结构（dict / list / 标量）。

    Returns:
        同构的新结构；命中黑名单的值被替换为 ``[REDACTED]``。若因深度或节点数超限而截断，
        顶层为 dict 时追加 ``_redactTruncated: True``（顶层不是 dict 时无处可挂，
        由读侧的体量本身说明——那种输入在本仓库不存在）。
    """
    budget = _Budget()
    result = _walk(obj, 0, budget)
    if budget.truncated and isinstance(result, dict):
        result[TRUNCATED_KEY] = True
    return result
