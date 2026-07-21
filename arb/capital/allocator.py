"""资金管理(纯函数,可测)。

三块能力:
1. 名义分配:在总本金 * 杠杆构成的可用购买力下,把可开名义均分到各监控对象。
2. 保证金监控:根据当前对冲仓位的总名义与杠杆,估算保证金占用率并给出告警。
3. 跨所再平衡建议:根据各交易所可用余额与目标占比,给出资金划转建议(信息性)。

说明:本模块只做估算与建议,不发起任何真实划转。真实划转在迭代 6/实盘中
由用户在交易所侧手动确认或通过带划转权限的连接器执行。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Allocation:
    per_pair_notional: float   # 每个对象单腿可开名义(计价币)
    max_total_notional: float  # 全部对象合计可开名义上限


def allocate(total_capital: float, leverage: float, num_pairs: int) -> Allocation:
    """把 total_capital * leverage 的购买力均分到 num_pairs 个对象。"""
    if total_capital <= 0 or leverage <= 0 or num_pairs <= 0:
        return Allocation(0.0, 0.0)
    max_total = total_capital * leverage
    return Allocation(per_pair_notional=max_total / num_pairs, max_total_notional=max_total)


def required_margin(notional: float, leverage: float) -> float:
    """按杠杆估算某笔名义所需保证金。"""
    if leverage <= 0:
        return notional
    return notional / leverage


def margin_ratio(gross_notional: float, total_capital: float, leverage: float) -> float:
    """保证金占用率 = 已用保证金 / 总本金(0~1+)。"""
    if total_capital <= 0:
        return 0.0
    return required_margin(gross_notional, leverage) / total_capital


def check_margin_alert(ratio: float, alert_ratio: float) -> bool:
    """占用率达到/超过告警阈值则返回 True。"""
    return ratio >= alert_ratio


@dataclass(frozen=True)
class RebalanceAdvice:
    from_exchange: str
    to_exchange: str
    amount: float
    reason: str


def rebalance_advice(
    balances: dict[str, float],
    tolerance_ratio: float = 0.1,
) -> list[RebalanceAdvice]:
    """给出把各交易所余额拉平到均值的划转建议。

    balances: {exchange_id: available_quote}。
    tolerance_ratio: 相对均值的容忍带,偏离超过该比例才建议划转。
    """
    if len(balances) < 2:
        return []
    total = sum(balances.values())
    if total <= 0:
        return []
    target = total / len(balances)
    band = target * tolerance_ratio

    surplus = sorted(
        ((ex, bal - target) for ex, bal in balances.items() if bal - target > band),
        key=lambda x: x[1],
        reverse=True,
    )
    deficit = sorted(
        ((ex, target - bal) for ex, bal in balances.items() if target - bal > band),
        key=lambda x: x[1],
        reverse=True,
    )

    advice: list[RebalanceAdvice] = []
    i = j = 0
    surplus = [list(s) for s in surplus]
    deficit = [list(d) for d in deficit]
    while i < len(surplus) and j < len(deficit):
        move = min(surplus[i][1], deficit[j][1])
        if move > 0:
            advice.append(
                RebalanceAdvice(
                    from_exchange=surplus[i][0],
                    to_exchange=deficit[j][0],
                    amount=move,
                    reason="rebalance_to_mean",
                )
            )
            surplus[i][1] -= move
            deficit[j][1] -= move
        if surplus[i][1] <= band:
            i += 1
        if deficit[j][1] <= band:
            j += 1
    return advice
