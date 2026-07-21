"""纯数据结构:不依赖任何第三方库,便于单元测试直接导入。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PairConfig:
    """一个"同一资产、不同合约"的对冲监控对象。

    典型形态:现货 vs 永续(basis 套利)。
    """

    name: str                    # 监控对象名称,如 "BTC-basis"
    spot_symbol: str             # 现货腿符号,如 "BTC/USDT"
    perp_symbol: str             # 永续腿符号,如 "BTC/USDT:USDT"
    threshold_bps: float         # 净价差超过该阈值(基点)判定为机会
    spot_taker_fee_bps: float    # 现货腿 taker 手续费(基点)
    perp_taker_fee_bps: float    # 永续腿 taker 手续费(基点)
    staleness_ms: int            # 盘口数据超过该时长(毫秒)视为过期
    trade_notional: float = 100.0        # 单腿名义(计价币,如 USDT),用于策略/执行
    spot_exchange: str | None = None     # 现货腿所在交易所(跨所模型 B;None=用全局交易所)
    perp_exchange: str | None = None     # 永续腿所在交易所(跨所模型 B;None=用全局交易所)


@dataclass(frozen=True)
class SymbolsConfig:
    """symbols.yaml 的解析结果。"""

    staleness_ms: int
    pairs: list[PairConfig]
