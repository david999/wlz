"""回测绩效指标(纯函数,可测)。

输入为每笔交易的收益(基点)。产出:交易数、胜率、累计/平均收益、
最大回撤(基于累计基点净值曲线)、简化夏普(每笔收益的 mean/std)。
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BacktestMetrics:
    num_trades: int
    wins: int
    win_rate: float
    total_bps: float
    avg_bps: float
    max_drawdown_bps: float
    sharpe: float


def equity_curve_bps(trade_returns_bps: list[float]) -> list[float]:
    """累计基点净值曲线(从 0 开始)。"""
    curve: list[float] = []
    acc = 0.0
    for r in trade_returns_bps:
        acc += r
        curve.append(acc)
    return curve


def max_drawdown_bps(curve: list[float]) -> float:
    """净值曲线的最大回撤(正数,基点)。"""
    peak = 0.0
    max_dd = 0.0
    for v in curve:
        peak = max(peak, v)
        max_dd = max(max_dd, peak - v)
    return max_dd


def compute_metrics(trade_returns_bps: list[float]) -> BacktestMetrics:
    n = len(trade_returns_bps)
    if n == 0:
        return BacktestMetrics(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)
    wins = sum(1 for r in trade_returns_bps if r > 0)
    total = sum(trade_returns_bps)
    avg = total / n
    curve = equity_curve_bps(trade_returns_bps)
    mdd = max_drawdown_bps(curve)
    if n >= 2:
        mean = avg
        var = sum((r - mean) ** 2 for r in trade_returns_bps) / (n - 1)
        std = math.sqrt(var)
        sharpe = (mean / std) * math.sqrt(n) if std > 0 else 0.0
    else:
        sharpe = 0.0
    return BacktestMetrics(
        num_trades=n,
        wins=wins,
        win_rate=wins / n,
        total_bps=total,
        avg_bps=avg,
        max_drawdown_bps=mdd,
        sharpe=sharpe,
    )
