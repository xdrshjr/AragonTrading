"""`ai_auto/flip_detector.py` 防抖状态机单测（ai-trade-wake-scheduler-closed-loop §6.1 T-12~T-14）。

覆盖：双条件（时长 ∧ 有效样本数）缺一不可、`row_key` 未变不计采样（P0-2）、signature 变更双重置、
锁存与新根解锁。纯状态、无时钟、无 IO。
"""
from __future__ import annotations

from app.ai_auto.flip_detector import CONFIRMED, IDLE, PENDING, FlipDetector

_T0 = 1_785_600_000.0
_SIG = "flat>long|7:flat>long"
_OTHER = "flat>short|9:flat>short"


def _row(step: int) -> tuple:
    """一条**确实变过**的行情行（每次调用给不同的 c/h/l）。"""
    return (1_785_600_000, 30_000.0 + step, 30_010.0 + step, 29_990.0 + step)


def _detector(*, hold_s: float = 45.0, min_samples: int = 3) -> FlipDetector:
    return FlipDetector(hold_s=hold_s, min_samples=min_samples)


# ---------- T-12：双条件缺一不可 ----------
def test_duration_alone_is_not_enough():
    """时长够但有效样本不足 ⇒ 仍 pending（只看时长，一次 15 秒间隔的采样就能「保持 45 秒」）。"""
    detector = _detector(min_samples=5)
    for step in range(3):                     # 3 次采样，跨 90 秒
        state = detector.observe(_SIG, _T0 + step * 45.0, row_key=_row(step))
    assert detector.held_s >= 45.0 and detector.samples == 3
    assert state == PENDING


def test_samples_alone_are_not_enough():
    """样本够但时长不足 ⇒ 仍 pending（只看次数，高频 tick 会让它瞬间达标）。"""
    detector = _detector()
    for step in range(6):                     # 6 次采样，只跨 5 秒
        state = detector.observe(_SIG, _T0 + step, row_key=_row(step))
    assert detector.samples == 6 and detector.held_s < 45.0
    assert state == PENDING


def test_both_conditions_met_confirms():
    """两者都够 ⇒ confirmed（复算 §2.8 时间线 t+820…t+865 那四步）。"""
    detector = _detector()
    assert detector.observe(_SIG, _T0 + 0.0, row_key=_row(0)) == PENDING     # samples=1
    assert detector.observe(_SIG, _T0 + 15.0, row_key=_row(1)) == PENDING    # samples=2
    assert detector.observe(_SIG, _T0 + 30.0, row_key=_row(2)) == PENDING    # 30s < 45s
    assert detector.observe(_SIG, _T0 + 45.0, row_key=_row(3)) == CONFIRMED


def test_no_flip_resets_and_reports_idle():
    """`signature is None`（本次预演无翻转）⇒ 重置计时与样本、返回 idle。"""
    detector = _detector()
    detector.observe(_SIG, _T0, row_key=_row(0))
    assert detector.observe(None, _T0 + 5.0, row_key=_row(1)) == IDLE
    assert detector.samples == 0 and detector.held_s == 0.0


def test_signature_change_resets_both_timer_and_samples():
    """signature 中途变更 ⇒ 计时与样本**双重置**（repaint 换了方向就得从头数）。"""
    detector = _detector()
    for step in range(3):
        detector.observe(_SIG, _T0 + step * 20.0, row_key=_row(step))
    assert detector.samples == 3
    assert detector.observe(_OTHER, _T0 + 60.0, row_key=_row(9)) == PENDING
    assert detector.samples == 1 and detector.held_s == 0.0


# ---------- T-12b（P0-2）：行情没动过的采样不算证据 ----------
def test_repeated_identical_row_key_never_accumulates_evidence():
    """同 signature + 同 row_key 连喂 10 次、跨越 hold_s ⇒ 仍 pending。

    没有这条判据，一个**静默的 WS** 就能让「该翻转已连续保持 45 秒」这句话在零新数据的情况下成立
    ——而那正是本轴要送进 prompt 的核心论据。
    """
    detector = _detector()
    frozen = _row(0)
    for step in range(10):                    # 跨 90 秒，但行情一秒都没动过
        state = detector.observe(_SIG, _T0 + step * 10.0, row_key=frozen)
    assert state == PENDING
    assert detector.samples == 1 and detector.held_s == 0.0


def test_row_key_gate_does_not_block_real_movement():
    """行情真的变了就照常计数——闸门只挡「重复采样」，不挡「反复变动」。"""
    detector = _detector(min_samples=2, hold_s=0.0)
    detector.observe(_SIG, _T0, row_key=_row(0))
    assert detector.observe(_SIG, _T0 + 1.0, row_key=_row(1)) == CONFIRMED


# ---------- T-13：锁存与新根解锁 ----------
def test_latch_prevents_the_same_flip_from_waking_twice():
    """`confirmed` → `note_fired` → 同 signature 恒 idle（同一次翻转绝不反复开轮）。"""
    detector = _detector(min_samples=2, hold_s=0.0)
    detector.observe(_SIG, _T0, row_key=_row(0))
    assert detector.observe(_SIG, _T0 + 1.0, row_key=_row(1)) == CONFIRMED
    detector.note_fired()
    for step in range(2, 8):
        assert detector.observe(_SIG, _T0 + step, row_key=_row(step)) == IDLE


def test_new_bar_unlocks_the_latch():
    """`note_new_bar` 后同方向翻转可再次被跟踪（新根 = 新证据）。"""
    detector = _detector(min_samples=2, hold_s=0.0)
    detector.observe(_SIG, _T0, row_key=_row(0))
    detector.observe(_SIG, _T0 + 1.0, row_key=_row(1))
    detector.note_fired()
    assert detector.observe(_SIG, _T0 + 2.0, row_key=_row(2)) == IDLE
    detector.note_new_bar()
    assert detector.observe(_SIG, _T0 + 3.0, row_key=_row(3)) == PENDING
    assert detector.observe(_SIG, _T0 + 4.0, row_key=_row(4)) == CONFIRMED


def test_latched_signature_does_not_block_a_different_flip():
    """锁存只锁**那一次**翻转：换个方向照常进入跟踪。"""
    detector = _detector(min_samples=2, hold_s=0.0)
    detector.observe(_SIG, _T0, row_key=_row(0))
    detector.observe(_SIG, _T0 + 1.0, row_key=_row(1))
    detector.note_fired()
    assert detector.observe(_OTHER, _T0 + 2.0, row_key=_row(2)) == PENDING
