"""风控规则(纯函数,可测)。"""
from __future__ import annotations


def check_position_limit(
    current_notional: float, add_notional: float, max_notional: float
) -> bool:
    """新增仓位后总名义不超过上限则通过。"""
    return (current_notional + add_notional) <= max_notional


def delta_bps(spot_notional_signed: float, perp_notional_signed: float) -> float:
    """两腿带符号名义的净敞口占总名义的比例(基点)。

    delta 越接近 0 越中性。空腿以负号表示。
    """
    gross = abs(spot_notional_signed) + abs(perp_notional_signed)
    if gross <= 0:
        return 0.0
    net = spot_notional_signed + perp_notional_signed
    return abs(net) / gross * 1e4


def check_delta_neutral(
    spot_notional_signed: float, perp_notional_signed: float, max_delta_bps: float
) -> bool:
    return delta_bps(spot_notional_signed, perp_notional_signed) <= max_delta_bps


def drawdown_pct(equity_peak: float, equity_now: float) -> float:
    """当前回撤百分比(正数)。"""
    if equity_peak <= 0:
        return 0.0
    return max(0.0, (equity_peak - equity_now) / equity_peak * 100.0)


def check_drawdown(equity_peak: float, equity_now: float, max_drawdown_pct: float) -> bool:
    """未触发最大回撤熔断则通过。"""
    return drawdown_pct(equity_peak, equity_now) <= max_drawdown_pct
