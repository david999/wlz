"""参数搜索:对 (window, entry_z, exit_z, threshold_bps) 做网格/随机搜索。

评估唯一入口为 arb.backtest.engine.run_backtest(samples 已含 fee 口径)。
目标函数默认最大化夏普;硬约束(最大回撤上限、最少交易数)不满足即淘汰
(feasible=False 且 score=-inf,排序时沉底,select_best 只取可行解)。
"""
from __future__ import annotations

import itertools
import random
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

from arb.backtest.engine import run_backtest
from arb.backtest.metrics import BacktestMetrics


@dataclass(frozen=True)
class Params:
    """一组待评估参数。"""

    window: int
    entry_z: float
    exit_z: float
    threshold_bps: float


@dataclass(frozen=True)
class Constraints:
    """硬约束:样本外最大回撤上限(基点)+ 最少交易笔数 N。"""

    max_drawdown_bps: float
    min_trades: int

    def feasible(self, m: BacktestMetrics) -> bool:
        return m.num_trades >= self.min_trades and m.max_drawdown_bps <= self.max_drawdown_bps


# 目标函数签名:输入回测指标,输出"越大越好"的分数。
Objective = Callable[[BacktestMetrics], float]


def default_objective(m: BacktestMetrics) -> float:
    """默认目标:夏普最大化。"""
    return m.sharpe


@dataclass(frozen=True)
class SearchResult:
    """单组参数的评估结果。feasible=False 表示被硬约束淘汰。"""

    params: Params
    metrics: BacktestMetrics
    score: float
    feasible: bool


@dataclass(frozen=True)
class ParamGrid:
    """离散参数网格;iter_params 生成全部组合。"""

    windows: Sequence[int]
    entry_zs: Sequence[float]
    exit_zs: Sequence[float]
    thresholds_bps: Sequence[float]

    def iter_params(self) -> Iterator[Params]:
        for w, e, x, t in itertools.product(
            self.windows, self.entry_zs, self.exit_zs, self.thresholds_bps
        ):
            yield Params(int(w), float(e), float(x), float(t))

    def size(self) -> int:
        return (
            len(self.windows)
            * len(self.entry_zs)
            * len(self.exit_zs)
            * len(self.thresholds_bps)
        )


def evaluate(
    samples: list[tuple[int, float]],
    params: Params,
    constraints: Constraints,
    objective: Objective = default_objective,
) -> SearchResult:
    """对单组参数跑一次回测并按约束打分。

    非法参数(如 window<2 触发下游 ValueError)等同硬约束淘汰:标记 infeasible、
    score=-inf,避免网格中单个非法组合中断整轮搜索、丢失已完成的有效评估。
    """
    try:
        report = run_backtest(
            samples,
            window=params.window,
            entry_z=params.entry_z,
            exit_z=params.exit_z,
            threshold_bps=params.threshold_bps,
        )
    except ValueError:
        m = BacktestMetrics(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)
        return SearchResult(params=params, metrics=m, score=float("-inf"), feasible=False)
    m = report.metrics
    feasible = constraints.feasible(m)
    score = objective(m) if feasible else float("-inf")
    return SearchResult(params=params, metrics=m, score=score, feasible=feasible)


def grid_search(
    samples: list[tuple[int, float]],
    grid: ParamGrid,
    constraints: Constraints,
    objective: Objective = default_objective,
) -> list[SearchResult]:
    """遍历网格,返回按分数降序排列的结果表(不可行解沉底)。"""
    results = [evaluate(samples, p, constraints, objective) for p in grid.iter_params()]
    results.sort(key=lambda r: r.score, reverse=True)
    return results


def random_search(
    samples: list[tuple[int, float]],
    grid: ParamGrid,
    constraints: Constraints,
    n_samples: int,
    seed: int | None = None,
    objective: Objective = default_objective,
) -> list[SearchResult]:
    """从网格组合中不放回随机抽取 n_samples 组评估;结果按分数降序。

    n_samples 超过网格规模时退化为遍历全部组合(等价于 grid_search 的集合)。
    """
    all_params = list(grid.iter_params())
    rng = random.Random(seed)
    k = min(n_samples, len(all_params))
    chosen = rng.sample(all_params, k) if k < len(all_params) else all_params
    results = [evaluate(samples, p, constraints, objective) for p in chosen]
    results.sort(key=lambda r: r.score, reverse=True)
    return results


def select_best(results: Sequence[SearchResult]) -> SearchResult | None:
    """从(通常已按分数降序的)结果表中取首个可行解;全被淘汰则返回 None。"""
    best: SearchResult | None = None
    for r in results:
        if not r.feasible:
            continue
        if best is None or r.score > best.score:
            best = r
    return best
