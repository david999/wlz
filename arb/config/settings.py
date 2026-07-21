"""运行时配置:环境变量(pydantic-settings)+ symbols.yaml 解析。"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

from arb.config.models import PairConfig, SymbolsConfig


class Settings(BaseSettings):
    """从 .env / 环境变量读取(前缀 ARB_)。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ARB_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    exchange: str = "okx"
    testnet: bool = True

    # 只读密钥(可选);不填则仅使用公共行情
    api_key: str | None = None
    api_secret: str | None = None
    api_password: str | None = None

    db_path: str = "arb.db"
    symbols_path: str = "arb/config/symbols.yaml"
    log_level: str = "INFO"

    eval_interval_sec: float = 1.0
    funding_refresh_sec: float = 60.0

    # ---- 运行模式 ----
    # monitor(只读) / backtest(回测) / paper(纸上模拟撮合) / live(真实下单)
    mode: str = "monitor"

    # ---- 策略(z-score 均值回归) ----
    zscore_window: int = 200          # 滚动窗口样本数
    entry_z: float = 2.0              # |z| 超过该值且净价差>阈值 则开仓
    exit_z: float = 0.5               # |z| 回归到该值以下 则平仓

    # ---- 执行 ----
    slippage_bps: float = 2.0         # 模拟撮合滑点(基点)
    order_timeout_sec: float = 5.0    # 限价单等待成交超时(秒),超时撤单

    # ---- 风控 ----
    max_position_notional: float = 1000.0  # 单/总仓位名义上限(计价币)
    max_delta_bps: float = 50.0            # 两腿名义 delta 偏离上限(基点)
    max_drawdown_pct: float = 5.0          # 最大回撤熊断(百分比)

    # ---- 资金 ----
    total_capital: float = 20000.0    # 总本金(计价币)
    leverage: float = 2.0             # 目标杠杆
    margin_alert_ratio: float = 0.5   # 保证金占用率告警阈值

    # ---- 安全开关 ----
    allow_live: bool = False          # 必须显式置 true 才允许 live 真实下单

    # ---- 告警(Telegram,可选) ----
    telegram_token: str | None = None
    telegram_chat_id: str | None = None

    # ---- 回测 ----
    backtest_source: str = "db"       # db | csv
    backtest_csv_path: str | None = None
    backtest_pair: str | None = None  # 仅回测指定 pair(None=第一个)


def load_symbols(path: str) -> SymbolsConfig:
    """解析 symbols.yaml,把全局 staleness_ms 注入每个 PairConfig。"""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    staleness_ms = int(data.get("staleness_ms", 2000))
    pairs: list[PairConfig] = []
    for raw in data.get("pairs", []):
        pairs.append(
            PairConfig(
                name=raw["name"],
                spot_symbol=raw["spot_symbol"],
                perp_symbol=raw["perp_symbol"],
                threshold_bps=float(raw["threshold_bps"]),
                spot_taker_fee_bps=float(raw["spot_taker_fee_bps"]),
                perp_taker_fee_bps=float(raw["perp_taker_fee_bps"]),
                staleness_ms=staleness_ms,
                trade_notional=float(raw.get("trade_notional", 100.0)),
                spot_exchange=raw.get("spot_exchange"),
                perp_exchange=raw.get("perp_exchange"),
            )
        )
    if not pairs:
        raise ValueError(f"未在 {path} 中找到任何 pairs 配置")
    return SymbolsConfig(staleness_ms=staleness_ms, pairs=pairs)
