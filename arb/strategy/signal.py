"""基于 z-score 的均值回归进出场信号引擎(纯逻辑,可测)。

思路:对净价差 net_bps 维护滚动窗口,计算 z 分数。
- 空仓时:当 |z| >= entry_z 且 net_bps > threshold_bps,发出 OPEN(方向由外部 compute_spread 权威给定并原样回传)。
- 持仓时:当 |z| <= exit_z(价差回归),发出 CLOSE。
- 其余情形 HOLD。

注意:本引擎只决定"何时开/平",不自行推断套利方向;方向必须由调用方
(compute_spread 选定的可成交方向)传入,避免与实际成交价方向不一致。
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from enum import Enum


class Action(str, Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Signal:
    action: Action
    direction: str | None      # 开仓方向;CLOSE/HOLD 为 None
    zscore: float
    net_bps: float
    reason: str


class ZScoreSignalEngine:
    def __init__(
        self,
        window: int,
        entry_z: float,
        exit_z: float,
        threshold_bps: float,
    ) -> None:
        if window < 2:
            raise ValueError("window 至少为 2")
        self.window = window
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.threshold_bps = threshold_bps
        self._buf: deque[float] = deque(maxlen=window)

    def _zscore(self, value: float) -> float | None:
        """样本不足或标准差为 0 时返回 None。"""
        if len(self._buf) < self.window:
            return None
        mean = sum(self._buf) / len(self._buf)
        var = sum((x - mean) ** 2 for x in self._buf) / len(self._buf)
        std = math.sqrt(var)
        if std < 1e-12:      # 近似为 0 的标准差:避免浮点噪声放大出巨量级 z
            return None
        return (value - mean) / std

    def update(self, net_bps: float, has_position: bool, direction: str | None = None) -> Signal:
        """喂入一个新的净价差样本并返回信号。

        direction:
        - 空仓开仓时应传入 compute_spread 本 tick 选定的可成交方向(权威来源),
          OPEN 信号原样回传,确保与实际成交价方向一致;
        - 持仓时传入当前持仓方向,CLOSE/HOLD 原样回传。
        注意:先基于历史窗口计算 z,再把当前样本纳入窗口。
        """
        z = self._zscore(net_bps)
        self._buf.append(net_bps)

        if z is None:
            return Signal(Action.HOLD, None, float("nan"), net_bps, "warmup")

        if not has_position:
            if abs(z) >= self.entry_z and net_bps > self.threshold_bps:
                return Signal(Action.OPEN, direction, z, net_bps, "entry_z_reached")
            return Signal(Action.HOLD, None, z, net_bps, "no_entry")

        # 持仓中:价差回归即平仓
        if abs(z) <= self.exit_z:
            return Signal(Action.CLOSE, direction, z, net_bps, "reverted_to_mean")
        return Signal(Action.HOLD, direction, z, net_bps, "hold_position")
