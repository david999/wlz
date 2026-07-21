"""参数标定层(迭代 7):纯逻辑、离线可单测、不触网。

复用 arb.backtest.engine.run_backtest 作为唯一评估入口(samples 已含 fee 口径),
在此之上做:
- search:对 (window, entry_z, exit_z, threshold_bps) 网格/随机搜索;
- walk_forward:训练窗选参 → 紧邻样本外窗评估,聚合样本外指标以防过拟合;
- report:产出 best_params.yaml 与样本内外对比摘要(含风险提示)。

目标函数:样本外夏普最大化;硬约束 max_drawdown_bps ≤ 阈值、num_trades ≥ N,
不满足即淘汰。本层不改动任何现有文件与 settings;新增 env 参数仅在 report 中登记。
"""
from arb.optimize.search import (
    Constraints,
    Objective,
    ParamGrid,
    Params,
    SearchResult,
    default_objective,
    grid_search,
    random_search,
    select_best,
)
from arb.optimize.walk_forward import (
    SegmentResult,
    WalkForwardResult,
    WalkForwardSplit,
    make_splits,
    run_walk_forward,
)
from arb.optimize.report import (
    RISK_WARNING,
    SUGGESTED_ENV_VARS,
    best_params_doc,
    format_is_oos_summary,
    to_param_dict,
    write_best_params_yaml,
)

__all__ = [
    "Constraints",
    "Objective",
    "ParamGrid",
    "Params",
    "SearchResult",
    "default_objective",
    "grid_search",
    "random_search",
    "select_best",
    "SegmentResult",
    "WalkForwardResult",
    "WalkForwardSplit",
    "make_splits",
    "run_walk_forward",
    "RISK_WARNING",
    "SUGGESTED_ENV_VARS",
    "best_params_doc",
    "format_is_oos_summary",
    "to_param_dict",
    "write_best_params_yaml",
]
