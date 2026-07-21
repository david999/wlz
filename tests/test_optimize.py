"""参数标定层测试(迭代 7):搜索命中、walk-forward 切分、约束淘汰。

构造样本:每个 cycle = 5 个小幅震荡预热值 + 1 个尖峰(触发 OPEN)+ 1 个回归值
(触发 CLOSE)。窗口 5、cycle 长度 7、步长与 cycle 对齐,保证每 cycle 恰好一笔
正收益交易;逐 cycle 变化回归值使各笔收益不同,从而夏普可计算(std>0)。
"""
from pathlib import Path

import yaml

from arb.optimize.report import (
    RISK_WARNING,
    best_params_doc,
    format_is_oos_summary,
    to_param_dict,
    write_best_params_yaml,
)
from arb.optimize.search import (
    Constraints,
    ParamGrid,
    Params,
    grid_search,
    random_search,
    select_best,
)
from arb.optimize.walk_forward import make_splits, run_walk_forward

WINDOW = 5
CYCLE_LEN = 7
# 各 cycle 回归离场值(接近尖峰后新窗口均值,触发 CLOSE);互不相同以产生收益方差。
EXIT_VALUES = [12.0, 14.0, 10.0, 13.0, 11.0, 15.0]


def build_samples(cycles: int) -> list[tuple[int, float]]:
    """构造含 `cycles` 笔可预测交易的样本序列。"""
    samples: list[tuple[int, float]] = []
    ts = 0
    for i in range(cycles):
        for v in [0.0, 1.0, 0.0, 1.0, 0.0]:  # 预热:非零方差、均低于阈值
            samples.append((ts, v))
            ts += 1000
        samples.append((ts, 60.0))  # 尖峰 -> OPEN
        ts += 1000
        samples.append((ts, EXIT_VALUES[i % len(EXIT_VALUES)]))  # 回归 -> CLOSE
        ts += 1000
    return samples


# ---------------- search ----------------

def test_grid_search_hits_and_sorts():
    samples = build_samples(4)
    grid = ParamGrid(
        windows=[WINDOW],
        entry_zs=[1.0],
        exit_zs=[0.5],
        thresholds_bps=[5.0, 1000.0],  # 5.0 命中;1000.0 因 net<threshold 永不开仓
    )
    constraints = Constraints(max_drawdown_bps=1000.0, min_trades=1)
    results = grid_search(samples, grid, constraints)

    assert len(results) == 2
    # 已按分数降序:命中组合在前且可行
    assert results[0].feasible is True
    assert results[0].params.threshold_bps == 5.0
    assert results[0].metrics.num_trades == 4
    # 高阈值组合无交易 -> 被 min_trades 淘汰
    infeasible = [r for r in results if r.params.threshold_bps == 1000.0][0]
    assert infeasible.feasible is False
    assert infeasible.metrics.num_trades == 0
    assert infeasible.score == float("-inf")


def test_select_best_returns_feasible():
    samples = build_samples(3)
    grid = ParamGrid([WINDOW], [1.0], [0.5], [5.0])
    best = select_best(grid_search(samples, grid, Constraints(1000.0, 1)))
    assert best is not None
    assert best.params.threshold_bps == 5.0
    assert best.metrics.num_trades == 3
    assert best.metrics.sharpe > 0  # 收益互不相同 -> std>0 -> 夏普可计算且为正


def test_random_search_covers_grid_when_n_large():
    samples = build_samples(2)
    grid = ParamGrid([WINDOW], [1.0, 2.0], [0.5], [5.0])
    results = random_search(samples, grid, Constraints(1000.0, 1), n_samples=99, seed=7)
    assert len(results) == grid.size() == 2


def test_invalid_window_is_eliminated_not_crashing():
    # window=1 会触发下游 ValueError,应被视作淘汰而非中断整轮搜索
    samples = build_samples(3)
    grid = ParamGrid([1, WINDOW], [1.0], [0.5], [5.0])
    results = grid_search(samples, grid, Constraints(1000.0, 1))
    assert len(results) == 2  # 两组都被评估,非法组合未中断搜索
    bad = [r for r in results if r.params.window == 1][0]
    assert bad.feasible is False
    assert bad.metrics.num_trades == 0
    assert bad.score == float("-inf")
    # 合法组合仍被选出
    best = select_best(results)
    assert best is not None and best.params.window == WINDOW


# ---------------- 约束淘汰 ----------------

def test_min_trades_eliminates_all():
    samples = build_samples(3)
    grid = ParamGrid([WINDOW], [1.0], [0.5], [5.0])
    # 要求至少 100 笔,实际仅 3 笔 -> 全部淘汰
    results = grid_search(samples, grid, Constraints(max_drawdown_bps=1000.0, min_trades=100))
    assert all(r.feasible is False for r in results)
    assert select_best(results) is None


def test_max_drawdown_eliminates_all():
    samples = build_samples(3)
    grid = ParamGrid([WINDOW], [1.0], [0.5], [5.0])
    # 全为正收益 -> 实际回撤为 0;要求回撤 <= -1(不可能)-> 全部淘汰
    results = grid_search(samples, grid, Constraints(max_drawdown_bps=-1.0, min_trades=1))
    assert all(r.feasible is False for r in results)
    assert select_best(results) is None


# ---------------- walk-forward 切分 ----------------

def test_make_splits_indices():
    # 42 个样本,train=14(2 cycles),test=7(1 cycle),step=7 -> 4 段
    splits = make_splits(n_samples=42, train_size=14, test_size=7, step=7)
    assert len(splits) == 4
    assert (splits[0].train_start, splits[0].train_end) == (0, 14)
    assert (splits[0].test_start, splits[0].test_end) == (14, 21)
    # 相邻段前移 step;训练窗后紧邻样本外窗
    assert splits[1].train_start == 7
    assert splits[1].test_start == splits[1].train_end
    assert splits[-1].test_end == 42


def test_make_splits_drops_incomplete_tail():
    # 40 个样本无法容纳第 4 段(需 42) -> 只剩 3 段
    splits = make_splits(n_samples=40, train_size=14, test_size=7, step=7)
    assert len(splits) == 3
    assert splits[-1].test_end <= 40


# ---------------- walk-forward 运行 ----------------

def test_run_walk_forward_aggregates_oos():
    samples = build_samples(6)  # 42 个样本
    grid = ParamGrid([WINDOW], [1.0], [0.5], [5.0])
    wf = run_walk_forward(
        samples,
        grid,
        Constraints(max_drawdown_bps=1000.0, min_trades=1),
        train_size=14,
        test_size=7,
        step=7,
    )
    assert len(wf.segments) == 4
    assert wf.num_selected == 4
    # 每段 OOS 窗恰好 1 笔交易 -> 聚合样本外共 4 笔
    assert wf.out_sample.num_trades == 4
    # 每段训练窗 2 cycles -> 聚合样本内共 8 笔
    assert wf.in_sample.num_trades == 8
    for seg in wf.segments:
        assert seg.chosen is not None
        assert seg.chosen.threshold_bps == 5.0
        assert seg.out_sample.num_trades == 1


def test_run_walk_forward_skips_when_infeasible():
    samples = build_samples(6)
    grid = ParamGrid([WINDOW], [1.0], [0.5], [5.0])
    # 训练窗仅 2 笔,要求 >=100 -> 每段均无可行参数
    wf = run_walk_forward(
        samples,
        grid,
        Constraints(max_drawdown_bps=1000.0, min_trades=100),
        train_size=14,
        test_size=7,
        step=7,
    )
    assert wf.num_selected == 0
    assert all(seg.chosen is None for seg in wf.segments)
    assert wf.out_sample.num_trades == 0


# ---------------- report ----------------

def test_write_best_params_yaml_roundtrip(tmp_path: Path):
    params_by_pair = {
        "BTC/USDT": Params(window=200, entry_z=2.0, exit_z=0.5, threshold_bps=8.0),
        "ETH/USDT": Params(window=120, entry_z=1.5, exit_z=0.4, threshold_bps=6.0),
    }
    out = write_best_params_yaml(tmp_path / "best_params.yaml", params_by_pair)
    assert out.exists()

    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert set(doc["pairs"].keys()) == {"BTC/USDT", "ETH/USDT"}
    assert doc["pairs"]["BTC/USDT"] == to_param_dict(params_by_pair["BTC/USDT"])
    assert doc["pairs"]["BTC/USDT"]["window"] == 200
    # 每 pair 一组四个字段
    assert set(doc["pairs"]["ETH/USDT"].keys()) == {
        "window",
        "entry_z",
        "exit_z",
        "threshold_bps",
    }


def test_best_params_doc_structure():
    doc = best_params_doc({"BTC/USDT": Params(10, 2.0, 0.5, 5.0)})
    assert doc == {
        "pairs": {
            "BTC/USDT": {
                "window": 10,
                "entry_z": 2.0,
                "exit_z": 0.5,
                "threshold_bps": 5.0,
            }
        }
    }


def test_summary_contains_risk_warning():
    samples = build_samples(6)
    grid = ParamGrid([WINDOW], [1.0], [0.5], [5.0])
    wf = run_walk_forward(
        samples,
        grid,
        Constraints(max_drawdown_bps=1000.0, min_trades=1),
        train_size=14,
        test_size=7,
        step=7,
    )
    text = format_is_oos_summary("BTC/USDT", wf)
    assert "参数标定摘要: BTC/USDT" in text
    assert "样本内" in text and "样本外" in text
    assert RISK_WARNING in text
    assert "paper" in text and "testnet" in text
