"""spreads 表结构与记录数据结构(纯 stdlib)。"""
from __future__ import annotations

from dataclasses import dataclass

CREATE_SPREADS_TABLE = """
CREATE TABLE IF NOT EXISTS spreads (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             INTEGER NOT NULL,      -- 毫秒时间戳
    pair_name      TEXT    NOT NULL,
    direction      TEXT    NOT NULL,
    spot_price     REAL    NOT NULL,
    perp_price     REAL    NOT NULL,
    gross_bps      REAL    NOT NULL,
    net_bps        REAL    NOT NULL,
    funding_rate   REAL,                  -- 最新资金费率(小数),可为空
    is_opportunity INTEGER NOT NULL       -- 1=机会 0=否
);
"""

CREATE_SPREADS_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_spreads_pair_ts ON spreads (pair_name, ts);"
)

INSERT_SPREAD = """
INSERT INTO spreads
    (ts, pair_name, direction, spot_price, perp_price, gross_bps, net_bps, funding_rate, is_opportunity)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
"""


@dataclass(frozen=True)
class SpreadRecord:
    ts: int
    pair_name: str
    direction: str
    spot_price: float
    perp_price: float
    gross_bps: float
    net_bps: float
    funding_rate: float | None
    is_opportunity: int

    def as_row(self) -> tuple:
        return (
            self.ts,
            self.pair_name,
            self.direction,
            self.spot_price,
            self.perp_price,
            self.gross_bps,
            self.net_bps,
            self.funding_rate,
            self.is_opportunity,
        )
