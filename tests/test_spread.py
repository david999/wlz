"""compute_spread 单元测试(纯函数,无需网络/第三方交易所依赖)。"""
from __future__ import annotations

import pytest

from arb.config.models import PairConfig
from arb.connectors.base import OrderBookSnapshot
from arb.marketdata.spread import (
    DIR_LONG_PERP_SHORT_SPOT,
    DIR_SHORT_PERP_LONG_SPOT,
    compute_spread,
)

NOW = 1_000_000_000_000  # 固定"现在"时间戳(ms)


def make_cfg(threshold_bps: float = 20.0) -> PairConfig:
    return PairConfig(
        name="BTC-basis",
        spot_symbol="BTC/USDT",
        perp_symbol="BTC/USDT:USDT",
        threshold_bps=threshold_bps,
        spot_taker_fee_bps=8.0,
        perp_taker_fee_bps=5.0,
        staleness_ms=2000,
    )


def ob(symbol: str, bid: float, ask: float, ts: int = NOW) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol=symbol, bid=bid, ask=ask, bid_qty=1.0, ask_qty=1.0, timestamp=ts
    )


def test_positive_basis_is_opportunity():
    cfg = make_cfg()
    spot = ob("BTC/USDT", bid=100.0, ask=100.1)
    perp = ob("BTC/USDT:USDT", bid=101.0, ask=101.1)
    res = compute_spread(spot, perp, cfg, NOW)
    assert res is not None
    assert res.direction == DIR_SHORT_PERP_LONG_SPOT
    assert res.fee_bps == pytest.approx(26.0)          # 2*(8+5)
    assert res.gross_bps == pytest.approx(89.507, abs=0.1)
    assert res.net_bps == pytest.approx(res.gross_bps - 26.0)
    assert res.is_opportunity is True


def test_fees_can_kill_thin_spread():
    cfg = make_cfg()
    spot = ob("BTC/USDT", bid=99.99, ask=100.0)
    perp = ob("BTC/USDT:USDT", bid=100.1, ask=100.11)
    res = compute_spread(spot, perp, cfg, NOW)
    assert res is not None
    # 毛价差很薄,扣掉 26bps 往返费后应为负,不构成机会
    assert res.net_bps < res.gross_bps
    assert res.is_opportunity is False


def test_funding_is_added_for_short_perp():
    cfg = make_cfg()
    spot = ob("BTC/USDT", bid=100.0, ask=100.1)
    perp = ob("BTC/USDT:USDT", bid=101.0, ask=101.1)
    base = compute_spread(spot, perp, cfg, NOW, funding_bps=0.0)
    with_funding = compute_spread(spot, perp, cfg, NOW, funding_bps=10.0)
    assert base is not None and with_funding is not None
    # 空 perp 方向,正资金费率作为收益叠加
    assert with_funding.funding_bps == pytest.approx(10.0)
    assert with_funding.net_bps == pytest.approx(base.net_bps + 10.0)


def test_negative_basis_selects_long_perp_direction():
    cfg = make_cfg()
    spot = ob("BTC/USDT", bid=101.0, ask=101.1)
    perp = ob("BTC/USDT:USDT", bid=100.0, ask=100.1)
    res = compute_spread(spot, perp, cfg, NOW, funding_bps=10.0)
    assert res is not None
    assert res.direction == DIR_LONG_PERP_SHORT_SPOT
    # 多 perp 方向,正资金费率变为成本(负号)
    assert res.funding_bps == pytest.approx(-10.0)


def test_stale_data_returns_none():
    cfg = make_cfg()
    spot = ob("BTC/USDT", bid=100.0, ask=100.1, ts=NOW - 3000)  # 超过 2000ms
    perp = ob("BTC/USDT:USDT", bid=101.0, ask=101.1, ts=NOW)
    assert compute_spread(spot, perp, cfg, NOW) is None


def test_missing_leg_returns_none():
    cfg = make_cfg()
    perp = ob("BTC/USDT:USDT", bid=101.0, ask=101.1)
    assert compute_spread(None, perp, cfg, NOW) is None


def test_invalid_price_returns_none():
    cfg = make_cfg()
    spot = ob("BTC/USDT", bid=100.0, ask=0.0)  # 非法卖价
    perp = ob("BTC/USDT:USDT", bid=101.0, ask=101.1)
    assert compute_spread(spot, perp, cfg, NOW) is None
