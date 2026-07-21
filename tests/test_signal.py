"""ZScoreSignalEngine 单元测试(迭代 2)。"""
from arb.marketdata.spread import DIR_LONG_PERP_SHORT_SPOT, DIR_SHORT_PERP_LONG_SPOT
from arb.strategy.signal import Action, ZScoreSignalEngine


def test_warmup_returns_hold():
    eng = ZScoreSignalEngine(window=5, entry_z=2.0, exit_z=0.5, threshold_bps=10.0)
    for _ in range(4):
        sig = eng.update(10.0, has_position=False)
        assert sig.action == Action.HOLD
        assert sig.reason == "warmup"


def test_zero_std_returns_hold():
    eng = ZScoreSignalEngine(window=3, entry_z=2.0, exit_z=0.5, threshold_bps=1.0)
    eng.update(5.0, False)
    eng.update(5.0, False)
    sig = eng.update(5.0, False)  # 窗口满但 std=0 -> z=None
    assert sig.action == Action.HOLD


def test_open_signal_echoes_direction():
    eng = ZScoreSignalEngine(window=5, entry_z=1.5, exit_z=0.5, threshold_bps=5.0)
    # 用一批小波动值建立非零方差,再来一个大正值触发正 z
    for v in [0.0, 1.0, 0.0, 1.0, 0.0]:
        eng.update(v, False)
    # 方向由调用方(compute_spread)传入,OPEN 信号原样回传
    sig = eng.update(50.0, has_position=False, direction=DIR_SHORT_PERP_LONG_SPOT)
    assert sig.action == Action.OPEN
    assert sig.direction == DIR_SHORT_PERP_LONG_SPOT
    assert sig.zscore >= 1.5


def test_open_signal_echoes_negative_direction():
    eng = ZScoreSignalEngine(window=5, entry_z=1.0, exit_z=0.5, threshold_bps=-1000.0)
    for v in [99.0, 101.0, 99.0, 101.0, 100.0]:
        eng.update(v, False)
    # 负向偏离且阈值极低,触发开仓;方向由传入值决定
    sig = eng.update(-50.0, has_position=False, direction=DIR_LONG_PERP_SHORT_SPOT)
    assert sig.action == Action.OPEN
    assert sig.direction == DIR_LONG_PERP_SHORT_SPOT


def test_no_entry_when_below_threshold():
    eng = ZScoreSignalEngine(window=5, entry_z=1.5, exit_z=0.5, threshold_bps=1000.0)
    for v in [0.0, 1.0, 0.0, 1.0, 0.0]:
        eng.update(v, False)
    sig = eng.update(50.0, has_position=False)  # z 够大但 net<threshold
    assert sig.action == Action.HOLD
    assert sig.reason == "no_entry"


def test_close_on_mean_revert():
    eng = ZScoreSignalEngine(window=5, entry_z=1.5, exit_z=0.5, threshold_bps=5.0)
    for v in [10.0, 12.0, 8.0, 11.0, 9.0]:
        eng.update(v, False)
    # 持仓中喂入接近均值的值 -> |z| 很小 -> CLOSE
    sig = eng.update(10.0, has_position=True, direction=DIR_SHORT_PERP_LONG_SPOT)
    assert sig.action == Action.CLOSE
