"""因子层 engine 子包：库 / 评估 / 挖掘 / 训练 / 打分（纯计算，严格因果）(spec §2.1)。

聚合导出五模块的公开 API，供 service / strategy / agent tools 单点导入。
"""
from __future__ import annotations

from .evaluation import (  # noqa: F401
    FactorEvalResult,
    FactorHorizonStat,
    eval_result_to_dict,
    evaluate_factor,
    evaluate_factor_key,
    forward_return,
    resolve_direction,
)
from .library import (  # noqa: F401
    FACTOR_LIBRARY,
    FactorSpec,
    available_factors,
    compute_factor,
    factor_meta,
    is_bar_compatible,
)
from .mining import (  # noqa: F401
    MINE_WORK_UNIT_LIMIT,
    default_candidates,
    mine_factors,
    mining_workload,
)
from .scoring import (  # noqa: F401
    DEFAULT_CLIP,
    DEFAULT_NORM_WINDOW,
    MemberContribution,
    ScoreBreakdown,
    causal_zscore,
    compose_score,
    member_names,
    resolve_norm_contract,
    score_breakdown,
    score_points,
)
from .training import (  # noqa: F401
    MIN_TRAIN_SAMPLES,
    TrainResult,
    TrainSample,
    available_trainers,
    build_factor_matrix,
    build_train_result,
    fit_at_fraction,
    forward_return_label,
    learning_curve_steps,
    prepare_curve_sample,
    train_weights,
    trainer_available,
)
