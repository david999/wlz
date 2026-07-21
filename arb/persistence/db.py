"""aiosqlite 落库:初始化与写入。"""
from __future__ import annotations

import aiosqlite

from arb.persistence.models import (
    CREATE_SPREADS_INDEX,
    CREATE_SPREADS_TABLE,
    INSERT_SPREAD,
    SpreadRecord,
)


async def init_db(path: str) -> aiosqlite.Connection:
    db = await aiosqlite.connect(path)
    await db.execute(CREATE_SPREADS_TABLE)
    await db.execute(CREATE_SPREADS_INDEX)
    await db.commit()
    return db


async def insert_spread(db: aiosqlite.Connection, rec: SpreadRecord) -> None:
    await db.execute(INSERT_SPREAD, rec.as_row())
    await db.commit()
