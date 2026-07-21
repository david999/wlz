"""回测历史价差样本的采集/合成(核心逻辑纯函数,可离线单测)。

用途:为回测提供 (ts_ms, net_bps) 样本序列。fee/funding 口径与实盘监控的
arb.marketdata.spread.compute_spread 一致,但 gross_bps 采用单方向(short_perp)
有符号视角(perp - spot,正=short_perp 机会、负=long_perp 方向),而非
compute_spread 的双方向取优——两者在正基差路径下等价,匹配
ZScoreSignalEngine 的 net_bps > threshold_bps 单方向入场判定:

- fee_bps = 2 * (spot_taker_fee_bps + perp_taker_fee_bps)  # 开+平往返双腿 taker
- net_bps = gross_bps - fee_bps + signed_funding
- 方向化资金费:正基差(perp 贵,short_perp)收资金费(+),
  负基差(spot 贵,long_perp)付资金费(-)。

数据源:ccxt 标准 OHLCV 行 [ts_ms, open, high, low, close, volume],
以 close(索引 4)作为两腿可成交价近似(回测 MVP)。

注意:模块顶层不 import ccxt,取数入口在函数内部惰性 import,
保证 ccxt 缺失/无网络时仍可离线导入与单测纯函数部分。
"""
from __future__ import annotations

import csv

_TS = 0
_CLOSE = 4


def synthesize_from_ohlcv(
    spot_ohlcv: list[list],
    perp_ohlcv: list[list],
    spot_taker_fee_bps: float,
    perp_taker_fee_bps: float,
    funding_bps: float = 0.0,
) -> list[tuple[int, float]]:
    """由现货/永续 OHLCV 合成净价差样本序列(纯函数,不触网)。

    仅对两腿都存在的相同 ts 计算;跳过价格非法(<=0)的行。
    返回按 ts 升序的 (ts_ms, net_bps) 列表。
    """
    fee_bps = 2.0 * (spot_taker_fee_bps + perp_taker_fee_bps)

    perp_by_ts: dict[int, float] = {}
    for row in perp_ohlcv:
        ts = int(row[_TS])
        close = float(row[_CLOSE])
        perp_by_ts[ts] = close

    out: list[tuple[int, float]] = []
    for row in spot_ohlcv:
        ts = int(row[_TS])
        if ts not in perp_by_ts:
            continue
        spot_close = float(row[_CLOSE])
        perp_close = perp_by_ts[ts]
        if spot_close <= 0 or perp_close <= 0:
            continue
        mid = (perp_close + spot_close) / 2.0
        if mid <= 0:
            continue
        gross_bps = (perp_close - spot_close) / mid * 1e4
        signed_funding = funding_bps if gross_bps >= 0 else -funding_bps
        net_bps = gross_bps - fee_bps + signed_funding
        out.append((ts, float(net_bps)))

    out.sort(key=lambda x: x[0])
    return out


def export_to_csv(samples: list[tuple[int, float]], csv_path: str) -> None:
    """把样本导出为 CSV,列为 ts,net_bps,可被 loader.load_from_csv 读取。"""
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ts", "net_bps"])
        for ts, net_bps in samples:
            writer.writerow([int(ts), float(net_bps)])


def fetch_ohlcv(
    exchange_id: str,
    symbol: str,
    timeframe: str = "1h",
    limit: int = 500,
    since: int | None = None,
) -> list[list]:
    """用 ccxt 拉取单个标的历史 K 线(可选取数入口,需联网)。

    ccxt 缺失时抛 RuntimeError;交易所不支持时抛 ValueError;
    网络/接口错误统一包成 RuntimeError,便于上层优雅跳过。
    """
    try:
        import ccxt
    except ImportError as e:  # ccxt 缺失:优雅报错,不影响纯函数单测
        raise RuntimeError("需要安装 ccxt 才能拉取历史 K 线:pip install ccxt") from e

    if exchange_id not in getattr(ccxt, "exchanges", []):
        raise ValueError(f"ccxt 不支持交易所: {exchange_id}")

    exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    try:
        return exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
    except Exception as e:  # 无网络/接口异常:统一优雅报错
        raise RuntimeError(f"拉取 OHLCV 失败({exchange_id} {symbol}): {e}") from e
    finally:
        close = getattr(exchange, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def synthesize_from_exchange(
    exchange_id: str,
    spot_symbol: str,
    perp_symbol: str,
    spot_taker_fee_bps: float,
    perp_taker_fee_bps: float,
    funding_bps: float = 0.0,
    timeframe: str = "1h",
    limit: int = 500,
    since: int | None = None,
) -> list[tuple[int, float]]:
    """联网拉取现货+永续 OHLCV 并合成净价差样本(薄封装,不参与离线单测)。"""
    spot_ohlcv = fetch_ohlcv(exchange_id, spot_symbol, timeframe, limit, since)
    perp_ohlcv = fetch_ohlcv(exchange_id, perp_symbol, timeframe, limit, since)
    return synthesize_from_ohlcv(
        spot_ohlcv, perp_ohlcv, spot_taker_fee_bps, perp_taker_fee_bps, funding_bps
    )
