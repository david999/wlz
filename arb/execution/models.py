"""执行层数据结构与方向/盈亏工具(纯逻辑,可测)。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from arb.marketdata.spread import (
    DIR_LONG_PERP_SHORT_SPOT,
    DIR_SHORT_PERP_LONG_SPOT,
)


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    NEW = "NEW"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: Side
    price: float
    qty: float
    fee_quote: float = 0.0


@dataclass(frozen=True)
class Position:
    pair_name: str
    direction: str
    spot_symbol: str
    perp_symbol: str
    spot_side: Side
    perp_side: Side
    spot_qty: float
    perp_qty: float
    entry_spot_price: float
    entry_perp_price: float
    opened_ts: int


@dataclass
class ExecutionResult:
    ok: bool
    fills: list[Fill] = field(default_factory=list)
    position: Position | None = None
    realized_pnl_quote: float = 0.0
    error: str | None = None


def sides_for_direction(direction: str) -> tuple[Side, Side]:
    """返回 (spot_side, perp_side)。"""
    if direction == DIR_SHORT_PERP_LONG_SPOT:
        return Side.BUY, Side.SELL      # 买现货、卖永续
    if direction == DIR_LONG_PERP_SHORT_SPOT:
        return Side.SELL, Side.BUY      # 卖现货、买永续
    raise ValueError(f"未知方向: {direction}")


def _leg_pnl(side: Side, entry: float, exit_: float, qty: float) -> float:
    """单腿平仓盈亏(计价币)。多头= (exit-entry)*qty;空头相反。"""
    sign = 1.0 if side == Side.BUY else -1.0
    return sign * (exit_ - entry) * qty


def realized_pnl_quote(
    pos: Position, close_spot_price: float, close_perp_price: float
) -> float:
    """按两腿平仓价计算已实现盈亏(计价币,未计平仓手续费)。"""
    return _leg_pnl(pos.spot_side, pos.entry_spot_price, close_spot_price, pos.spot_qty) + _leg_pnl(
        pos.perp_side, pos.entry_perp_price, close_perp_price, pos.perp_qty
    )
