"""Lưu trữ dữ liệu Binance Alpha vào SQLite để backtest."""
import sqlite3
from typing import Iterable, List

from signals import Kline, parse_kline

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    alpha_id TEXT PRIMARY KEY,
    symbol TEXT, name TEXT, chain TEXT, contract TEXT,
    listing_time INTEGER, updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS snapshots (
    ts INTEGER, alpha_id TEXT,
    price REAL, volume24h REAL, count24h INTEGER,
    liquidity REAL, holders INTEGER, market_cap REAL, fdv REAL,
    PRIMARY KEY (ts, alpha_id)
);
CREATE TABLE IF NOT EXISTS bars (
    alpha_id TEXT, interval TEXT, open_time INTEGER,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, quote_volume REAL, trades INTEGER,
    PRIMARY KEY (alpha_id, interval, open_time)
);
CREATE TABLE IF NOT EXISTS alerts (
    ts INTEGER, alpha_id TEXT, symbol TEXT,
    price REAL, rise_pct REAL, vol_ratio REAL, window_volume REAL,
    liquidity REAL, fdv REAL,
    PRIMARY KEY (ts, alpha_id)
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def save_tokens(conn: sqlite3.Connection, tokens: Iterable[dict], ts: int) -> None:
    conn.executemany(
        "INSERT INTO tokens VALUES (?,?,?,?,?,?,?) ON CONFLICT(alpha_id) DO UPDATE SET "
        "symbol=excluded.symbol, chain=excluded.chain, contract=excluded.contract, updated_at=excluded.updated_at",
        [
            (
                t["alphaId"], t["symbol"], t.get("name"), t.get("chainName"),
                t.get("contractAddress"), t.get("listingTime"), ts,
            )
            for t in tokens
        ],
    )
    conn.commit()


def save_snapshots(conn: sqlite3.Connection, tokens: Iterable[dict], ts: int) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (
                ts, t["alphaId"], float(t.get("price") or 0), float(t.get("volume24h") or 0),
                int(float(t.get("count24h") or 0)), float(t.get("liquidity") or 0),
                int(float(t.get("holders") or 0)), float(t.get("marketCap") or 0),
                float(t.get("fdv") or 0),
            )
            for t in tokens
        ],
    )
    conn.commit()


def save_bars(conn: sqlite3.Connection, alpha_id: str, interval: str, raws: Iterable[list]) -> None:
    conn.executemany(
        "INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(alpha_id, interval, open_time) DO UPDATE SET "
        "high=excluded.high, low=excluded.low, close=excluded.close, "
        "volume=excluded.volume, quote_volume=excluded.quote_volume, trades=excluded.trades",
        [(alpha_id, interval) + tuple(parse_kline(r)) for r in raws],
    )


def save_alert(conn: sqlite3.Connection, row: tuple) -> None:
    conn.execute("INSERT OR IGNORE INTO alerts VALUES (?,?,?,?,?,?,?,?,?)", row)
    conn.commit()


def load_bars(conn: sqlite3.Connection, alpha_id: str, interval: str = "1m") -> List[Kline]:
    cur = conn.execute(
        "SELECT open_time, open, high, low, close, volume, quote_volume, trades "
        "FROM bars WHERE alpha_id=? AND interval=? ORDER BY open_time",
        (alpha_id, interval),
    )
    return [Kline(*r) for r in cur]


def list_symbols(conn: sqlite3.Connection, interval: str = "1m") -> List[tuple]:
    cur = conn.execute(
        "SELECT b.alpha_id, COALESCE(t.symbol, b.alpha_id), COUNT(*) "
        "FROM bars b LEFT JOIN tokens t ON t.alpha_id = b.alpha_id "
        "WHERE b.interval=? GROUP BY b.alpha_id",
        (interval,),
    )
    return list(cur)
