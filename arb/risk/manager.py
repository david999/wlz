"""有状态风控管理器 + kill switch。

职责:
- 开仓前审批(仓位上限、delta 中性)。
- 跟踪权益/峰值,触发最大回撤熔断 -> 置 kill switch。
- kill switch 一旦触发,拒绝一切新开仓,并要求上层平掉现有仓位。
"""
from __future__ import annotations

from dataclasses import dataclass

from arb.execution.models import Position, Side
from arb.risk import rules


@dataclass(frozen=True)
class RiskDecision:
    allow: bool
    reason: str
    kill: bool = False


class RiskManager:
    def __init__(
        self,
        max_position_notional: float,
        max_delta_bps: float,
        max_drawdown_pct: float,
        initial_equity: float,
    ) -> None:
        self.max_position_notional = max_position_notional
        self.max_delta_bps = max_delta_bps
        self.max_drawdown_pct = max_drawdown_pct
        self.equity = initial_equity
        self.peak_equity = initial_equity
        self.killed = False

    def approve_open(self, current_notional: float, add_notional: float) -> RiskDecision:
        if self.killed:
            return RiskDecision(False, "kill_switch_active", kill=True)
        if not rules.check_position_limit(
            current_notional, add_notional, self.max_position_notional
        ):
            return RiskDecision(False, "position_limit_exceeded")
        return RiskDecision(True, "ok")

    def check_position_delta(self, pos: Position) -> RiskDecision:
        """校验一个已建对冲仓位的 delta 中性度。"""
        spot_signed = (1 if pos.spot_side == Side.BUY else -1) * pos.spot_qty * pos.entry_spot_price
        perp_signed = (1 if pos.perp_side == Side.BUY else -1) * pos.perp_qty * pos.entry_perp_price
        if not rules.check_delta_neutral(spot_signed, perp_signed, self.max_delta_bps):
            return RiskDecision(False, "delta_deviation")
        return RiskDecision(True, "ok")

    def record_pnl(self, pnl_quote: float) -> RiskDecision:
        """结算一笔盈亏,更新权益/峰值,必要时触发熔断。"""
        self.equity += pnl_quote
        self.peak_equity = max(self.peak_equity, self.equity)
        if not rules.check_drawdown(self.peak_equity, self.equity, self.max_drawdown_pct):
            self.killed = True
            return RiskDecision(False, "max_drawdown_breached", kill=True)
        return RiskDecision(True, "ok")

    def trigger_kill(self, reason: str = "manual") -> None:
        self.killed = True

    def reset_kill(self) -> None:
        self.killed = False
