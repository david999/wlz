"""交易编排引擎(迭代 3 paper / 迭代 4 live):行情 -> 信号 -> 风控 -> 执行。

复用迭代 1 的行情订阅方式(WebSocket 盘口 + 资金费率刷新),在评估循环中:
1. 计算净价差;
2. 用每个 pair 的 ZScoreSignalEngine 产生 OPEN/CLOSE/HOLD 信号;
3. OPEN 前经 RiskManager 审批(仓位上限 + 资金/保证金占用);
4. 通过 Executor(SimulatedExecutor / LiveExecutor)开/平双腿对冲;
5. 平仓结算盈亏,更新风控权益;触发最大回撤则 kill switch 平掉所有仓位并停止开仓。

paper 模式使用只读连接器 + 模拟撮合;live 模式使用交易连接器 + 真实下单。
"""
from __future__ import annotations

import asyncio
import time

from arb.capital import allocator
from arb.config.models import PairConfig
from arb.config.settings import Settings
from arb.connectors.base import Connector, OrderBookSnapshot
from arb.connectors.ccxt_connector import CCXTConnector, CCXTTradingConnector
from arb.execution.executor import Executor, SimulatedExecutor
from arb.execution.live_executor import LiveExecutor
from arb.execution.models import Position, Side
from arb.marketdata.spread import compute_spread
from arb.monitoring.alerts import Alerter, build_alerter
from arb.monitoring.confirm import ConfirmationSource, build_confirmation_source
from arb.monitoring.logger import get_logger
from arb.risk.manager import RiskManager
from arb.strategy.signal import Action, ZScoreSignalEngine


class TradingEngine:
    def __init__(
        self,
        settings: Settings,
        pairs: list[PairConfig],
        live: bool,
        confirm_source: ConfirmationSource | None = None,
    ) -> None:
        self.settings = settings
        self.pairs = pairs
        self.live = live
        self.log = get_logger("live" if live else "paper")

        # live 需要下单能力;paper 仅需只读行情
        self.connector: Connector
        if live:
            self.connector = CCXTTradingConnector(
                exchange_id=settings.exchange,
                api_key=settings.api_key,
                secret=settings.api_secret,
                password=settings.api_password,
                testnet=settings.testnet,
            )
            self.executor: Executor = LiveExecutor(
                self.connector, timeout_sec=settings.order_timeout_sec  # type: ignore[arg-type]
            )
        else:
            self.connector = CCXTConnector(
                exchange_id=settings.exchange,
                api_key=settings.api_key,
                secret=settings.api_secret,
                password=settings.api_password,
                testnet=settings.testnet,
            )
            self.executor = SimulatedExecutor(slippage_bps=settings.slippage_bps)

        self.engines: dict[str, ZScoreSignalEngine] = {
            p.name: ZScoreSignalEngine(
                settings.zscore_window, settings.entry_z, settings.exit_z, p.threshold_bps
            )
            for p in pairs
        }
        self.positions: dict[str, Position] = {}
        self.risk = RiskManager(
            max_position_notional=settings.max_position_notional,
            max_delta_bps=settings.max_delta_bps,
            max_drawdown_pct=settings.max_drawdown_pct,
            initial_equity=settings.total_capital,
        )
        self.alerter: Alerter = build_alerter(settings.telegram_token, settings.telegram_chat_id)
        # 半自动逐单确认:默认基于告警渠道轮询 Telegram;测试可注入 Fake 确认源
        self.confirm_source: ConfirmationSource = (
            confirm_source or build_confirmation_source(self.alerter)
        )
        # 待确认开仓的后台任务(pair -> Task):不阻塞评估循环,平仓/kill switch 仍全自动
        self._pending: dict[str, asyncio.Task] = {}
        if settings.require_manual_confirm and not getattr(self.alerter, "enabled", False):
            self.log.warning(
                "confirm_enabled_but_no_alerter",
                hint="require_manual_confirm=True 但未配置告警渠道,所有开仓将超时作废",
            )
        self.alloc = allocator.allocate(
            settings.total_capital, settings.leverage, max(1, len(pairs))
        )
        self.books: dict[str, OrderBookSnapshot] = {}
        self.funding: dict[str, float] = {}
        self.running = True

    # ---- 行情订阅(与 monitor 相同的退避重连策略) ----
    async def _watch_symbol(self, symbol: str) -> None:
        backoff = 1.0
        while self.running:
            try:
                self.books[symbol] = await self.connector.watch_order_book(symbol)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.log.warning("ws_error", symbol=symbol, error=str(exc), retry_in=backoff)
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

    def pairs_notional(self) -> list[float]:
        out: list[float] = []
        for name in self.positions:
            pair = next((p for p in self.pairs if p.name == name), None)
            if pair is not None:
                out.append(pair.trade_notional)
        return out

    async def _alert(self, text: str) -> None:
        try:
            await self.alerter.send(text)
        except Exception:  # noqa: BLE001
            pass

    async def _close_all(self, reason: str) -> None:
        for name in list(self.positions.keys()):
            pair = next((p for p in self.pairs if p.name == name), None)
            if pair is None:
                continue
            spot = self.books.get(pair.spot_symbol)
            perp = self.books.get(pair.perp_symbol)
            if spot is None or perp is None:
                continue
            pos = self.positions[name]
            close_spot_px = spot.bid if pos.spot_side == Side.BUY else spot.ask
            close_perp_px = perp.ask if pos.perp_side == Side.SELL else perp.bid
            res = await self.executor.close_hedge(pos, close_spot_px, close_perp_px)
            del self.positions[name]
            self.risk.record_pnl(res.realized_pnl_quote)
            self.log.warning("force_close", pair=name, reason=reason, pnl=round(res.realized_pnl_quote, 4))

    async def _evaluate(self) -> None:
        while self.running:
            now = int(time.time() * 1000)
            if self.risk.killed:
                await self._close_all("kill_switch")
                self.log.error("kill_switch_active_no_new_orders")
                await self._alert("⛔ kill switch 已触发:停止开仓并已平掉所有仓位")
                await asyncio.sleep(self.settings.eval_interval_sec)
                continue

            for pair in self.pairs:
                spot = self.books.get(pair.spot_symbol)
                perp = self.books.get(pair.perp_symbol)
                fr = self.funding.get(pair.perp_symbol)
                funding_bps = (fr or 0.0) * 1e4
                result = compute_spread(spot, perp, pair, now, funding_bps=funding_bps)
                if result is None:
                    continue
                await self._on_spread(pair, result, now)

            # 保证金占用监控
            gross = sum(self.pairs_notional())
            ratio = allocator.margin_ratio(
                gross, self.settings.total_capital, self.settings.leverage
            )
            if allocator.check_margin_alert(ratio, self.settings.margin_alert_ratio):
                self.log.warning("margin_alert", used_ratio=round(ratio, 3))
                await self._alert(f"⚠️ 保证金占用率 {ratio:.1%} 超过告警阈值")

            await asyncio.sleep(self.settings.eval_interval_sec)

    async def _on_spread(self, pair: PairConfig, result, now: int) -> None:
        engine = self.engines[pair.name]
        pos = self.positions.get(pair.name)
        has_pos = pos is not None
        # 方向权威来源:持仓时用持仓方向,空仓时用 compute_spread 本 tick 选定方向
        ctx_direction = pos.direction if pos else result.direction
        sig = engine.update(result.net_bps, has_pos, ctx_direction)

        if sig.action == Action.OPEN and not has_pos:
            current = sum(self.pairs_notional())
            decision = self.risk.approve_open(current, pair.trade_notional)
            if not decision.allow:
                self.log.info("open_rejected", pair=pair.name, reason=decision.reason)
                return
            # 半自动确认闸门:require_manual_confirm=True 时不阻塞评估循环,
            # 而是起后台任务等待人工确认(平仓/kill switch 仍按周期全自动运行)。
            if self.settings.require_manual_confirm:
                if pair.name in self._pending:
                    return  # 该 pair 已有待确认机会,忽略重复触发
                self._pending[pair.name] = asyncio.create_task(
                    self._confirm_then_open(pair, result, sig, now)
                )
                return
            await self._open(pair, result, sig, now)
        elif sig.action == Action.CLOSE and has_pos:
            spot = self.books.get(pair.spot_symbol)
            perp = self.books.get(pair.perp_symbol)
            if spot is None or perp is None:
                return
            # 平仓取与持仓方向匹配的对手价:卖出取 bid、买入取 ask
            close_spot_px = spot.bid if pos.spot_side == Side.BUY else spot.ask
            close_perp_px = perp.ask if pos.perp_side == Side.SELL else perp.bid
            res = await self.executor.close_hedge(pos, close_spot_px, close_perp_px)
            del self.positions[pair.name]
            rd = self.risk.record_pnl(res.realized_pnl_quote)
            self.log.warning(
                "CLOSE", pair=pair.name, pnl=round(res.realized_pnl_quote, 4),
                equity=round(self.risk.equity, 2),
            )
            await self._alert(
                f"🔵 平仓 {pair.name} pnl={res.realized_pnl_quote:.4f} equity={self.risk.equity:.2f}"
            )
            if rd.kill:
                self.log.error("max_drawdown_breached_kill", equity=round(self.risk.equity, 2))
                await self._alert("⛔ 触发最大回撤熔断,已置 kill switch")

    async def _open(self, pair: PairConfig, result, sig, now: int) -> None:
        """执行开仓(两腿对冲)并记录仓位/告警。调用前已通过风控与(可选)人工确认。"""
        # 使用 compute_spread 已选定的方向(与 result.spot_price/perp_price 一致)
        res = await self.executor.open_hedge(
            pair, result.direction, result.spot_price, result.perp_price, now
        )
        if res.ok and res.position is not None:
            self.positions[pair.name] = res.position
            # delta 中性校验(非阻断,仅告警):对冲两腿名义严重偏离时提醒
            dcheck = self.risk.check_position_delta(res.position)
            if not dcheck.allow:
                self.log.warning("delta_deviation", pair=pair.name)
                await self._alert(f"⚠️ 仓位 {pair.name} delta 偏离中性超限")
            self.log.warning(
                "OPEN", pair=pair.name, direction=result.direction,
                z=round(sig.zscore, 2), net_bps=round(result.net_bps, 2),
                note=res.error or "",
            )
            await self._alert(
                f"🟢 开仓 {pair.name} {result.direction} z={sig.zscore:.2f} net={result.net_bps:.1f}bps"
            )
        else:
            self.log.warning("open_failed", pair=pair.name, error=res.error)

    async def _confirm_then_open(self, pair: PairConfig, result, sig, now: int) -> None:
        """后台等待人工确认;确认且未超时则开仓,否则作废。不阻塞评估循环。"""
        try:
            if await self._confirm_gate(pair, result, sig):
                # 再次确认此刻仍空仓(等待期间可能已由其他路径处理)
                if pair.name not in self.positions:
                    await self._open(pair, result, sig, now)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 后台任务异常不应影响主循环
            self.log.warning("confirm_open_error", pair=pair.name, error=str(exc))
        finally:
            self._pending.pop(pair.name, None)

    async def _confirm_gate(self, pair: PairConfig, result, sig) -> bool:
        """开仓前的人工确认闸门(仅开仓)。

        - require_manual_confirm=False:直接放行,保持现有全自动行为(向后兼容)。
        - True:发告警并进入待确认;收到确认且未超时返回 True,否则作废并记日志。
        """
        if not self.settings.require_manual_confirm:
            return True
        request_id = f"{pair.name}:{int(time.time() * 1000)}"
        timeout = self.settings.confirm_timeout_sec
        self.log.warning(
            "await_confirm", pair=pair.name, direction=result.direction,
            request_id=request_id, timeout_sec=timeout,
        )
        await self._alert(
            f"⏳ 待确认开仓 {pair.name} {result.direction} "
            f"z={sig.zscore:.2f} net={result.net_bps:.1f}bps — "
            f"回复 confirm 确认(超时 {timeout:.0f}s 作废)id={request_id}"
        )
        try:
            ok = await self.confirm_source.wait_for_confirmation(request_id, timeout)
        except Exception as exc:  # noqa: BLE001 确认源异常按未确认处理，不开仓
            self.log.warning("confirm_source_error", pair=pair.name, error=str(exc))
            ok = False
        if not ok:
            self.log.warning("confirm_discarded", pair=pair.name, request_id=request_id)
            await self._alert(f"🚫 未确认/超时,作废开仓机会 {pair.name} id={request_id}")
        return ok

    async def run(self) -> None:
        if self.live and not self.settings.allow_live:
            raise RuntimeError(
                "live 模式需显式设置 ARB_ALLOW_LIVE=true(且请先在测试网充分验证)"
            )
        symbols: set[str] = set()
        for p in self.pairs:
            symbols.add(p.spot_symbol)
            symbols.add(p.perp_symbol)
        perp_symbols = [p.perp_symbol for p in self.pairs]

        self.log.info(
            "engine_start", mode="live" if self.live else "paper",
            exchange=self.settings.exchange, testnet=self.settings.testnet,
            pairs=[p.name for p in self.pairs],
            per_pair_notional=round(self.alloc.per_pair_notional, 2),
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
        # 取消所有待确认开仓后台任务(避免悬挂 / 停止后再开仓)
        for task in list(self._pending.values()):
            task.cancel()
        self._pending.clear()
        try:
            await self._close_all("shutdown")
        except Exception:  # noqa: BLE001
            pass
        try:
            await self.connector.close()
        except Exception:  # noqa: BLE001
            pass
        self.log.info("engine_stopped", final_equity=round(self.risk.equity, 2))
