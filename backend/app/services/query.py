"""查询层：所有对外接口的数据都从这里出。

库里只有一份当前快照，没有历史批次，查询因此非常简单：
entries 按 (board_key, cat, rank) 已经有索引，直接扫。
唯一的例外是「近 24 小时新增收藏」—— 那个要翻 shelf_history 做差。
"""
import time

from .. import db

# 前端沿用的压缩字段名，和旧版 data.js 保持一致
COLS = (" e.cat, e.bid, e.rank AS r, e.words AS w, e.words_exact AS wx,"
        " e.collect_num AS n, e.badge AS bg,"
        " e.label AS lb, e.last_chapter AS lc, e.update_date AS d,"
        " b.title AS t, b.author AS a, b.cat2 AS c2, b.cat3 AS c3, b.intro AS intro,"
        " b.shelf AS sh, b.shelf_num AS shn, b.fans AS fans, b.score AS sc")

# 「收藏」排序口径：优先用详情页的加入书架人数，没采到的退回榜单热度
N_SORT = "COALESCE(NULLIF(b.shelf_num, 0), e.collect_num)"
FROM_BOOKS = " FROM entries e JOIN books b ON b.bid = e.bid"

# dataset 缓存：入库版本号不变就直接返回上次拼好的对象
_DS = {"rev": None, "data": None}


def _slim(row, intro_max=150):
    return {
        "r": row["r"] or 0,
        "bid": str(row["bid"]),
        "t": row["t"] or "",
        "a": row["a"] or "",
        "c2": row["c2"] or "",
        "c3": row["c3"] or "",
        "lb": row["lb"] or "",
        "w": round(row["w"] or 0, 1),
        "wx": row["wx"] or 0,          # 精确字数（0 表示老数据没有）
        "n": row["n"] or 0,            # 榜单热度（App 列表接口的 num）
        "sh": row["sh"] or "",         # 加入书架人数，App 原样显示：14.3万
        "shn": row["shn"] or 0,        # 上者折成的整数，用于排序
        "fans": row["fans"] or "",
        "sc": row["sc"] or 0,
        "bg": row["bg"] or "",
        "lc": row["lc"] or 0,
        "d": row["d"] or "",
        "in": (row["intro"] or "")[:intro_max],
    }


def _stamp(con):
    row = con.execute("SELECT v FROM meta WHERE k = 'collected_at'").fetchone()
    return (row["v"] if row else "") or ""


def dataset():
    """整包数据，结构与旧版 data.js 一致，前端主页面用它。"""
    rev = db.revision()
    if _DS["rev"] == rev and _DS["data"] is not None:
        return _DS["data"]

    with db.reader() as con:
        boards = [dict(r) for r in con.execute("SELECT * FROM boards ORDER BY key")]
        out = {"collected_at": _stamp(con), "platform": "", "boards": {}}

        prow = con.execute("SELECT v FROM meta WHERE k = 'platform'").fetchone()
        out["platform"] = (prow["v"] if prow else "") or ""

        for b in boards:
            key = b["key"]
            cats = {}
            for row in con.execute(
                    "SELECT" + COLS + FROM_BOOKS +
                    " WHERE e.board_key = ? ORDER BY e.cat, e.rank", (key,)):
                cats.setdefault(row["cat"], []).append(_slim(row))
            if not cats:
                continue

            cids = {r["name"]: r["cid"] for r in con.execute(
                "SELECT name, cid FROM categories WHERE board_key = ?", (key,))}

            # 「全部」既是分类也是总榜，两处都要，保持和旧 data.js 一致
            total = cats.get("全部") or (cats[sorted(cats)[0]] if cats else [])
            out["boards"][b["name"]] = {
                "name": b["name"],
                "cid": b["cid"] or "",
                "push": b["push_name"] or "",
                "books": total,
                "cats": {k: {"cid": cids.get(k) or "", "src": k, "books": v}
                         for k, v in cats.items()},
            }

    _DS["rev"], _DS["data"] = rev, out
    return out


def board_list():
    with db.reader() as con:
        stamp = _stamp(con)
        out = []
        for b in con.execute("SELECT * FROM boards ORDER BY key"):
            cats = con.execute(
                "SELECT count(*) FROM categories WHERE board_key = ?", (b["key"],)).fetchone()[0]
            books = con.execute(
                "SELECT count(*) FROM entries WHERE board_key = ? AND cat = '全部'",
                (b["key"],)).fetchone()[0]
            out.append({
                "key": b["key"], "name": b["name"], "cid": b["cid"] or "",
                "push": b["push_name"] or "",
                "cats": cats, "books": books,
                "collected_at": b["updated_at"] or stamp,
            })
        return out


def board_cats(key):
    with db.reader() as con:
        return [dict(r) for r in con.execute(
            "SELECT name, cid FROM categories WHERE board_key = ? ORDER BY name", (key,))]


def board_books(key, cat="全部", q="", sort="rank", page=1, size=50):
    """分页查询。前端一次性拿全量走 dataset，这里给移动端 / 外部调用留口子。"""
    with db.reader() as con:
        where = "e.board_key = ? AND e.cat = ?"
        args = [key, cat]
        if q:
            where += " AND (b.title LIKE ? OR b.author LIKE ?)"
            args += ["%" + q + "%", "%" + q + "%"]

        order = {"rank": "e.rank ASC", "collect": N_SORT + " DESC",
                 "words": "e.words DESC", "chapter": "e.last_chapter DESC"}.get(sort, "e.rank ASC")

        total = con.execute("SELECT count(*)" + FROM_BOOKS + " WHERE " + where, args).fetchone()[0]
        size = max(1, min(int(size or 50), 500))
        page = max(1, int(page or 1))
        rows = con.execute(
            "SELECT" + COLS + FROM_BOOKS + " WHERE " + where +
            " ORDER BY " + order + " LIMIT ? OFFSET ?",
            args + [size, (page - 1) * size])
        return {"total": total, "page": page, "size": size,
                "items": [_slim(r) for r in rows]}


def _shelf_delta(con, bid, shelf_num, now):
    """当前书架数 - 约 24 小时前的快照。

    QQ阅读 没有「新增收藏」这种字段，只能拿两次快照相减，所以要有一份
    至少一天前的历史。取「18 小时前及更早」里最新的一条：

    - 正常每天采一次，跨度就在 24 小时上下
    - 某天漏采了，跨度会变成 36 / 48 / 72 小时，那就如实把小时数带出去，
      页面上写「近 48 小时」，而不是硬说成 24 小时
    - 超过 4 天的历史不认：再旧就和「近 24 小时」没什么关系了

    返回 (delta, base_at, hours)；没基准返回 (None, "", 0.0)。
    """
    if not shelf_num or shelf_num <= 0:
        return None, "", 0.0
    hi = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 18 * 3600))
    lo = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 96 * 3600))
    row = con.execute(
        "SELECT at, shelf_num FROM shelf_history"
        " WHERE bid = ? AND at <= ? AND at >= ? ORDER BY at DESC LIMIT 1",
        (bid, hi, lo),
    ).fetchone()
    if not row:
        return None, "", 0.0
    try:
        base = time.mktime(time.strptime(row["at"], "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError, OverflowError):
        return None, "", 0.0
    return shelf_num - (row["shelf_num"] or 0), row["at"], round((now - base) / 3600, 1)


def book_detail(bid):
    """单本书的当前状态 + 它在各分类里的名次（同一个库里的多榜位置）。"""
    with db.reader() as con:
        b = con.execute("SELECT * FROM books WHERE bid = ?", (bid,)).fetchone()
        if not b:
            return None
        pos = [dict(r) for r in con.execute(
            "SELECT e.board_key, bd.name AS board, e.cat, e.rank, e.words AS w,"
            " e.collect_num AS n, e.update_date AS d"
            " FROM entries e LEFT JOIN boards bd ON bd.key = e.board_key"
            " WHERE e.bid = ? AND e.cat = '全部' ORDER BY e.board_key", (bid,))]
        now = time.time()
        delta, base_at, hours = _shelf_delta(con, bid, b["shelf_num"] or 0, now)
        return {
            "bid": b["bid"], "title": b["title"], "author": b["author"],
            "cat2": b["cat2"], "cat3": b["cat3"], "intro": b["intro"],
            "shelf": b["shelf"] or "", "shelf_num": b["shelf_num"] or 0,
            "fans": b["fans"] or "", "score": b["score"], "ratings": b["ratings"] or 0,
            # 近 24 小时新增收藏：delta 为 None 表示还没攒够基准快照
            "shelf_24h": delta, "shelf_24h_base": base_at, "shelf_24h_hours": hours,
            "detail_at": b["detail_at"] or "",
            "collected_at": _stamp(con), "positions": pos,
        }


def onboard_bids(board="new"):
    """当前在榜书目的 bid 列表。

    采集器（比如 shelf_snapshot.py）靠它决定要给哪些书拍快照。
    走接口而不是直连库，是因为库可能在容器里：Windows 宿主机进程和
    容器里 Linux 进程同时打开同一个 WAL 文件会直接 disk I/O error。
    """
    with db.reader() as con:
        if board in ("", "all"):
            rows = con.execute("SELECT DISTINCT bid FROM entries ORDER BY bid")
        else:
            rows = con.execute(
                "SELECT DISTINCT bid FROM entries WHERE board_key = ? ORDER BY bid", (board,))
        return [r["bid"] for r in rows]


def shelf_last_at():
    """最近一次书架数快照的时间；从没采过返回空串。"""
    with db.reader() as con:
        row = con.execute("SELECT MAX(at) AS m FROM shelf_history").fetchone()
        return (row["m"] or "") if row else ""


def run_list(limit=50):
    with db.reader() as con:
        return [dict(r) for r in con.execute(
            "SELECT id, platform, source, collected_at, received_at, status, boards, cats, books"
            " FROM runs ORDER BY id DESC LIMIT ?", (limit,))]
