"""真实下单执行器(迭代 4):双腿限价对冲 + 超时撤单 + 部分成交处理 + 回滚。

状态机(open_hedge):
1. 两腿同时挂限价单。
2. 轮询直到两腿全部成交或超时。
3. 超时后撤销未成交挂单,读取各腿实际成交量。
4. 对齐两腿名义:取较小成交名义为对冲仓位;对超出的一侧用市价单回滚多出的部分;
   若某腿完全未成交,则市价回滚另一腿并判定失败(避免留下裸露单腿)。

close_hedge:两腿市价反向平仓,按成交均价计算已实现盈亏。
"""
from __future__ import annotations

import asyncio

from arb.config.models import PairConfig
from arb.connectors.base import TradingConnector
from arb.execution.executor import Executor
from arb.execution.models import (
    ExecutionResult,
    Fill,
    Position,
    Side,
    realized_pnl_quote,
    sides_for_direction,
)


def _side_str(side: Side) -> str:
    return "buy" if side == Side.BUY else "sell"


def _opposite(side: Side) -> Side:
    return Side.SELL if side == Side.BUY else Side.BUY


class LiveExecutor(Executor):
    def __init__(
        self,
        conn: TradingConnector,
        timeout_sec: float = 5.0,
        poll_interval: float = 0.2,
    ) -> None:
        self.conn = conn
        self.timeout_sec = timeout_sec
        self.poll_interval = poll_interval

    async def _wait_fills(self, legs: list[tuple[str, str]]) -> dict[str, dict]:
        """轮询多个 (symbol, order_id),返回 order_id -> 最新订单 dict。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_sec
        latest: dict[str, dict] = {}
        while loop.time() < deadline:
            all_done = True
            for symbol, oid in legs:
                o = await self.conn.fetch_order(symbol, oid)
                latest[oid] = o
                if o.get("status") != "closed":
                    all_done = False
            if all_done:
                break
            await asyncio.sleep(self.poll_interval)
        return latest

    async def open_hedge(
        self, pair: PairConfig, direction: str, spot_price: float, perp_price: float, now_ms: int
    ) -> ExecutionResult:
        spot_side, perp_side = sides_for_direction(direction)
        spot_qty = pair.trade_notional / spot_price
        perp_qty = pair.trade_notional / perp_price

        spot_ord = await self.conn.create_limit_order(
            pair.spot_symbol, _side_str(spot_side), spot_qty, spot_price
        )
        perp_ord = await self.conn.create_limit_order(
            pair.perp_symbol, _side_str(perp_side), perp_qty, perp_price
        )

        latest = await self._wait_fills(
            [(pair.spot_symbol, spot_ord["id"]), (pair.perp_symbol, perp_ord["id"])]
        )
        spot_o = latest.get(spot_ord["id"], spot_ord)
        perp_o = latest.get(perp_ord["id"], perp_ord)

        # 撤销未成交挂单;撤单后重新查询最终状态,避免最后一次轮询与撤单
        # 之间发生成交却被漏计(误判为未成交而错误回滚)
        legs = {pair.spot_symbol: spot_o, pair.perp_symbol: perp_o}
        for symbol, o in list(legs.items()):
            if o.get("status") == "open":
                try:
                    await self.conn.cancel_order(symbol, o["id"])
                except Exception:  # noqa: BLE001
                    pass
                try:
                    legs[symbol] = await self.conn.fetch_order(symbol, o["id"])
                except Exception:  # noqa: BLE001
                    pass
        spot_o = legs[pair.spot_symbol]
        perp_o = legs[pair.perp_symbol]

        spot_filled = float(spot_o.get("filled") or 0.0)
        perp_filled = float(perp_o.get("filled") or 0.0)
        spot_avg = spot_o.get("average") or spot_price
        perp_avg = perp_o.get("average") or perp_price

        spot_notional = spot_filled * spot_avg
        perp_notional = perp_filled * perp_avg
        matched = min(spot_notional, perp_notional)

        if matched <= 0:
            # 至少一腿完全未成交 -> 回滚已成交的一侧,判定失败
            if spot_filled > 0:
                await self.conn.create_market_order(
                    pair.spot_symbol, _side_str(_opposite(spot_side)), spot_filled
                )
            if perp_filled > 0:
                await self.conn.create_market_order(
                    pair.perp_symbol, _side_str(_opposite(perp_side)), perp_filled
                )
            return ExecutionResult(ok=False, error="one_leg_only_rolled_back")

        # 回滚超出对冲名义的多余部分,保持两腿名义对齐
        if spot_notional > matched:
            excess_qty = (spot_notional - matched) / spot_avg
            await self.conn.create_market_order(
                pair.spot_symbol, _side_str(_opposite(spot_side)), excess_qty
            )
        if perp_notional > matched:
            excess_qty = (perp_notional - matched) / perp_avg
            await self.conn.create_market_order(
                pair.perp_symbol, _side_str(_opposite(perp_side)), excess_qty
            )

        final_spot_qty = matched / spot_avg
        final_perp_qty = matched / perp_avg
        pos = Position(
            pair_name=pair.name,
            direction=direction,
            spot_symbol=pair.spot_symbol,
            perp_symbol=pair.perp_symbol,
            spot_side=spot_side,
            perp_side=perp_side,
            spot_qty=final_spot_qty,
            perp_qty=final_perp_qty,
            entry_spot_price=spot_avg,
            entry_perp_price=perp_avg,
            opened_ts=now_ms,
        )
        fills = [
            Fill(pair.spot_symbol, spot_side, spot_avg, final_spot_qty),
            Fill(pair.perp_symbol, perp_side, perp_avg, final_perp_qty),
        ]
        partial = (spot_o.get("status") != "closed") or (perp_o.get("status") != "closed")
        return ExecutionResult(
            ok=True, fills=fills, position=pos,
            error="partial_fill_aligned" if partial else None,
        )

    async def close_hedge(
        self, pos: Position, close_spot_price: float, close_perp_price: float
    ) -> ExecutionResult:
        close_spot_side = _opposite(pos.spot_side)
        close_perp_side = _opposite(pos.perp_side)
        spot_o = await self.conn.create_market_order(
            pos.spot_symbol, _side_str(close_spot_side), pos.spot_qty
        )
        perp_o = await self.conn.create_market_order(
            pos.perp_symbol, _side_str(close_perp_side), pos.perp_qty
        )
        spot_exit = spot_o.get("average") or close_spot_price
        perp_exit = perp_o.get("average") or close_perp_price
        pnl = realized_pnl_quote(pos, spot_exit, perp_exit)
        fills = [
            Fill(pos.spot_symbol, close_spot_side, spot_exit, pos.spot_qty),
            Fill(pos.perp_symbol, close_perp_side, perp_exit, pos.perp_qty),
        ]
        return ExecutionResult(ok=True, fills=fills, position=None, realized_pnl_quote=pnl)
