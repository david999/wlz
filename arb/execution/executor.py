"""执行器:抽象接口 + 纸上模拟撮合(SimulatedExecutor)。

真实下单执行器见 live_executor.py(迭代 4)。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from arb.config.models import PairConfig
from arb.execution.models import (
    ExecutionResult,
    Fill,
    Position,
    Side,
    realized_pnl_quote,
    sides_for_direction,
)


def _slip(price: float, side: Side, slippage_bps: float) -> float:
    """对我方不利的滑点:买入抬价、卖出压价。"""
    adj = slippage_bps / 1e4
    return price * (1 + adj) if side == Side.BUY else price * (1 - adj)


class Executor(ABC):
    @abstractmethod
    async def open_hedge(
        self, pair: PairConfig, direction: str, spot_price: float, perp_price: float, now_ms: int
    ) -> ExecutionResult:
        ...

    @abstractmethod
    async def close_hedge(
        self, pos: Position, close_spot_price: float, close_perp_price: float
    ) -> ExecutionResult:
        ...


class SimulatedExecutor(Executor):
    """确定性模拟撮合:立即以(含滑点的)给定价格成交。用于 paper 模式与测试。"""

    def __init__(self, slippage_bps: float = 2.0) -> None:
        self.slippage_bps = slippage_bps

    async def open_hedge(
        self, pair: PairConfig, direction: str, spot_price: float, perp_price: float, now_ms: int
    ) -> ExecutionResult:
        spot_side, perp_side = sides_for_direction(direction)
        spot_qty = pair.trade_notional / spot_price
        perp_qty = pair.trade_notional / perp_price
        spot_fill_px = _slip(spot_price, spot_side, self.slippage_bps)
        perp_fill_px = _slip(perp_price, perp_side, self.slippage_bps)
        fills = [
            Fill(pair.spot_symbol, spot_side, spot_fill_px, spot_qty,
                 pair.trade_notional * pair.spot_taker_fee_bps / 1e4),
            Fill(pair.perp_symbol, perp_side, perp_fill_px, perp_qty,
                 pair.trade_notional * pair.perp_taker_fee_bps / 1e4),
        ]
        pos = Position(
            pair_name=pair.name,
            direction=direction,
            spot_symbol=pair.spot_symbol,
            perp_symbol=pair.perp_symbol,
            spot_side=spot_side,
            perp_side=perp_side,
            spot_qty=spot_qty,
            perp_qty=perp_qty,
            entry_spot_price=spot_fill_px,
            entry_perp_price=perp_fill_px,
            opened_ts=now_ms,
        )
        return ExecutionResult(ok=True, fills=fills, position=pos)

    async def close_hedge(
        self, pos: Position, close_spot_price: float, close_perp_price: float
    ) -> ExecutionResult:
        # 平仓方向与开仓相反
        close_spot_side = Side.SELL if pos.spot_side == Side.BUY else Side.BUY
        close_perp_side = Side.SELL if pos.perp_side == Side.BUY else Side.BUY
        spot_exit = _slip(close_spot_price, close_spot_side, self.slippage_bps)
        perp_exit = _slip(close_perp_price, close_perp_side, self.slippage_bps)
        pnl = realized_pnl_quote(pos, spot_exit, perp_exit)
        fills = [
            Fill(pos.spot_symbol, close_spot_side, spot_exit, pos.spot_qty),
            Fill(pos.perp_symbol, close_perp_side, perp_exit, pos.perp_qty),
        ]
        return ExecutionResult(ok=True, fills=fills, position=None, realized_pnl_quote=pnl)
