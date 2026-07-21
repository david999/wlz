"""执行器测试:SimulatedExecutor(迭代 3)+ LiveExecutor(迭代 4,用 FakeConnector)。

async 测试统一用 asyncio.run 驱动,避免依赖 pytest-asyncio 插件。
"""
import asyncio

from arb.config.models import PairConfig
from arb.execution.executor import SimulatedExecutor
from arb.execution.live_executor import LiveExecutor
from arb.execution.models import Side
from arb.marketdata.spread import DIR_LONG_PERP_SHORT_SPOT, DIR_SHORT_PERP_LONG_SPOT


def _pair(notional=100.0):
    return PairConfig(
        name="BTC-basis", spot_symbol="BTC/USDT", perp_symbol="BTC/USDT:USDT",
        threshold_bps=20.0, spot_taker_fee_bps=8.0, perp_taker_fee_bps=5.0,
        staleness_ms=2000, trade_notional=notional,
    )


def test_simulated_long_perp_short_spot_direction_pnl():
    """负基差方向(买永续、卖现货)的方向与盈亏符号回归测试。

    回归保护:signal 不再自行推断方向后,此方向必须能被正确开/平仓。
    """
    ex = SimulatedExecutor(slippage_bps=0.0)
    pair = _pair(100.0)
    res = asyncio.run(
        ex.open_hedge(pair, DIR_LONG_PERP_SHORT_SPOT, 100.0, 100.0, now_ms=0)
    )
    assert res.ok
    pos = res.position
    assert pos.spot_side == Side.SELL and pos.perp_side == Side.BUY
    # 永续涨到 110(多头获利 +10)、现货不变 -> 合计 +10
    close = asyncio.run(ex.close_hedge(pos, 100.0, 110.0))
    assert abs(close.realized_pnl_quote - 10.0) < 1e-6


def test_simulated_open_and_close_roundtrip():
    ex = SimulatedExecutor(slippage_bps=0.0)  # 无滑点便于精确校验
    pair = _pair(100.0)
    res = asyncio.run(
        ex.open_hedge(pair, DIR_SHORT_PERP_LONG_SPOT, 100.0, 100.0, now_ms=0)
    )
    assert res.ok
    pos = res.position
    assert pos.spot_side == Side.BUY and pos.perp_side == Side.SELL
    assert abs(pos.spot_qty - 1.0) < 1e-9

    # 现货涨、永续不变:买现货赚、卖永续平;方向中性下应有明确 pnl
    close = asyncio.run(ex.close_hedge(pos, 110.0, 100.0))
    assert close.ok
    # spot: (110-100)*1=+10;perp: 卖出后价格不变 pnl=0 -> 合计 +10
    assert abs(close.realized_pnl_quote - 10.0) < 1e-6


def test_simulated_slippage_is_adverse():
    ex = SimulatedExecutor(slippage_bps=10.0)
    pair = _pair(100.0)
    res = asyncio.run(ex.open_hedge(pair, DIR_SHORT_PERP_LONG_SPOT, 100.0, 100.0, 0))
    pos = res.position
    # 买现货抬价 > 100,卖永续压价 < 100
    assert pos.entry_spot_price > 100.0
    assert pos.entry_perp_price < 100.0


class FakeConnector:
    """可编排成交行为的假交易连接器,用于 LiveExecutor 测试。"""

    def __init__(self, fill_map):
        # fill_map: symbol -> dict(status, filled, average)
        self.fill_map = fill_map
        self.orders = {}
        self.market_orders = []
        self.canceled = []
        self._seq = 0

    async def create_limit_order(self, symbol, side, qty, price):
        self._seq += 1
        oid = f"o{self._seq}"
        self.orders[oid] = {"id": oid, "symbol": symbol, "side": side, "qty": qty}
        return {"id": oid, "status": "open", "filled": 0.0, "average": None}

    async def create_market_order(self, symbol, side, qty):
        self.market_orders.append((symbol, side, qty))
        return {"id": "m", "status": "closed", "filled": qty, "average": 100.0}

    async def fetch_order(self, symbol, order_id):
        f = self.fill_map[symbol]
        return {"id": order_id, "status": f["status"],
                "filled": f["filled"], "average": f["average"]}

    async def cancel_order(self, symbol, order_id):
        self.canceled.append((symbol, order_id))


def test_live_both_legs_fill():
    conn = FakeConnector({
        "BTC/USDT": {"status": "closed", "filled": 1.0, "average": 100.0},
        "BTC/USDT:USDT": {"status": "closed", "filled": 1.0, "average": 100.0},
    })
    ex = LiveExecutor(conn, timeout_sec=0.5, poll_interval=0.01)
    res = asyncio.run(
        ex.open_hedge(_pair(100.0), DIR_SHORT_PERP_LONG_SPOT, 100.0, 100.0, 0)
    )
    assert res.ok
    assert res.position is not None
    assert res.error is None
    assert conn.market_orders == []  # 两腿刚好对齐,无回滚


def test_live_one_leg_only_rolls_back():
    conn = FakeConnector({
        "BTC/USDT": {"status": "closed", "filled": 1.0, "average": 100.0},
        "BTC/USDT:USDT": {"status": "open", "filled": 0.0, "average": None},
    })
    ex = LiveExecutor(conn, timeout_sec=0.2, poll_interval=0.01)
    res = asyncio.run(
        ex.open_hedge(_pair(100.0), DIR_SHORT_PERP_LONG_SPOT, 100.0, 100.0, 0)
    )
    assert res.ok is False
    assert res.error == "one_leg_only_rolled_back"
    # 已成交的现货腿被市价回滚
    assert len(conn.market_orders) == 1
    # 未成交的永续腿被撤单
    assert ("BTC/USDT:USDT", "o2") in conn.canceled or len(conn.canceled) >= 1


def test_live_partial_fill_aligned():
    conn = FakeConnector({
        "BTC/USDT": {"status": "closed", "filled": 1.0, "average": 100.0},
        "BTC/USDT:USDT": {"status": "closed", "filled": 0.5, "average": 100.0},
    })
    ex = LiveExecutor(conn, timeout_sec=0.2, poll_interval=0.01)
    res = asyncio.run(
        ex.open_hedge(_pair(100.0), DIR_SHORT_PERP_LONG_SPOT, 100.0, 100.0, 0)
    )
    assert res.ok
    # 现货多成交,超出部分被市价回滚以对齐名义
    assert len(conn.market_orders) == 1
    assert abs(res.position.spot_qty - 0.5) < 1e-9
