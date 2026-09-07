"""开仓发送事实；已确认结果不被后续审计或富化失败覆盖。"""
from dataclasses import dataclass
import time

from ..okx.order_builder import SubmissionTrace


@dataclass(frozen=True)
class OpenAttemptReceipt:
    """一次开仓尝试的不可变事实，时间单位为 UTC 秒。"""

    attempt_id: str
    status: str
    reason: str
    cl_ord_id: str | None
    ord_id: str | None
    inst_id: str
    submitted_new: bool
    submission_phase: str
    observed_at_ts: float

    def to_dict(self) -> dict:
        """返回公开响应使用的驼峰字段。"""
        return {
            "attemptId": self.attempt_id, "status": self.status,
            "reason": self.reason, "clOrdId": self.cl_ord_id,
            "ordId": self.ord_id, "instId": self.inst_id,
            "submittedNew": self.submitted_new,
            "submissionPhase": self.submission_phase,
            "observedAtTs": self.observed_at_ts,
        }


def receipt_from_trace(attempt_id: str, inst_id: str,
                       cl_ord_id: str | None, trace: SubmissionTrace,
                       reason: str = "pre_submit_error") -> OpenAttemptReceipt:
    """确认 ack 优先；可能发送却没有 ack 的结果始终待核实。"""
    if trace.ack == "accepted":
        status, reason = "accepted", "accepted"
    elif trace.ack == "rejected":
        status, reason = "rejected", "exchange_rejected"
    elif trace.phase == "sent":
        status, reason = "unknown", "acceptance_unknown"
    else:
        status = "not_submitted"
    return OpenAttemptReceipt(
        attempt_id, status, reason, cl_ord_id, trace.ord_id, inst_id,
        status == "accepted", trace.phase, time.time())
