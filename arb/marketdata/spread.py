"""净价差计算(纯函数,无第三方依赖,便于单元测试)。

术语:
- 现货腿(spot)与永续腿(perp)构成同一资产的 basis 对冲。
- 毛价差 gross_bps:两腿可成交价之差(基点)。
- 手续费 fee_bps:开+平往返、双腿 taker 手续费之和。
- 资金费 funding_bps:按持仓方向对资金费率的方向化折算(信息性,见 README 说明)。
- 净价差 net_bps = gross_bps - fee_bps + funding_bps。
"""
from __future__ import annotations

from dataclasses import dataclass

from arb.config.models import PairConfig
from arb.connectors.base import OrderBookSnapshot

# 两个可执行方向
DIR_SHORT_PERP_LONG_SPOT = "short_perp_long_spot"   # 正基差:perp 贵 -> 卖 perp、买 spot
DIR_LONG_PERP_SHORT_SPOT = "long_perp_short_spot"   # 负基差:spot 贵 -> 买 perp、卖 spot


@dataclass(frozen=True)
class SpreadResult:
    pair_name: str
    direction: str
    spot_price: float      # 该方向下现货腿的成交价
    perp_price: float      # 该方向下永续腿的成交价
    gross_bps: float
    fee_bps: float
    funding_bps: float
    net_bps: float
    is_opportunity: bool


def _is_stale(snap: OrderBookSnapshot, now_ms: int, staleness_ms: int) -> bool:
    return (now_ms - snap.timestamp) > staleness_ms


def compute_spread(
    spot: OrderBookSnapshot | None,
    perp: OrderBookSnapshot | None,
    cfg: PairConfig,
    now_ms: int,
    funding_bps: float = 0.0,
) -> SpreadResult | None:
    """计算一对腿的净价差。

    返回 None 表示本次数据不可用(缺失、过期或报价非法)。
    否则在两个方向中取毛价差更优者,叠加手续费与资金费得到净价差。

    funding_bps 约定为"正数=正资金费率(多头付、空头收)"的原始折算值;
    在 short_perp 方向作为收益(+),在 long_perp 方向作为成本(-)。
    """
    if spot is None or perp is None:
        return None
    if _is_stale(spot, now_ms, cfg.staleness_ms) or _is_stale(perp, now_ms, cfg.staleness_ms):
        return None
    if spot.bid <= 0 or spot.ask <= 0 or perp.bid <= 0 or perp.ask <= 0:
        return None

    # 往返(开+平)双腿 taker 手续费
    fee_bps = 2.0 * (cfg.spot_taker_fee_bps + cfg.perp_taker_fee_bps)

    # 方向 A:卖 perp@perp.bid,买 spot@spot.ask
    a_mid = (perp.bid + spot.ask) / 2.0
    a_gross = (perp.bid - spot.ask) / a_mid * 1e4

    # 方向 B:买 perp@perp.ask,卖 spot@spot.bid
    b_mid = (spot.bid + perp.ask) / 2.0
    b_gross = (spot.bid - perp.ask) / b_mid * 1e4

    if a_gross >= b_gross:
        direction = DIR_SHORT_PERP_LONG_SPOT
        gross_bps = a_gross
        spot_price, perp_price = spot.ask, perp.bid
        signed_funding = funding_bps          # 空 perp 收资金费
    else:
        direction = DIR_LONG_PERP_SHORT_SPOT
        gross_bps = b_gross
        spot_price, perp_price = spot.bid, perp.ask
        signed_funding = -funding_bps         # 多 perp 付资金费

    net_bps = gross_bps - fee_bps + signed_funding
    return SpreadResult(
        pair_name=cfg.name,
        direction=direction,
        spot_price=spot_price,
        perp_price=perp_price,
        gross_bps=gross_bps,
        fee_bps=fee_bps,
        funding_bps=signed_funding,
        net_bps=net_bps,
        is_opportunity=net_bps > cfg.threshold_bps,
    )
