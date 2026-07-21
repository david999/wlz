"""回测指标与引擎测试(迭代 2)。"""
from arb.backtest.engine import run_backtest
from arb.backtest.metrics import compute_metrics, equity_curve_bps, max_drawdown_bps


def test_empty_metrics():
    m = compute_metrics([])
    assert m.num_trades == 0
    assert m.total_bps == 0.0
    assert m.sharpe == 0.0


def test_metrics_basic():
    m = compute_metrics([10.0, -5.0, 15.0, -2.0])
    assert m.num_trades == 4
    assert m.wins == 2
    assert m.win_rate == 0.5
    assert abs(m.total_bps - 18.0) < 1e-9
    assert abs(m.avg_bps - 4.5) < 1e-9


def test_equity_curve_and_drawdown():
    curve = equity_curve_bps([10.0, -4.0, -3.0, 20.0])
    assert curve == [10.0, 6.0, 3.0, 23.0]
    # 峰值 10 -> 回落到 3 -> 最大回撤 7
    assert max_drawdown_bps(curve) == 7.0


def test_sharpe_positive_when_consistent_gains():
    m = compute_metrics([5.0, 6.0, 4.0, 5.0])
    assert m.sharpe > 0


def test_run_backtest_produces_trade():
    # 构造:小波动预热(非零方差),再尖峰(触发 OPEN),再回归(触发 CLOSE)
    samples = []
    ts = 0
    for v in [0.0, 1.0, 0.0, 1.0, 0.0]:
        samples.append((ts, v)); ts += 1000
    samples.append((ts, 60.0)); ts += 1000      # 尖峰 -> OPEN
    samples.append((ts, 12.0)); ts += 1000       # 回归到新窗口均值附近 -> CLOSE
    report = run_backtest(samples, window=5, entry_z=1.0, exit_z=0.5, threshold_bps=5.0)
    assert report.metrics.num_trades >= 1
    t = report.trades[0]
    # entry 60、exit 12 -> realized = 48(从宽价差进、窄价差出)
    assert abs(t.realized_bps - 48.0) < 1e-6
    assert t.realized_bps > 0
