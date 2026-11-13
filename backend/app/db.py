"""SQLite 连接管理。

单机单进程场景，不必上连接池：每次请求开一个新连接，SQLite 打开成本可以忽略，
配合 WAL 模式读写互不阻塞。事务用 with tx() 包一层，异常自动回滚。
"""
import os
import sqlite3
from contextlib import contextmanager

from .config import Config

SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def connect():
    os.makedirs(os.path.dirname(Config.DB_PATH), exist_ok=True)
    con = sqlite3.connect(Config.DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 15000")
    return con


@contextmanager
def tx():
    """写事务。"""
    con = connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


@contextmanager
def reader():
    """只读查询。"""
    con = connect()
    try:
        yield con
    finally:
        con.close()


def _cols(con, table):
    return {r["name"] for r in con.execute("PRAGMA table_info(%s)" % table)}


def _drop_legacy(con):
    names = {r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}

    # 带历史快照的旧结构：历史数据不再使用，整张表丢弃
    if "snapshots" in names:
        con.execute("DROP TABLE snapshots")

    # books 从 first_seen/last_seen 的旧结构改成了 updated_at，列对不上就重建
    if "books" in names and "updated_at" not in _cols(con, "books"):
        con.execute("DROP TABLE books")
        if "entries" in names:
            con.execute("DROP TABLE entries")

    if "runs" in names:                     # 旧 runs 缺列时补上，避免 INSERT 报错
        con.execute("DROP INDEX IF EXISTS ux_runs_batch")   # 老结构按批次去重的唯一索引
        cols = _cols(con, "runs")
        for col, ddl in (("source", "TEXT"), ("status", "TEXT DEFAULT 'ok'"),
                         ("boards", "TEXT"), ("cats", "INTEGER DEFAULT 0"),
                         ("books", "INTEGER DEFAULT 0"), ("note", "TEXT")):
            if col not in cols:
                con.execute("ALTER TABLE runs ADD COLUMN %s %s" % (col, ddl))

    # entries 补精确字数列（早期只有四舍五入后的 words，万位小数）
    if "entries" in names:
        cols = _cols(con, "entries")
        if "words_exact" not in cols:
            con.execute("ALTER TABLE entries ADD COLUMN words_exact INTEGER")

    # books 补详情页字段（加入书架人数 / 书友 / 评分 / 催更），CREATE TABLE IF NOT EXISTS 不会补列
    if "books" in names:
        cols = _cols(con, "books")
        for col, ddl in (("shelf", "TEXT"), ("shelf_num", "INTEGER"),
                         ("fans", "TEXT"), ("fans_num", "INTEGER"),
                         ("score", "REAL"), ("ratings", "INTEGER"),
                         ("urge", "INTEGER"), ("detail_at", "TEXT")):
            if col not in cols:
                con.execute("ALTER TABLE books ADD COLUMN %s %s" % (col, ddl))


def init():
    """建表；旧结构自动升级。"""
    os.makedirs(os.path.dirname(Config.DB_PATH), exist_ok=True)
    con = connect()
    try:
        con.execute("PRAGMA journal_mode = WAL")
        _drop_legacy(con)
        with open(SCHEMA, encoding="utf-8") as f:
            con.executescript(f.read())
        con.commit()
    finally:
        con.close()


def meta(k, default=""):
    with reader() as con:
        row = con.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
        return row["v"] if row else default


def revision():
    """入库版本号，变化即代表数据换了。"""
    return meta("revision", "0")


def stats():
    with reader() as con:
        one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "entries": one("SELECT count(*) FROM entries"),
            "books": one("SELECT count(*) FROM books"),
            "boards": one("SELECT count(*) FROM boards"),
            "cats": one("SELECT count(*) FROM categories"),
            "runs": one("SELECT count(*) FROM runs"),
            "collected_at": (con.execute(
                "SELECT v FROM meta WHERE k = 'collected_at'").fetchone() or [""])[0],
            "size": os.path.getsize(Config.DB_PATH) if os.path.exists(Config.DB_PATH) else 0,
        }
