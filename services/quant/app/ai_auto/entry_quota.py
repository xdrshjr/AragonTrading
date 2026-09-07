"""逆势委托次数的有界状态机，持久化由所属成员统一事务负责。"""
from dataclasses import dataclass, field
from copy import deepcopy


@dataclass
class EntryQuota:
    """预留不会消费额度，只有交易所确认接受才消费。"""

    run_id: str
    max_per_session: int
    consumed_attempts: dict[str, str] = field(default_factory=dict)
    reservations: dict[str, str] = field(default_factory=dict)
    legacy_assumed_used: int = 0
    coverage: str = "complete"
    schema_version: int = 2

    def __post_init__(self):
        self.max_per_session = min(50, max(0, int(self.max_per_session)))

    @property
    def used(self) -> int:
        """确认消费数及单列的历史保守占用。"""
        return len(self.consumed_attempts) + self.legacy_assumed_used

    @property
    def reserved(self) -> int:
        """尚待交易所回执核实的次数。"""
        return len(self.reservations)

    @property
    def remaining(self) -> int:
        """扣除已用与预留后的可用次数。"""
        return max(0, self.max_per_session - self.used - self.reserved)

    def reserve(self, attempt_id: str, cl_ord_id: str) -> bool:
        """绑定固定订单身份；相同回调幂等，不复用不同身份。"""
        known = self.reservations | self.consumed_attempts
        if attempt_id in known:
            return known[attempt_id] == cl_ord_id
        if not attempt_id or not cl_ord_id or self.remaining <= 0:
            return False
        if cl_ord_id in known.values():
            return False
        self.reservations[attempt_id] = cl_ord_id
        return True

    def consume(self, attempt_id: str) -> bool:
        """接收后消费一次，撤单和到期不退款。"""
        if attempt_id in self.consumed_attempts:
            return True
        identity = self.reservations.pop(attempt_id, None)
        if identity is None:
            return False
        self.consumed_attempts[attempt_id] = identity
        return True

    def release(self, attempt_id: str) -> bool:
        """调用方仅在明确拒绝或本进程可证未发送时释放。"""
        return self.reservations.pop(attempt_id, None) is not None

    def to_dict(self) -> dict:
        """完整恢复快照，不包含账户密钥。"""
        if hasattr(self, "_future_snapshot"):
            return deepcopy(self._future_snapshot)
        return deepcopy(getattr(self, "_unknown_fields", {})) | {
            "schemaVersion": 2, "runId": self.run_id,
            "maxPerSession": self.max_per_session,
            "consumedAttempts": dict(self.consumed_attempts),
            "reservations": dict(self.reservations),
            "legacyAssumedUsed": self.legacy_assumed_used,
            "coverage": self.coverage,
        }

    def summary(self) -> dict:
        """面向模型和 UI 的计数，不暴露全部历史身份。"""
        return {
            "maxPerSession": self.max_per_session, "used": self.used,
            "reserved": self.reserved, "remaining": self.remaining,
            "legacyAssumedUsed": self.legacy_assumed_used,
            "coverage": self.coverage,
        }

    @classmethod
    def restore(cls, snapshot: dict | None, *, run_id: str,
                max_per_session: int, legacy_used: int = 0):
        """旧或未知契约耗尽剩余额度；同一 run 不重新发放。"""
        quota = cls(run_id=run_id, max_per_session=max_per_session)
        if (not isinstance(snapshot, dict)
                or snapshot.get("schemaVersion") != 2
                or str(snapshot.get("runId")) != str(run_id)):
            if (isinstance(snapshot, dict)
                    and snapshot.get("schemaVersion") != 2):
                quota._future_snapshot = deepcopy(snapshot)
            return quota._mark_unknown()
        known_keys = set(quota.to_dict())
        quota._unknown_fields = {key: deepcopy(value)
                                 for key, value in snapshot.items()
                                 if key not in known_keys}
        try:
            capacity = int(snapshot["maxPerSession"])
            if not 0 <= capacity <= 50:
                raise ValueError("额度上限非法")
            quota.max_per_session = capacity
            quota._restore_maps(snapshot)
        except (KeyError, TypeError, ValueError):
            return quota._mark_unknown()
        return quota

    def _restore_maps(self, snapshot: dict) -> None:
        consumed, reserved = (snapshot["consumedAttempts"],
                              snapshot["reservations"])
        if not isinstance(consumed, dict) or not isinstance(reserved, dict):
            raise ValueError("额度身份映射非法")
        if consumed.keys() & reserved.keys():
            raise ValueError("同一额度不可同时已用和预留")
        combined = consumed | reserved
        if any(not isinstance(key, str) or not key
               or not isinstance(value, str) or not value
               for key, value in combined.items()):
            raise ValueError("额度身份为空")
        if len(set(combined.values())) != len(combined):
            raise ValueError("订单身份复用")
        self.consumed_attempts, self.reservations = (
            dict(consumed), dict(reserved))
        self.legacy_assumed_used = int(snapshot.get("legacyAssumedUsed", 0))
        if (self.legacy_assumed_used < 0
                or self.used + self.reserved > self.max_per_session):
            raise ValueError("额度快照超限")
        self.coverage = str(snapshot.get("coverage", "partial"))

    def _mark_unknown(self):
        self.consumed_attempts.clear()
        self.reservations.clear()
        self.legacy_assumed_used = self.max_per_session
        self.coverage = "legacy_unknown"
        return self
