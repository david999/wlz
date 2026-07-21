"""统一入口:多模式编排。

用法:
    python -m arb.main --mode monitor    # 迭代 1:只读价差监控(落库+告警)
    python -m arb.main --mode backtest   # 迭代 2:在录制/CSV 数据上回放策略并输出报告
    python -m arb.main --mode synthesize # 迭代 4a:拉历史 K 线合成价差样本 CSV(供回测/标定)
    python -m arb.main --mode paper      # 迭代 3:测试网行情驱动的模拟撮合(不下单)
    python -m arb.main --mode live       # 迭代 4:测试网/实盘真实下单(需 ARB_ALLOW_LIVE=true)
    python -m arb.main --mode cross      # 迭代 6:跨交易所价差监控 + 再平衡建议

monitor 全程只读;backtest 离线;paper 只读行情 + 模拟撮合;live 才会真实下单。
模式与所有策略/风控/资金参数均可经 .env(前缀 ARB_)覆盖,见 config/settings.py。
"""
from __future__ import annotations

import argparse
import asyncio
import time

from arb.config.settings import Settings, load_symbols
from arb.config.models import PairConfig
from arb.connectors.base import OrderBookSnapshot
from arb.connectors.ccxt_connector import CCXTConnector
from arb.marketdata.spread import compute_spread
from arb.monitoring.logger import configure_logging, get_logger
from arb.persistence.db import init_db, insert_spread
from arb.persistence.models import SpreadRecord


class SpreadMonitor:
    def __init__(self, settings: Settings, pairs: list[PairConfig]) -> None:
        self.settings = settings
        self.pairs = pairs
        self.connector = CCXTConnector(
            exchange_id=settings.exchange,
            api_key=settings.api_key,
            secret=settings.api_secret,
            password=settings.api_password,
            testnet=settings.testnet,
        )
        self.books: dict[str, OrderBookSnapshot] = {}
        self.funding: dict[str, float] = {}
        self.db = None
        self.running = True
        self.log = get_logger("monitor")

    async def _watch_symbol(self, symbol: str) -> None:
        backoff = 1.0
        while self.running:
            try:
                snap = await self.connector.watch_order_book(symbol)
                self.books[symbol] = snap
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 网络/订阅异常 -> 退避重连
                self.log.warning(
                    "ws_error", symbol=symbol, error=str(exc), retry_in=backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _refresh_funding(self, symbols: list[str]) -> None:
        while self.running:
            for sym in symbols:
                try:
                    rate = await self.connector.fetch_funding_rate(sym)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    rate = None
                if rate is not None:
                    self.funding[sym] = rate
            await asyncio.sleep(self.settings.funding_refresh_sec)

    async def _evaluate(self) -> None:
        while self.running:
            now = int(time.time() * 1000)
            for pair in self.pairs:
                spot = self.books.get(pair.spot_symbol)
                perp = self.books.get(pair.perp_symbol)
                fr = self.funding.get(pair.perp_symbol)
                funding_bps = (fr or 0.0) * 1e4
                result = compute_spread(spot, perp, pair, now, funding_bps=funding_bps)
                if result is None:
                    continue
                try:
                    await self._record(now, pair, result, fr)
                except Exception as exc:  # noqa: BLE001 落库尽力而为,不阻断只读监控
                    self.log.warning("record_failed", pair=pair.name, error=str(exc))
            await asyncio.sleep(self.settings.eval_interval_sec)

    async def _record(self, now, pair, result, fr) -> None:
        rec = SpreadRecord(
            ts=now,
            pair_name=pair.name,
            direction=result.direction,
            spot_price=result.spot_price,
            perp_price=result.perp_price,
            gross_bps=result.gross_bps,
            net_bps=result.net_bps,
            funding_rate=fr,
            is_opportunity=int(result.is_opportunity),
        )
        await insert_spread(self.db, rec)
        if result.is_opportunity:
            self.log.warning(
                ">>> OPPORTUNITY",
                pair=pair.name,
                direction=result.direction,
                net_bps=round(result.net_bps, 2),
                gross_bps=round(result.gross_bps, 2),
                threshold_bps=pair.threshold_bps,
                funding=fr,
            )
        else:
            self.log.info(
                "spread",
                pair=pair.name,
                net_bps=round(result.net_bps, 2),
                gross_bps=round(result.gross_bps, 2),
            )

    async def run(self) -> None:
        self.db = await init_db(self.settings.db_path)
        symbols: set[str] = set()
        for pair in self.pairs:
            symbols.add(pair.spot_symbol)
            symbols.add(pair.perp_symbol)
        perp_symbols = [p.perp_symbol for p in self.pairs]

        self.log.info(
            "monitor_start",
            exchange=self.settings.exchange,
            testnet=self.settings.testnet,
            pairs=[p.name for p in self.pairs],
        )

        tasks = [asyncio.create_task(self._watch_symbol(s)) for s in symbols]
        tasks.append(asyncio.create_task(self._refresh_funding(perp_symbols)))
        tasks.append(asyncio.create_task(self._evaluate()))
        try:
            await asyncio.gather(*tasks)
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        self.running = False
        try:
            await self.connector.close()
        except Exception:  # noqa: BLE001
            pass
        if self.db is not None:
            await self.db.close()
        self.log.info("monitor_stopped")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="加密价差套利系统:多模式编排")
    parser.add_argument(
        "--mode",
        default=None,
        choices=["monitor", "backtest", "synthesize", "paper", "live", "cross"],
        help="运行模式;缺省时用 ARB_MODE / settings.mode",
    )
    return parser.parse_args()


def _run_backtest(settings: Settings, pairs) -> None:
    """迭代 2:离线回放,输出绩效报告。"""
    from arb.backtest.engine import format_report, run_backtest
    from arb.backtest.loader import load_from_csv, load_from_db

    log = get_logger("backtest")
    pair = next((p for p in pairs if p.name == settings.backtest_pair), pairs[0])
    if settings.backtest_source == "csv":
        if not settings.backtest_csv_path:
            raise ValueError("backtest_source=csv 需设置 ARB_BACKTEST_CSV_PATH")
        samples = load_from_csv(settings.backtest_csv_path)
    else:
        samples = load_from_db(settings.db_path, pair.name)
    log.info("backtest_loaded", pair=pair.name, samples=len(samples), source=settings.backtest_source)
    report = run_backtest(
        samples, settings.zscore_window, settings.entry_z, settings.exit_z, pair.threshold_bps
    )
    print(format_report(report, pair.name))


def _run_synthesize(settings: Settings, pairs) -> None:
    """迭代 4a:用 ccxt 历史 K 线合成 (ts_ms, net_bps) 样本并导出 CSV。

    产出可直接用于 --mode backtest(ARB_BACKTEST_SOURCE=csv)与 arb.optimize 参数标定。
    ccxt 缺失/无网络时优雅报错并返回,不抛未捕获异常。
    """
    from arb.backtest.history import export_to_csv, synthesize_from_exchange

    log = get_logger("synthesize")
    pair = next((p for p in pairs if p.name == settings.backtest_pair), pairs[0])
    try:
        samples = synthesize_from_exchange(
            exchange_id=settings.exchange,
            spot_symbol=pair.spot_symbol,
            perp_symbol=pair.perp_symbol,
            spot_taker_fee_bps=pair.spot_taker_fee_bps,
            perp_taker_fee_bps=pair.perp_taker_fee_bps,
            funding_bps=settings.history_funding_bps,
            timeframe=settings.history_timeframe,
            limit=settings.history_limit,
        )
    except (RuntimeError, ValueError) as exc:  # ccxt 缺失/无网/交易所不支持
        log.error("synthesize_failed", pair=pair.name, error=str(exc))
        return
    export_to_csv(samples, settings.history_out_csv)
    log.info(
        "synthesize_done",
        pair=pair.name,
        samples=len(samples),
        out=settings.history_out_csv,
        timeframe=settings.history_timeframe,
    )
    print(f"已合成 {len(samples)} 条样本 -> {settings.history_out_csv}(pair={pair.name})")


def main() -> None:
    args = parse_args()
    settings = Settings()
    configure_logging(settings.log_level)
    mode = args.mode or settings.mode
    symbols_cfg = load_symbols(settings.symbols_path)
    pairs = symbols_cfg.pairs

    if mode == "backtest":
        _run_backtest(settings, pairs)
        return

    if mode == "synthesize":
        _run_synthesize(settings, pairs)
        return

    if mode == "monitor":
        runner = SpreadMonitor(settings, pairs).run()
    elif mode == "paper":
        from arb.trading_engine import TradingEngine
        runner = TradingEngine(settings, pairs, live=False).run()
    elif mode == "live":
        from arb.trading_engine import TradingEngine
        runner = TradingEngine(settings, pairs, live=True).run()
    elif mode == "cross":
        from arb.cross_monitor import CrossExchangeMonitor
        runner = CrossExchangeMonitor(settings, pairs).run()
    else:
        raise ValueError(f"未知模式: {mode}")

    try:
        asyncio.run(runner)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
