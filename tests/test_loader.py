"""回测数据加载测试:load_from_csv / load_from_db(纯离线,用 tmp 目录)。

覆盖:
- CSV/DB 均返回 (ts_ms, net_bps) 元组序列;
- 时间严格升序(即使源数据乱序);
- DB 按 pair_name 过滤;
- 缺失文件抛 FileNotFoundError。
"""
from __future__ import annotations

import csv
import sqlite3

import pytest

from arb.backtest.loader import load_from_csv, load_from_db

# 迭代 1 落库的 spreads 表结构
SPREADS_DDL = """
CREATE TABLE spreads (
    ts INTEGER,
    pair_name TEXT,
    direction TEXT,
    spot_price REAL,
    perp_price REAL,
    gross_bps REAL,
    net_bps REAL,
    funding_rate REAL,
    is_opportunity INTEGER
)
"""


def _write_csv(path, rows: list[tuple[int, float]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "net_bps"])
        for ts, net in rows:
            w.writerow([ts, net])


def _make_db(path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(SPREADS_DDL)
        conn.executemany(
            "INSERT INTO spreads (ts, pair_name, direction, spot_price, perp_price, "
            "gross_bps, net_bps, funding_rate, is_opportunity) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def test_load_from_csv_sorted_ascending(tmp_path):
    csv_path = tmp_path / "hist.csv"
    # 故意乱序写入
    _write_csv(csv_path, [(3000, 33.0), (1000, 11.0), (2000, 22.0)])
    out = load_from_csv(str(csv_path))
    assert out == [(1000, 11.0), (2000, 22.0), (3000, 33.0)]
    # 元素类型:ts 为 int,net 为 float
    for ts, net in out:
        assert isinstance(ts, int)
        assert isinstance(net, float)


def test_load_from_csv_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_from_csv(str(tmp_path / "nope.csv"))


def test_load_from_db_sorted_and_filtered(tmp_path):
    db_path = tmp_path / "arb.db"
    rows = [
        # 目标 pair,乱序 ts
        (3000, "BTC-basis", "short_perp_long_spot", 100.3, 101.3, 40.0, 33.0, 0.0001, 1),
        (1000, "BTC-basis", "short_perp_long_spot", 100.1, 101.1, 20.0, 11.0, 0.0001, 0),
        (2000, "BTC-basis", "short_perp_long_spot", 100.2, 101.2, 30.0, 22.0, 0.0001, 1),
        # 另一个 pair,应被过滤掉
        (1500, "ETH-basis", "long_perp_short_spot", 50.0, 49.0, 10.0, 99.0, -0.0002, 1),
    ]
    _make_db(db_path, rows)

    out = load_from_db(str(db_path), "BTC-basis")
    assert out == [(1000, 11.0), (2000, 22.0), (3000, 33.0)]
    for ts, net in out:
        assert isinstance(ts, int)
        assert isinstance(net, float)

    # 过滤生效:ETH 的 net_bps=99.0 不应出现
    assert all(net != 99.0 for _, net in out)


def test_load_from_db_unknown_pair_returns_empty(tmp_path):
    db_path = tmp_path / "arb.db"
    _make_db(
        db_path,
        [(1000, "BTC-basis", "short_perp_long_spot", 100.0, 101.0, 20.0, 11.0, 0.0, 0)],
    )
    assert load_from_db(str(db_path), "NOPE") == []


def test_load_from_db_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_from_db(str(tmp_path / "missing.db"), "BTC-basis")
