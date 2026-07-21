"""回测回放引擎:复用 ZScoreSignalEngine,把录制的净价差序列回放成交易与绩效。

近似口径:一笔交易在 OPEN 时记录 entry_net_bps,在 CLOSE 时记录 exit_net_bps,
实现收益 realized_bps = entry_net_bps - exit_net_bps(净价差已含手续费口径,
代表从"宽价差"进场、"窄价差"回归离场所捕获的收敛)。这是回测 MVP 的简化,
真实撮合的滑点/资金费在迭代 3/paper 与实盘中进一步刻画。
"""
from __future__ import annotations

from dataclasses import dataclass

from arb.backtest.metrics import BacktestMetrics, compute_metrics
from arb.marketdata.spread import DIR_LONG_PERP_SHORT_SPOT, DIR_SHORT_PERP_LONG_SPOT
from arb.strategy.signal import Action, ZScoreSignalEngine


@dataclass(frozen=True)
class BacktestTrade:
    open_ts: int
    close_ts: int
    direction: str
    entry_net_bps: float
    exit_net_bps: float
    realized_bps: float


@dataclass(frozen=True)
class BacktestReport:
    metrics: BacktestMetrics
    trades: list[BacktestTrade]


def run_backtest(
    samples: list[tuple[int, float]],
    window: int,
    entry_z: float,
    exit_z: float,
    threshold_bps: float,
) -> BacktestReport:
    """samples: 按时间升序的 (ts_ms, net_bps) 序列。"""
    engine = ZScoreSignalEngine(window, entry_z, exit_z, threshold_bps)
    trades: list[BacktestTrade] = []

    has_pos = False
    direction: str | None = None
    entry_ts = 0
    entry_net = 0.0

    for ts, net_bps in samples:
        # 回测无盘口,方向按净价差符号做信息性推断(不影响 realized_bps 口径)
        open_dir = DIR_SHORT_PERP_LONG_SPOT if net_bps >= 0 else DIR_LONG_PERP_SHORT_SPOT
        sig = engine.update(net_bps, has_pos, direction if has_pos else open_dir)
        if sig.action == Action.OPEN and not has_pos:
            has_pos = True
            direction = sig.direction
            entry_ts = ts
            entry_net = net_bps
        elif sig.action == Action.CLOSE and has_pos:
            realized = entry_net - net_bps
            trades.append(
                BacktestTrade(
                    open_ts=entry_ts,
                    close_ts=ts,
                    direction=direction or "",
                    entry_net_bps=entry_net,
                    exit_net_bps=net_bps,
                    realized_bps=realized,
                )
            )
            has_pos = False
            direction = None

    metrics = compute_metrics([t.realized_bps for t in trades])
    return BacktestReport(metrics=metrics, trades=trades)


def format_report(report: BacktestReport, pair_name: str) -> str:
    m = report.metrics
    lines = [
        f"===== 回测报告: {pair_name} =====",
        f"交易笔数 : {m.num_trades}",
        f"胜率     : {m.win_rate:.1%} ({m.wins}/{m.num_trades})",
        f"累计收益 : {m.total_bps:.2f} bps",
        f"平均每笔 : {m.avg_bps:.2f} bps",
        f"最大回撤 : {m.max_drawdown_bps:.2f} bps",
        f"夏普(简) : {m.sharpe:.2f}",
    ]
    return "\n".join(lines)
