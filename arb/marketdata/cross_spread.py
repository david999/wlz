"""跨交易所价差计算(迭代 6,纯函数,可测)。

模型 B:同一资产、同一类合约(如两个交易所的 BTC 永续或 BTC 现货)在
不同交易所间的价差套利。与单所 basis 不同,这里两腿在不同交易所,保证金
不共享,需要各自准备资金(见 capital.allocator 的再平衡建议)。

方向:
- BUY_A_SELL_B:在 A 买(A.ask)、在 B 卖(B.bid),当 B 更贵时有利。
- BUY_B_SELL_A:在 B 买(B.ask)、在 A 卖(A.bid),当 A 更贵时有利。
"""
from __future__ import annotations

from dataclasses import dataclass

from arb.connectors.base import OrderBookSnapshot

DIR_BUY_A_SELL_B = "buy_a_sell_b"
DIR_BUY_B_SELL_A = "buy_b_sell_a"


@dataclass(frozen=True)
class CrossSpreadResult:
    pair_name: str
    direction: str
    exchange_a: str
    exchange_b: str
    price_a: float          # A 腿成交价
    price_b: float          # B 腿成交价
    gross_bps: float
    fee_bps: float
    net_bps: float
    is_opportunity: bool


def _is_stale(snap: OrderBookSnapshot, now_ms: int, staleness_ms: int) -> bool:
    return (now_ms - snap.timestamp) > staleness_ms


def compute_cross_spread(
    book_a: OrderBookSnapshot | None,
    book_b: OrderBookSnapshot | None,
    exchange_a: str,
    exchange_b: str,
    pair_name: str,
    threshold_bps: float,
    fee_a_bps: float,
    fee_b_bps: float,
    staleness_ms: int,
    now_ms: int,
) -> CrossSpreadResult | None:
    """计算两个交易所同一资产的净价差。返回 None 表示数据不可用/过期/非法。"""
    if book_a is None or book_b is None:
        return None
    if _is_stale(book_a, now_ms, staleness_ms) or _is_stale(book_b, now_ms, staleness_ms):
        return None
    if book_a.bid <= 0 or book_a.ask <= 0 or book_b.bid <= 0 or book_b.ask <= 0:
        return None

    fee_bps = 2.0 * (fee_a_bps + fee_b_bps)  # 开+平往返双腿

    # 方向 1:A 买(ask)、B 卖(bid)
    mid1 = (book_a.ask + book_b.bid) / 2.0
    g1 = (book_b.bid - book_a.ask) / mid1 * 1e4

    # 方向 2:B 买(ask)、A 卖(bid)
    mid2 = (book_b.ask + book_a.bid) / 2.0
    g2 = (book_a.bid - book_b.ask) / mid2 * 1e4

    if g1 >= g2:
        direction = DIR_BUY_A_SELL_B
        gross_bps = g1
        price_a, price_b = book_a.ask, book_b.bid
    else:
        direction = DIR_BUY_B_SELL_A
        gross_bps = g2
        price_a, price_b = book_a.bid, book_b.ask

    net_bps = gross_bps - fee_bps
    return CrossSpreadResult(
        pair_name=pair_name,
        direction=direction,
        exchange_a=exchange_a,
        exchange_b=exchange_b,
        price_a=price_a,
        price_b=price_b,
        gross_bps=gross_bps,
        fee_bps=fee_bps,
        net_bps=net_bps,
        is_opportunity=net_bps > threshold_bps,
    )
