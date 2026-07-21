"""半自动逐单确认闸门测试(离线,不触网)。

覆盖:
- 确认源解析/超时逻辑(TelegramConfirmationSource + Fake alerter);
- 开关开启 + 确认 -> 正常开仓;
- 开关开启 + 未确认/超时 -> 作废,不开仓;
- 开关关闭 -> 行为与既有全自动一致(不经过确认源)。

async 测试统一用 asyncio.run 驱动,避免依赖 pytest-asyncio 插件。
"""
import asyncio

from arb.config.models import PairConfig
from arb.config.settings import Settings
from arb.marketdata.spread import DIR_SHORT_PERP_LONG_SPOT, SpreadResult
from arb.monitoring.confirm import (
    FakeConfirmationSource,
    TelegramConfirmationSource,
    decide,
)
from arb.strategy.signal import Action, Signal
from arb.trading_engine import TradingEngine


def _pair(notional=100.0):
    return PairConfig(
        name="BTC-basis", spot_symbol="BTC/USDT", perp_symbol="BTC/USDT:USDT",
        threshold_bps=20.0, spot_taker_fee_bps=8.0, perp_taker_fee_bps=5.0,
        staleness_ms=2000, trade_notional=notional,
    )


def _open_result(pair):
    """构造一个净价差机会(短永续/多现货方向),价格 100。"""
    return SpreadResult(
        pair_name=pair.name, direction=DIR_SHORT_PERP_LONG_SPOT,
        spot_price=100.0, perp_price=100.0,
        gross_bps=60.0, fee_bps=26.0, funding_bps=0.0,
        net_bps=34.0, is_opportunity=True,
    )


class _OpenSignalEngine:
    """桩信号引擎:恒定返回 OPEN,把方向原样回传(隔离 z-score 预热复杂度)。"""

    def update(self, net_bps, has_position, direction=None):
        return Signal(Action.OPEN, direction, 3.0, net_bps, "test_open")


def _build_engine(confirm_source, *, require_manual_confirm):
    settings = Settings(
        require_manual_confirm=require_manual_confirm,
        confirm_timeout_sec=1.0,
    )
    engine = TradingEngine(settings, [_pair()], live=False, confirm_source=confirm_source)
    engine.engines[_pair().name] = _OpenSignalEngine()  # 强制 OPEN 信号
    return engine


# ---- 确认源纯逻辑 ----
def test_decide_confirm_reject_and_irrelevant():
    assert decide("confirm") is True
    assert decide("/confirm BTC-basis:123") is True
    assert decide("确认") is True
    assert decide("reject") is False
    assert decide("取消") is False
    assert decide("hello world") is None
    assert decide("") is None


class _StubAlerter:
    """离线告警桩:send 记录文本,fetch_updates 返回预设批次。"""

    enabled = False

    def __init__(self, updates=None):
        self._updates = updates or []
        self.sent = []

    async def send(self, text):
        self.sent.append(text)
        return True

    async def fetch_updates(self, offset=None):  # noqa: ARG002
        return self._updates


def test_telegram_source_confirm_from_update():
    alerter = _StubAlerter([{"update_id": 1, "message": {"text": "confirm"}}])
    src = TelegramConfirmationSource(alerter, poll_interval=0.01)
    assert asyncio.run(src.wait_for_confirmation("BTC-basis:1", 1.0)) is True


def test_telegram_source_reject_via_callback():
    alerter = _StubAlerter([{"update_id": 5, "callback_query": {"data": "reject"}}])
    src = TelegramConfirmationSource(alerter, poll_interval=0.01)
    assert asyncio.run(src.wait_for_confirmation("BTC-basis:1", 1.0)) is False


def test_telegram_source_timeout_returns_false():
    alerter = _StubAlerter([])  # 无任何更新 -> 超时作废
    src = TelegramConfirmationSource(alerter, poll_interval=0.01)
    assert asyncio.run(src.wait_for_confirmation("BTC-basis:1", 0.05)) is False


class _OffsetStubAlerter:
    """尊重 offset 的告警桩:仅返回 update_id >= offset 的更新(近似真实 getUpdates)。"""

    enabled = False

    def __init__(self, updates):
        self._updates = list(updates)

    async def send(self, text):  # noqa: ARG002
        return True

    async def fetch_updates(self, offset=None):
        if offset is None:
            return list(self._updates)
        return [u for u in self._updates if u.get("update_id", 0) >= offset]


def test_telegram_source_flushes_stale_messages():
    """等待开始前已存在的 confirm 属陈旧消息,应被丢弃,不得自动放行。"""
    alerter = _OffsetStubAlerter([{"update_id": 7, "message": {"text": "confirm"}}])
    src = TelegramConfirmationSource(alerter, poll_interval=0.01)
    assert asyncio.run(src.wait_for_confirmation("BTC-basis:1", 0.05)) is False


# ---- 引擎确认闸门 ----
def _drive_open(engine, pair):
    """运行 _on_spread,并在有后台确认任务时等待其完成。"""
    async def _run():
        await engine._on_spread(pair, _open_result(pair), now=0)
        pending = list(engine._pending.values())
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    asyncio.run(_run())


def test_confirmed_opens_position():
    fake = FakeConfirmationSource(approve=True)
    engine = _build_engine(fake, require_manual_confirm=True)
    pair = engine.pairs[0]
    _drive_open(engine, pair)
    assert pair.name in engine.positions          # 确认后如常开仓
    assert fake.requests                            # 确认源被征询过
    assert engine._pending == {}                    # 后台任务已清理


def test_timeout_or_rejected_discards_opportunity():
    fake = FakeConfirmationSource(approve=False)   # 模拟未确认/超时
    engine = _build_engine(fake, require_manual_confirm=True)
    pair = engine.pairs[0]
    _drive_open(engine, pair)
    assert pair.name not in engine.positions        # 机会被作废,不开仓
    assert fake.requests                             # 确实进入了待确认
    assert engine._pending == {}                    # 后台任务已清理


def test_switch_off_keeps_full_auto_unchanged():
    fake = FakeConfirmationSource(approve=False)   # 若被调用则会阻断开仓
    engine = _build_engine(fake, require_manual_confirm=False)
    pair = engine.pairs[0]
    _drive_open(engine, pair)
    assert pair.name in engine.positions            # 全自动:直接开仓
    assert fake.requests == []                       # 开关关闭时不经过确认源
    assert engine._pending == {}                     # 全自动路径不产生后台任务


def test_confirm_gate_does_not_block_eval_loop():
    """确认等待期间 _on_spread 应立即返回(机会转入后台),不阻塞评估循环。"""
    fake = FakeConfirmationSource(approve=True, delay=5.0)  # 慢确认
    engine = _build_engine(fake, require_manual_confirm=True)
    pair = engine.pairs[0]

    async def _run():
        # 限时 0.5s 内必须返回,否则说明闸门阻塞了循环
        await asyncio.wait_for(
            engine._on_spread(pair, _open_result(pair), now=0), timeout=0.5
        )
        opened = pair.name in engine.positions
        pending = pair.name in engine._pending
        tasks = list(engine._pending.values())
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return opened, pending

    opened_now, pending = asyncio.run(_run())
    assert opened_now is False    # 未同步等待确认,故此刻尚未开仓
    assert pending is True         # 机会已进入后台待确认队列
