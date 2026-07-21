"""交易编排引擎测试(离线):安全护栏 + paper 编排链路 + kill switch。

隔离策略:
- 用 monkeypatch 把 trading_engine 中的 CCXTConnector / CCXTTradingConnector 替换为
  自建 Fake,构造引擎时完全不触网、不依赖真实交易所实例;
- paper 模式仍使用真实 SimulatedExecutor(确定性撮合)以验证真实编排;
- async 统一用 asyncio.run 驱动。
"""
from __future__ import annotations

import asyncio

import pytest

from arb import trading_engine
from arb.config.models import PairConfig
from arb.config.settings import Settings
from arb.connectors.base import OrderBookSnapshot
from arb.execution.executor import SimulatedExecutor
from arb.execution.models import Side
from arb.marketdata.spread import DIR_SHORT_PERP_LONG_SPOT, SpreadResult
from arb.trading_engine import TradingEngine


class FakeReadOnlyConnector:
    """只读行情假连接器:构造签名与 CCXTConnector 对齐,离线可用。"""

    def __init__(self, exchange_id, api_key=None, secret=None, password=None, testnet=True):
        self.exchange_id = exchange_id
        self.closed = False

    async def watch_order_book(self, symbol):  # pragma: no cover - 编排测试不驱动 run 循环
        raise NotImplementedError

    async def fetch_funding_rate(self, symbol):  # pragma: no cover
        return None

    async def close(self):
        self.closed = True


class FakeTradingConnector(FakeReadOnlyConnector):
    """带下单能力的假连接器:live 护栏测试用(护栏会在下单前抛错)。"""

    async def create_limit_order(self, symbol, side, qty, price):  # pragma: no cover
        raise NotImplementedError

    async def create_market_order(self, symbol, side, qty):  # pragma: no cover
        raise NotImplementedError

    async def fetch_order(self, symbol, order_id):  # pragma: no cover
        raise NotImplementedError

    async def cancel_order(self, symbol, order_id):  # pragma: no cover
        raise NotImplementedError


def _settings(**over) -> Settings:
    base = dict(
        exchange="okx",
        testnet=True,
        api_key=None,
        api_secret=None,
        api_password=None,
        zscore_window=5,
        entry_z=1.0,
        exit_z=0.5,
        slippage_bps=0.0,
        max_position_notional=1000.0,
        max_delta_bps=50.0,
        max_drawdown_pct=5.0,
        total_capital=20000.0,
        leverage=2.0,
        margin_alert_ratio=0.5,
        eval_interval_sec=0.0,
        allow_live=False,
        telegram_token=None,
        telegram_chat_id=None,
    )
    base.update(over)
    # 传入 _env_file=None 阻止读取真实 .env,确保测试与本地环境/环境变量隔离
    return Settings(**base, _env_file=None)


def _pair(notional=100.0) -> PairConfig:
    return PairConfig(
        name="BTC-basis",
        spot_symbol="BTC/USDT",
        perp_symbol="BTC/USDT:USDT",
        threshold_bps=20.0,
        spot_taker_fee_bps=8.0,
        perp_taker_fee_bps=5.0,
        staleness_ms=2000,
        trade_notional=notional,
    )


def _spread(pair: PairConfig, net_bps: float, spot=100.0, perp=100.0) -> SpreadResult:
    """构造一个 short_perp_long_spot 方向、指定 net_bps 的价差结果。"""
    return SpreadResult(
        pair_name=pair.name,
        direction=DIR_SHORT_PERP_LONG_SPOT,
        spot_price=spot,
        perp_price=perp,
        gross_bps=net_bps,
        fee_bps=0.0,
        funding_bps=0.0,
        net_bps=net_bps,
        is_opportunity=net_bps > pair.threshold_bps,
    )


def _snap(symbol, bid, ask, ts) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol=symbol, bid=bid, ask=ask, bid_qty=1.0, ask_qty=1.0, timestamp=ts
    )


# ---------- 安全护栏 ----------

def test_live_without_allow_live_raises(monkeypatch):
    monkeypatch.setattr(trading_engine, "CCXTConnector", FakeReadOnlyConnector)
    monkeypatch.setattr(trading_engine, "CCXTTradingConnector", FakeTradingConnector)
    engine = TradingEngine(_settings(allow_live=False), [_pair()], live=True)
    with pytest.raises(RuntimeError, match="ARB_ALLOW_LIVE"):
        asyncio.run(engine.run())


def test_paper_run_is_unaffected_by_guardrail(monkeypatch):
    monkeypatch.setattr(trading_engine, "CCXTConnector", FakeReadOnlyConnector)
    engine = TradingEngine(_settings(), [_pair()], live=False)
    # 令所有后台循环立即退出,验证 run() 不因护栏抛错并能正常收尾
    engine.running = False
    asyncio.run(engine.run())
    assert engine.running is False
    assert engine.connector.closed is True  # shutdown 关闭了连接器


# ---------- paper 编排链路 ----------

def test_paper_orchestration_open_close_equity(monkeypatch):
    monkeypatch.setattr(trading_engine, "CCXTConnector", FakeReadOnlyConnector)
    pair = _pair()
    engine = TradingEngine(_settings(), [pair], live=False)
    assert isinstance(engine.executor, SimulatedExecutor)  # paper 用模拟撮合

    now = 1
    # 预热:低方差样本,不应开仓
    for v in [0.0, 1.0, 0.0, 1.0, 0.0]:
        asyncio.run(engine._on_spread(pair, _spread(pair, v), now))
    assert pair.name not in engine.positions

    # 尖峰:|z| 远超 entry_z 且 net>threshold -> OPEN 建仓
    asyncio.run(engine._on_spread(pair, _spread(pair, 60.0, spot=100.0, perp=100.0), now))
    assert pair.name in engine.positions
    pos = engine.positions[pair.name]
    assert pos.spot_side == Side.BUY and pos.perp_side == Side.SELL

    # 建仓后喂入回归价差前,准备平仓所需盘口:现货涨到 110、永续持平 100
    engine.books[pair.spot_symbol] = _snap(pair.spot_symbol, bid=110.0, ask=110.0, ts=now)
    engine.books[pair.perp_symbol] = _snap(pair.perp_symbol, bid=100.0, ask=100.0, ts=now)

    equity_before = engine.risk.equity
    # 回归到均值附近 -> |z| <= exit_z -> CLOSE
    asyncio.run(engine._on_spread(pair, _spread(pair, 10.0), now))
    assert pair.name not in engine.positions
    # 现货腿 (110-100)*1 = +10,永续腿持平 0 -> 权益 +10
    assert engine.risk.equity == pytest.approx(equity_before + 10.0, abs=1e-6)


def test_kill_switch_closes_all_and_blocks_new_orders(monkeypatch):
    monkeypatch.setattr(trading_engine, "CCXTConnector", FakeReadOnlyConnector)
    pair = _pair()
    engine = TradingEngine(_settings(), [pair], live=False)

    # 预置一个持仓与对应盘口,供 kill 时强平
    res = asyncio.run(
        engine.executor.open_hedge(pair, DIR_SHORT_PERP_LONG_SPOT, 100.0, 100.0, 1)
    )
    engine.positions[pair.name] = res.position
    engine.books[pair.spot_symbol] = _snap(pair.spot_symbol, 100.0, 100.0, 1)
    engine.books[pair.perp_symbol] = _snap(pair.perp_symbol, 100.0, 100.0, 1)

    engine.risk.trigger_kill()

    calls: list[str] = []
    orig_close_all = engine._close_all

    async def spy(reason):
        calls.append(reason)
        await orig_close_all(reason)
        engine.running = False  # 执行一轮后退出 _evaluate 循环

    monkeypatch.setattr(engine, "_close_all", spy)

    asyncio.run(engine._evaluate())

    # kill 分支调用 _close_all 并平掉全部仓位,且未进入开仓逻辑
    assert calls == ["kill_switch"]
    assert engine.positions == {}
    assert engine.running is False
