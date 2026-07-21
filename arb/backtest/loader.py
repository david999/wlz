"""回测数据加载:从 SQLite spreads 表或 CSV 读取 (ts, net_bps) 序列。"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path


def load_from_db(db_path: str, pair_name: str) -> list[tuple[int, float]]:
    """从迭代 1 落库的 spreads 表按时间升序读取指定 pair 的净价差序列。"""
    if not Path(db_path).exists():
        raise FileNotFoundError(f"数据库不存在: {db_path}(先用 monitor 模式录制数据)")
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT ts, net_bps FROM spreads WHERE pair_name = ? ORDER BY ts ASC",
            (pair_name,),
        ).fetchall()
    finally:
        conn.close()
    return [(int(ts), float(net)) for ts, net in rows]


def load_from_csv(csv_path: str) -> list[tuple[int, float]]:
    """CSV 需包含表头 ts,net_bps。"""
    if not Path(csv_path).exists():
        raise FileNotFoundError(f"CSV 不存在: {csv_path}")
    out: list[tuple[int, float]] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            out.append((int(float(row["ts"])), float(row["net_bps"])))
    out.sort(key=lambda x: x[0])
    return out
