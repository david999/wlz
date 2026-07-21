"""Walk-forward 参数标定:训练窗选参 → 紧邻样本外窗评估,防过拟合。

把时间升序样本切成多段"训练窗 → 紧邻样本外(OOS)窗":
- 每段在训练窗内用 grid_search + select_best 选参(受硬约束淘汰);
- 用选中的参数在紧邻的 OOS 窗上回测评估;
- 聚合所有 OOS 交易(按段拼接实现收益)重算整体样本外指标。

样本外指标才是"防过拟合"的诚实估计;样本内指标仅作对比参考。
"""
from __future__ import annotations

from dataclasses import dataclass

from arb.backtest.engine import run_backtest
from arb.backtest.metrics import BacktestMetrics, compute_metrics
from arb.optimize.search import (
    Constraints,
    Objective,
    ParamGrid,
    Params,
    default_objective,
    grid_search,
    select_best,
)


@dataclass(frozen=True)
class WalkForwardSplit:
    """一段切分的样本下标(左闭右开):[train_start,train_end)+[test_start,test_end)。"""

    train_start: int
    train_end: int
    test_start: int
    test_end: int


@dataclass(frozen=True)
class SegmentResult:
    """单段结果:选中参数 + 训练窗(样本内)指标 + OOS(样本外)指标。

    chosen 为 None 表示该段训练窗内所有参数均被硬约束淘汰,该段跳过。
    """

    split: WalkForwardSplit
    chosen: Params | None
    in_sample: BacktestMetrics | None
    out_sample: BacktestMetrics | None


@dataclass(frozen=True)
class WalkForwardResult:
    """整体结果:逐段明细 + 聚合样本内/样本外指标。"""

    segments: list[SegmentResult]
    in_sample: BacktestMetrics   # 各段训练窗选中参数的交易拼接后重算
    out_sample: BacktestMetrics  # 各段 OOS 窗交易拼接后重算(核心评估口径)

    @property
    def num_selected(self) -> int:
        return sum(1 for s in self.segments if s.chosen is not None)


def make_splits(
    n_samples: int,
    train_size: int,
    test_size: int,
    step: int | None = None,
) -> list[WalkForwardSplit]:
    """滚动切分:训练窗后紧邻样本外窗;step 默认为 test_size(窗口不重叠前移)。

    仅保留完整的 (train+test) 段;末尾不足一段的残余样本丢弃。
    """
    if train_size < 2:
        raise ValueError("train_size 至少为 2")
    if test_size < 1:
        raise ValueError("test_size 至少为 1")
    if step is None:
        step = test_size
    if step < 1:
        raise ValueError("step 至少为 1")

    splits: list[WalkForwardSplit] = []
    train_start = 0
    while train_start + train_size + test_size <= n_samples:
        train_end = train_start + train_size
        test_start = train_end
        test_end = test_start + test_size
        splits.append(
            WalkForwardSplit(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        train_start += step
    return splits


def run_walk_forward(
    samples: list[tuple[int, float]],
    grid: ParamGrid,
    constraints: Constraints,
    train_size: int,
    test_size: int,
    step: int | None = None,
    objective: Objective = default_objective,
) -> WalkForwardResult:
    """执行 walk-forward 标定并聚合样本内/外指标。"""
    splits = make_splits(len(samples), train_size, test_size, step)

    segments: list[SegmentResult] = []
    is_returns: list[float] = []
    oos_returns: list[float] = []

    for sp in splits:
        train = samples[sp.train_start : sp.train_end]
        test = samples[sp.test_start : sp.test_end]

        results = grid_search(train, grid, constraints, objective)
        best = select_best(results)
        if best is None:
            segments.append(SegmentResult(sp, None, None, None))
            continue

        p = best.params
        train_report = run_backtest(
            train, window=p.window, entry_z=p.entry_z, exit_z=p.exit_z, threshold_bps=p.threshold_bps
        )
        oos_report = run_backtest(
            test, window=p.window, entry_z=p.entry_z, exit_z=p.exit_z, threshold_bps=p.threshold_bps
        )

        is_returns.extend(t.realized_bps for t in train_report.trades)
        oos_returns.extend(t.realized_bps for t in oos_report.trades)

        segments.append(
            SegmentResult(
                split=sp,
                chosen=p,
                in_sample=train_report.metrics,
                out_sample=oos_report.metrics,
            )
        )

    return WalkForwardResult(
        segments=segments,
        in_sample=compute_metrics(is_returns),
        out_sample=compute_metrics(oos_returns),
    )
