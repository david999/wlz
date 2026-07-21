"""跨交易所价差监控(迭代 6,只读):多连接器 + 跨所净价差 + 再平衡建议。

为每个跨所 pair(spot_exchange != perp_exchange 视为跨所配置)在两个交易所各
建立一个只读连接器,分别订阅同一符号盘口,计算跨所净价差并在超阈值时告警。
另外周期性根据(占位的)各所余额给出再平衡建议,提示资金应如何在两所间调度。

注意:真实余额需交易权限;此处 balances 为配置/占位输入,仅演示调度建议逻辑。
"""
from __future__ import annotations

import asyncio
import time

from arb.capital import allocator
from arb.config.models import PairConfig
from arb.config.settings import Settings
from arb.connectors.base import OrderBookSnapshot
from arb.connectors.ccxt_connector import CCXTConnector
from arb.marketdata.cross_spread import compute_cross_spread
from arb.monitoring.alerts import build_alerter
from arb.monitoring.logger import get_logger


def _cross_pairs(pairs: list[PairConfig]) -> list[PairConfig]:
    """筛选出配置了 spot_exchange/perp_exchange 且两者不同的跨所对象。"""
    out = []
    for p in pairs:
        a = p.spot_exchange
        b = p.perp_exchange
        if a and b and a != b:
            out.append(p)
    return out


class CrossExchangeMonitor:
    def __init__(self, settings: Settings, pairs: list[PairConfig]) -> None:
        self.settings = settings
        self.pairs = _cross_pairs(pairs)
        self.log = get_logger("cross")
        self.alerter = build_alerter(settings.telegram_token, settings.telegram_chat_id)
        self.running = True
        # 每个交易所一个只读连接器
        self.connectors: dict[str, CCXTConnector] = {}
        for p in self.pairs:
            for ex in (p.spot_exchange, p.perp_exchange):
                if ex and ex not in self.connectors:
                    self.connectors[ex] = CCXTConnector(
                        exchange_id=ex,
                        api_key=settings.api_key,
                        secret=settings.api_secret,
                        password=settings.api_password,
                        testnet=settings.testnet,
                    )
        # books[(exchange, symbol)] = snapshot
        self.books: dict[tuple[str, str], OrderBookSnapshot] = {}

    async def _watch(self, exchange: str, symbol: str) -> None:
        conn = self.connectors[exchange]
        backoff = 1.0
        while self.running:
            try:
                self.books[(exchange, symbol)] = await conn.watch_order_book(symbol)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.log.warning(
                    "ws_error", exchange=exchange, symbol=symbol, error=str(exc), retry_in=backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _evaluate(self) -> None:
        while self.running:
            now = int(time.time() * 1000)
            for p in self.pairs:
                # 跨所模型:两腿使用同一资产符号(取 spot_symbol),分别在两个交易所
                symbol = p.spot_symbol
                book_a = self.books.get((p.spot_exchange, symbol))
                book_b = self.books.get((p.perp_exchange, symbol))
                res = compute_cross_spread(
                    book_a, book_b,
                    exchange_a=p.spot_exchange or "",
                    exchange_b=p.perp_exchange or "",
                    pair_name=p.name,
                    threshold_bps=p.threshold_bps,
                    fee_a_bps=p.spot_taker_fee_bps,
                    fee_b_bps=p.perp_taker_fee_bps,
                    staleness_ms=p.staleness_ms,
                    now_ms=now,
                )
                if res is None:
                    continue
                if res.is_opportunity:
                    self.log.warning(
                        ">>> CROSS OPPORTUNITY", pair=res.pair_name, direction=res.direction,
                        net_bps=round(res.net_bps, 2), a=res.exchange_a, b=res.exchange_b,
                    )
                    await self._alert(
                        f"💱 跨所机会 {res.pair_name} {res.direction} net={res.net_bps:.1f}bps"
                    )
                else:
                    self.log.info(
                        "cross_spread", pair=res.pair_name, net_bps=round(res.net_bps, 2)
                    )
            await asyncio.sleep(self.settings.eval_interval_sec)

    async def _alert(self, text: str) -> None:
        """告警失败不得影响监控主循环。"""
        try:
            await self.alerter.send(text)
        except Exception:  # noqa: BLE001
            pass

    def rebalance_suggestions(self, balances: dict[str, float]) -> list:
        """对外暴露的再平衡建议(供调度/测试调用)。"""
        return allocator.rebalance_advice(balances)

    async def run(self) -> None:
        if not self.pairs:
            self.log.error(
                "no_cross_pairs",
                hint="在 symbols.yaml 中为 pair 配置不同的 spot_exchange/perp_exchange",
            )
            return
        self.log.info(
            "cross_monitor_start",
            exchanges=list(self.connectors.keys()),
            pairs=[p.name for p in self.pairs],
        )
        tasks = []
        for p in self.pairs:
            symbol = p.spot_symbol
            tasks.append(asyncio.create_task(self._watch(p.spot_exchange, symbol)))
            tasks.append(asyncio.create_task(self._watch(p.perp_exchange, symbol)))
        tasks.append(asyncio.create_task(self._evaluate()))
        try:
            await asyncio.gather(*tasks)
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        self.running = False
        for conn in self.connectors.values():
            try:
                await conn.close()
            except Exception:  # noqa: BLE001
                pass
        self.log.info("cross_monitor_stopped")
