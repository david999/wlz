"""历史价差样本合成/导出测试(纯离线,不触网)。"""
from __future__ import annotations

import pytest

from arb.backtest.history import (
    export_to_csv,
    fetch_ohlcv,
    synthesize_from_ohlcv,
)
from arb.backtest.loader import load_from_csv

SPOT_FEE = 8.0
PERP_FEE = 5.0
FEE_BPS = 2.0 * (SPOT_FEE + PERP_FEE)  # 26.0


def ohlcv(ts: int, close: float) -> list:
    """构造 ccxt 标准 OHLCV 行 [ts, open, high, low, close, volume]。"""
    return [ts, close, close, close, close, 1.0]


def test_net_bps_formula():
    # spot=100, perp=101 -> mid=100.5, gross=(1/100.5)*1e4≈99.5025
    spot = [ohlcv(1000, 100.0)]
    perp = [ohlcv(1000, 101.0)]
    out = synthesize_from_ohlcv(spot, perp, SPOT_FEE, PERP_FEE)
    assert len(out) == 1
    ts, net = out[0]
    assert ts == 1000
    gross = (101.0 - 100.0) / ((101.0 + 100.0) / 2.0) * 1e4
    assert net == pytest.approx(gross - FEE_BPS)


def test_signed_funding_direction():
    # 正基差:perp 贵 -> short_perp 收资金费(+)
    pos_spot = [ohlcv(1000, 100.0)]
    pos_perp = [ohlcv(1000, 101.0)]
    base_pos = synthesize_from_ohlcv(pos_spot, pos_perp, SPOT_FEE, PERP_FEE, 0.0)[0][1]
    fund_pos = synthesize_from_ohlcv(pos_spot, pos_perp, SPOT_FEE, PERP_FEE, 10.0)[0][1]
    assert fund_pos == pytest.approx(base_pos + 10.0)

    # 负基差:spot 贵 -> long_perp 付资金费(-)
    neg_spot = [ohlcv(1000, 101.0)]
    neg_perp = [ohlcv(1000, 100.0)]
    base_neg = synthesize_from_ohlcv(neg_spot, neg_perp, SPOT_FEE, PERP_FEE, 0.0)[0][1]
    fund_neg = synthesize_from_ohlcv(neg_spot, neg_perp, SPOT_FEE, PERP_FEE, 10.0)[0][1]
    assert fund_neg == pytest.approx(base_neg - 10.0)


def test_time_sorted_and_aligned():
    # spot 乱序;perp 缺 ts=3000(不重叠);ts=4000 仅 perp 有
    spot = [ohlcv(3000, 100.3), ohlcv(1000, 100.1), ohlcv(2000, 100.2)]
    perp = [ohlcv(2000, 101.2), ohlcv(1000, 101.1), ohlcv(4000, 101.4)]
    out = synthesize_from_ohlcv(spot, perp, SPOT_FEE, PERP_FEE)
    ts_list = [ts for ts, _ in out]
    assert ts_list == [1000, 2000]  # 仅交集,且严格升序


def test_skip_invalid_price():
    spot = [ohlcv(1000, 0.0), ohlcv(2000, 100.0)]
    perp = [ohlcv(1000, 101.0), ohlcv(2000, 101.0)]
    out = synthesize_from_ohlcv(spot, perp, SPOT_FEE, PERP_FEE)
    assert [ts for ts, _ in out] == [2000]


def test_csv_roundtrip(tmp_path):
    spot = [ohlcv(1000, 100.0), ohlcv(2000, 100.5), ohlcv(3000, 99.8)]
    perp = [ohlcv(1000, 101.0), ohlcv(2000, 100.4), ohlcv(3000, 100.2)]
    samples = synthesize_from_ohlcv(spot, perp, SPOT_FEE, PERP_FEE, funding_bps=3.0)
    csv_path = tmp_path / "hist.csv"
    export_to_csv(samples, str(csv_path))
    loaded = load_from_csv(str(csv_path))
    assert [ts for ts, _ in loaded] == [ts for ts, _ in samples]
    for (ts_a, net_a), (ts_b, net_b) in zip(loaded, samples):
        assert ts_a == ts_b
        assert net_a == pytest.approx(net_b, abs=1e-9)


def test_fetch_unsupported_exchange():
    pytest.importorskip("ccxt")  # ccxt 缺失则跳过,不触网
    with pytest.raises(ValueError):
        fetch_ohlcv("__not_an_exchange__", "BTC/USDT")
