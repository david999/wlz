"""基于 ccxt.pro 的只读连接器实现。

迭代 1 只做行情监控:仅使用公共 WebSocket/REST 接口,
不实现任何下单/撤单/划转方法。API 密钥可选,若提供请仅授予只读权限。
"""
from __future__ import annotations

import time

import ccxt.pro as ccxtpro  # ccxt>=4 自带 pro(WebSocket)

from arb.connectors.base import Connector, OrderBookSnapshot, TradingConnector


class CCXTConnector(Connector):
    def __init__(
        self,
        exchange_id: str,
        api_key: str | None = None,
        secret: str | None = None,
        password: str | None = None,
        testnet: bool = True,
    ) -> None:
        if not hasattr(ccxtpro, exchange_id):
            raise ValueError(f"ccxt.pro 不支持交易所: {exchange_id}")
        cfg: dict = {"enableRateLimit": True}
        if api_key:
            cfg["apiKey"] = api_key
            cfg["secret"] = secret
            if password:
                cfg["password"] = password
        self.exchange = getattr(ccxtpro, exchange_id)(cfg)
        if testnet:
            # OKX/Bybit/Binance 等均通过该开关切换到测试网/模拟盘
            self.exchange.set_sandbox_mode(True)

    async def watch_order_book(self, symbol: str) -> OrderBookSnapshot:
        ob = await self.exchange.watch_order_book(symbol, limit=5)
        bids = ob.get("bids") or []
        asks = ob.get("asks") or []
        bid = bids[0][0] if bids else 0.0
        ask = asks[0][0] if asks else 0.0
        bid_qty = bids[0][1] if bids else 0.0
        ask_qty = asks[0][1] if asks else 0.0
        ts = ob.get("timestamp") or int(time.time() * 1000)
        return OrderBookSnapshot(
            symbol=symbol,
            bid=bid,
            ask=ask,
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            timestamp=int(ts),
        )

    async def fetch_funding_rate(self, symbol: str) -> float | None:
        try:
            fr = await self.exchange.fetch_funding_rate(symbol)
        except Exception:
            return None
        return fr.get("fundingRate")

    async def close(self) -> None:
        await self.exchange.close()


def _normalize_order(o: dict) -> dict:
    return {
        "id": o.get("id"),
        "status": o.get("status"),        # open / closed / canceled
        "filled": float(o.get("filled") or 0.0),
        "average": o.get("average"),
    }


class CCXTTradingConnector(CCXTConnector, TradingConnector):
    """在只读连接器基础上增加下单/撤单能力(迭代 4)。

    仅在 live 模式且显式允许时才应构造。需要具备交易权限的 API 密钥。
    """

    async def create_limit_order(self, symbol: str, side: str, qty: float, price: float) -> dict:
        o = await self.exchange.create_order(symbol, "limit", side, qty, price)
        return _normalize_order(o)

    async def create_market_order(self, symbol: str, side: str, qty: float) -> dict:
        o = await self.exchange.create_order(symbol, "market", side, qty)
        return _normalize_order(o)

    async def fetch_order(self, symbol: str, order_id: str) -> dict:
        o = await self.exchange.fetch_order(order_id, symbol)
        return _normalize_order(o)

    async def cancel_order(self, symbol: str, order_id: str) -> None:
        await self.exchange.cancel_order(order_id, symbol)
