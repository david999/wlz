"""连接器抽象接口与归一化盘口结构(纯 stdlib,便于测试)。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class OrderBookSnapshot:
    """归一化后的盘口快照(仅取顶档)。"""

    symbol: str
    bid: float          # 买一价
    ask: float          # 卖一价
    bid_qty: float      # 买一量
    ask_qty: float      # 卖一量
    timestamp: int      # 毫秒时间戳(交易所提供或本地接收时刻)


class Connector(ABC):
    """交易所连接器接口。

    迭代 1 仅暴露【只读】方法,刻意不提供任何下单/撤单能力。
    """

    @abstractmethod
    async def watch_order_book(self, symbol: str) -> OrderBookSnapshot:
        """通过 WebSocket 获取一次最新盘口快照。"""

    @abstractmethod
    async def fetch_funding_rate(self, symbol: str) -> float | None:
        """获取永续合约最新资金费率(小数,如 0.0001 = 0.01%)。"""

    @abstractmethod
    async def close(self) -> None:
        """释放连接资源。"""


class TradingConnector(ABC):
    """交易能力接口(迭代 4 起)。与只读 Connector 分离,便于在非 live 模式完全不引入下单能力。

    side 采用 ccxt 约定的小写字符串 "buy"/"sell";订单返回统一为 dict:
    {id, status(open/closed/canceled), filled(float), average(float|None)}。
    """

    @abstractmethod
    async def create_limit_order(self, symbol: str, side: str, qty: float, price: float) -> dict:
        ...

    @abstractmethod
    async def create_market_order(self, symbol: str, side: str, qty: float) -> dict:
        ...

    @abstractmethod
    async def fetch_order(self, symbol: str, order_id: str) -> dict:
        ...

    @abstractmethod
    async def cancel_order(self, symbol: str, order_id: str) -> None:
        ...
