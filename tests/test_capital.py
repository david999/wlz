"""资金管理测试(迭代 5)+ 跨所价差测试(迭代 6)。"""
from arb.capital import allocator
from arb.connectors.base import OrderBookSnapshot
from arb.marketdata.cross_spread import (
    DIR_BUY_A_SELL_B,
    compute_cross_spread,
)


def test_allocate():
    a = allocator.allocate(total_capital=20000.0, leverage=2.0, num_pairs=4)
    assert a.max_total_notional == 40000.0
    assert a.per_pair_notional == 10000.0


def test_allocate_guards():
    assert allocator.allocate(0.0, 2.0, 4).per_pair_notional == 0.0
    assert allocator.allocate(100.0, 2.0, 0).per_pair_notional == 0.0


def test_margin_ratio_and_alert():
    # 名义 10000、杠杆 2 -> 保证金 5000;本金 20000 -> 占用率 0.25
    r = allocator.margin_ratio(10000.0, 20000.0, 2.0)
    assert abs(r - 0.25) < 1e-9
    assert allocator.check_margin_alert(0.6, 0.5) is True
    assert allocator.check_margin_alert(0.4, 0.5) is False


def test_rebalance_advice():
    advice = allocator.rebalance_advice({"okx": 8000.0, "bybit": 2000.0})
    assert len(advice) == 1
    a = advice[0]
    assert a.from_exchange == "okx"
    assert a.to_exchange == "bybit"
    assert abs(a.amount - 3000.0) < 1e-6  # 目标 5000 各


def test_rebalance_within_tolerance_no_advice():
    advice = allocator.rebalance_advice({"okx": 5100.0, "bybit": 4900.0}, tolerance_ratio=0.1)
    assert advice == []


def _book(bid, ask, ts=1000):
    return OrderBookSnapshot("SYM", bid, ask, 1.0, 1.0, ts)


def test_cross_spread_opportunity():
    # B 明显更贵 -> 在 A 买、B 卖
    a = _book(100.0, 100.1)
    b = _book(101.0, 101.1)
    res = compute_cross_spread(
        a, b, "okx", "bybit", "BTC-cross",
        threshold_bps=10.0, fee_a_bps=1.0, fee_b_bps=1.0,
        staleness_ms=2000, now_ms=1500,
    )
    assert res is not None
    assert res.direction == DIR_BUY_A_SELL_B
    assert res.net_bps > 0


def test_cross_spread_stale_returns_none():
    a = _book(100.0, 100.1, ts=0)
    b = _book(101.0, 101.1, ts=0)
    res = compute_cross_spread(
        a, b, "okx", "bybit", "X",
        threshold_bps=10.0, fee_a_bps=1.0, fee_b_bps=1.0,
        staleness_ms=100, now_ms=100000,
    )
    assert res is None
