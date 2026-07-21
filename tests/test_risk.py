"""风控规则与管理器测试(迭代 3)。"""
from arb.execution.models import Position, Side
from arb.marketdata.spread import DIR_SHORT_PERP_LONG_SPOT
from arb.risk import rules
from arb.risk.manager import RiskManager


def test_position_limit():
    assert rules.check_position_limit(500.0, 400.0, 1000.0) is True
    assert rules.check_position_limit(700.0, 400.0, 1000.0) is False


def test_delta_bps_neutral():
    # 现货 +1000、永续 -1000 -> 完全中性
    assert rules.delta_bps(1000.0, -1000.0) == 0.0
    # 现货 +1000、永续 -900 -> net 100 / gross 1900
    d = rules.delta_bps(1000.0, -900.0)
    assert d > 0
    assert rules.check_delta_neutral(1000.0, -900.0, 1000.0) is True
    assert rules.check_delta_neutral(1000.0, -900.0, 10.0) is False


def test_drawdown():
    assert rules.drawdown_pct(100.0, 95.0) == 5.0
    assert rules.check_drawdown(100.0, 96.0, 5.0) is True
    assert rules.check_drawdown(100.0, 94.0, 5.0) is False


def test_manager_approve_and_limit():
    rm = RiskManager(1000.0, 50.0, 5.0, initial_equity=10000.0)
    assert rm.approve_open(0.0, 500.0).allow is True
    assert rm.approve_open(800.0, 500.0).allow is False  # 超上限


def test_manager_drawdown_triggers_kill():
    rm = RiskManager(1000.0, 50.0, 5.0, initial_equity=1000.0)
    d = rm.record_pnl(-60.0)  # 回撤 6% > 5%
    assert d.kill is True
    assert rm.killed is True
    # kill 后拒绝一切开仓
    assert rm.approve_open(0.0, 10.0).allow is False


def test_manager_delta_check():
    rm = RiskManager(1000.0, 10.0, 5.0, initial_equity=1000.0)
    pos = Position(
        pair_name="X", direction=DIR_SHORT_PERP_LONG_SPOT,
        spot_symbol="B/U", perp_symbol="B/U:U",
        spot_side=Side.BUY, perp_side=Side.SELL,
        spot_qty=1.0, perp_qty=1.0,
        entry_spot_price=100.0, entry_perp_price=100.0, opened_ts=0,
    )
    # 完全对称 -> delta 0 -> 通过
    assert rm.check_position_delta(pos).allow is True
