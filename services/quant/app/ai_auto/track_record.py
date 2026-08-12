"""战绩回喂决策纯核心（ai-trade-realized-edge-calibration 轴 D）。

模型今天在一个**绩效真空**里做决策：它不知道自己历史胜率 40%、赔率 1.01，而 40% 的胜率要求赔率
1.47。本模块把这几个数（外加「一笔往返的价格位移中位数 vs 一次往返成本」）写进每轮 payload 与
prompt——**只读、可关、样本不足即整键缺席**。

两段口径，各有各的来源：
- `history`：同 `(instId, bar)` 的历史池化，**启动期一次性快照，会话内不刷新**。刷新需要在盯市
  tick 上开 DB 会话，那是一条今天不存在的 IO 面，收益（几小时内战绩几乎不变）远小于风险。
- `session`：本会话至今的已结算往返，零 IO、每轮现算。

四条装配门禁（任一不满足 ⇒ **整键缺席** ⇒ prompt 逐字节回退）：
1. `ai_trade_track_record_enabled == False`；
2. `history.roundTrips < ai_trade_track_record_min_trips`；
3. `verdict.code == "insufficient_samples"`；
4. 该 `(instId, bar)` 组合无任何历史往返。
**绝不用「全标的合计」冒充本标的战绩**——BTC 15m 的胜率与 PEPE 1m 的胜率不是同一个东西。

四条文案硬约束：① 数值全部由本模块格式化好，`prompts.py` 不做任何算术；② **禁止 markdown 粗体**
——后端文案里的 `**` 在四个渲染点会显示成字面星号（本仓库已复发 3 次）；③ 不得出现任何指令性
措辞让模型「必须减少交易」——这一段是**证据**，不是门禁；④ **每一个数都必须带它自己的分母**。

**成本句是一条独立的、可整行缺席的行**（[P0-4] 阻断级）：生产库上 89% 的往返腿费恒 0（那是「没记」
不是「免费」），若照模板渲染，这一行会变成「一次往返成本约 0.0 bps」——把「别做撑不过成本的交易」
这句纪律翻译成「交易几乎不要钱」的反向暗示。这是本轮唯一一条会直接把模型推向更高换手的路径，
故它是**硬门禁**而不是文案偏好。
"""
from __future__ import annotations


def build_track_record(history: dict | None, session_trips: list, settings,
                       ) -> dict | None:
    """装配 `payload["trackRecord"]`；四条门禁任一不满足 ⇒ `None`（⇒ 整键缺席）。

    Args:
        history: 启动期快照 `{"pooled": {...}, "verdict": {...}, "instId":…, "bar":…}`；缺席 ⇒ None。
        session_trips: `AttributionLedger.settled_trips()`（本会话至今，零 IO）。
        settings: 会话私有 Settings 副本（总闸与两个下限的唯一真源）。
    """
    if not getattr(settings, "ai_trade_track_record_enabled", False) or not history:
        return None
    pooled = history.get("pooled") or {}
    trips = int(pooled.get("roundTrips") or 0)
    if trips <= 0 or trips < int(getattr(settings, "ai_trade_track_record_min_trips", 20)):
        return None
    if (history.get("verdict") or {}).get("code") == "insufficient_samples":
        return None
    return {"history": {**{k: pooled.get(k) for k in _HISTORY_KEYS},
                        "instId": history.get("instId"), "bar": history.get("bar"),
                        # 成本句的门禁在**装配期**判定而不是渲染期：阈值住在会话私有的那份 Settings
                        # 副本里，渲染期只拿得到进程默认值，两者分叉会让预设改过的阈值变成死字母。
                        "costSentenceOk": _cost_sentence_ok(pooled, settings)},
            "session": _session_segment(session_trips)}


_HISTORY_KEYS = ("roundTrips", "winRate", "payoff", "breakevenPayoff", "expectancyUsd",
                 "medianMoveBps", "medianCostBps", "feeTrips", "feeCoverage",
                 "expectancyR", "rSamples")


def _session_segment(trips: list) -> dict:
    """本会话段：只报**笔数**与三个中位读数；一笔都没有 ⇒ `{"roundTrips": 0}`（不造中位数）。"""
    rows = list(trips or [])
    if not rows:
        return {"roundTrips": 0}
    r_values = [t.r_multiple for t in rows if getattr(t, "r_multiple", None) is not None]
    return {"roundTrips": len(rows),
            "medianMoveBps": _median([getattr(t, "move_bps", None) for t in rows]),
            "medianRMultiple": _median(r_values),
            "netPnlUsd": round(sum(getattr(t, "net_pnl_usd", 0.0) or 0.0 for t in rows), 4)}


def _cost_sentence_ok(pooled: dict, settings) -> bool:
    """成本句的独立门禁（[P0-4]）：覆盖率 / 带费笔数 / 中位成本任一不达标 ⇒ False ⇒ **整行省略**。

    绝不下发一个由「没记费率」算出来的 `0.0 bps` 往返成本——那会把「别做撑不过成本的交易」这句
    纪律翻译成「交易几乎不要钱」的反向暗示（D-9 / R-11）。
    """
    median_cost = pooled.get("medianCostBps")
    coverage = pooled.get("feeCoverage")
    if median_cost is None or float(median_cost) <= 0:
        return False
    if int(pooled.get("feeTrips") or 0) < _MIN_FEE_TRIPS:
        return False
    floor = float(getattr(settings, "ai_trade_track_record_min_fee_coverage", 0.5))
    return coverage is not None and float(coverage) >= floor


def format_track_record_text(wire: dict | None) -> str:
    """prompt 段全文（`prompts.py` 只加装配行）；键缺席 ⇒ 空串 ⇒ 整段省略。"""
    if not wire:
        return ""
    hist = wire.get("history") or {}
    sess = wire.get("session") or {}
    scope = " ".join(str(v) for v in (hist.get("instId") or "本标的", hist.get("bar")) if v)
    lines = [
        f"【你的实测战绩】（{scope}，历史 {int(hist.get('roundTrips') or 0)} 笔往返 + "
        f"本会话 {int(sess.get('roundTrips') or 0)} 笔）",
        f"- 胜率 {_pct(hist.get('winRate'))}，赔率 {_num(hist.get('payoff'))}；"
        f"该胜率下的盈亏平衡赔率是 {_num(hist.get('breakevenPayoff'))}。",
        f"- 一笔往返的价格位移中位数 {_bps(hist.get('medianMoveBps'))}。",
    ]
    if hist.get("costSentenceOk"):       # [P0-4] 覆盖率不足 ⇒ **整行省略**，绝不下发 0.0 bps
        lines.append(f"- 而一次往返成本约 {_bps(hist.get('medianCostBps'))}"
                     f"（取自 {int(hist.get('feeTrips') or 0)} 笔有费率记录的往返）。")
    # `expectancyR` 是 R 倍数的**算术均值**（`profitability.pooled` 的口径），不是中位数——
    # 把均值写成「中位」是给模型一个它无法核对的错标签，而本段的全部价值就在于每个数都如实带着
    # 它的口径与分母。历史快照里没有 R 的中位数（`pooled` 不产出），故如实说「均值」。
    lines.append(f"- 已实现 R 倍数均值 {_num(hist.get('expectancyR'))}"
                 f"（{int(hist.get('rSamples') or 0)} 笔可算 R）。")
    lines.append("纪律：交易成本不会因为方向判断正确而消失；位移撑不过成本的交易，方向对了也是亏。")
    lines.append("以上是历史统计，不是对未来的承诺；样本不足时本段不出现。")
    return "\n".join(lines)


# 成本句的**第二道**门禁：即使覆盖率达标，带费样本少于这个数也不下结论（与轴 C 问二同一个下限）。
_MIN_FEE_TRIPS = 10


class TrackRecordRuntime:
    """会话侧运行时（镜像 `PriorRuntime` / `CostRuntime` 的「对象 + `inject(payload)`」范式）。

    `session.py` 里只留一行 `self._track.inject(payload)`——它的净增预算是硬验收判据。
    """

    def __init__(self, snapshot: dict | None, settings, trips_provider=None) -> None:
        self.snapshot = snapshot
        self.settings = settings
        # 会话段每轮现算，故这里存的是一个取数闭包（`AttributionLedger.settled_trips`），
        # 而不是一份会立刻过期的列表快照。
        self._trips_provider = trips_provider or (lambda: [])
        self.shown = False       # [P1-9] 本会话到底有没有真的把战绩喂给模型（随 aiStats 落库）

    def inject(self, payload: dict) -> None:
        """把 `trackRecord` 装进本轮 payload；门禁不过 ⇒ **整键缺席**（不是 None）。"""
        wire = build_track_record(self.snapshot, self._trips_provider(), self.settings)
        if wire is not None:
            payload["trackRecord"] = wire
            self.shown = True


def _pct(value) -> str:
    return "未知" if value is None else f"{float(value) * 100:.1f}%"


def _bps(value) -> str:
    return "未知" if value is None else f"{float(value):.1f} bps"


def _num(value) -> str:
    return "未知" if value is None else f"{float(value):.3g}"


def _median(values: list) -> float | None:
    rows = sorted(float(v) for v in values if v is not None)
    if not rows:
        return None
    mid = len(rows) // 2
    return round(rows[mid] if len(rows) % 2 else (rows[mid - 1] + rows[mid]) / 2, 4)
