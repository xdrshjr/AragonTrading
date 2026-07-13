"""老 SQLite trades 表的 position_pct 幂等补列回归。"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.session import _ensure_trade_risk_columns


async def test_old_trade_table_adds_position_pct_once_and_keeps_old_rows_null(tmp_path):
    db_path = (tmp_path / "old-trades.sqlite3").as_posix()
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE trades ("
                "id INTEGER PRIMARY KEY, run_id INTEGER, ts INTEGER, side VARCHAR(8), "
                "price FLOAT, qty FLOAT, fee FLOAT, pnl FLOAT, "
                "position_after FLOAT, reason VARCHAR(64))"
            ))
            await conn.execute(text(
                "INSERT INTO trades "
                "(id, run_id, ts, side, price, qty, fee, pnl, position_after, reason) "
                "VALUES (1, 1, 1000, 'long', 100, 2, 0, 0, 2, 'old')"
            ))
            await _ensure_trade_risk_columns(conn)
            await _ensure_trade_risk_columns(conn)

            columns = (await conn.execute(text("PRAGMA table_info(trades)"))).all()
            old_pct = (await conn.execute(text(
                "SELECT position_pct FROM trades WHERE id = 1"
            ))).scalar_one()

        assert [row[1] for row in columns].count("position_pct") == 1
        assert old_pct is None
    finally:
        await engine.dispose()
