#!/usr/bin/env python
"""把 App 缓存里的精确字数补进库。

采集端早期把 totalWords 除以 10000 后四舍五入成 words（万），精确值丢了。
这份脚本直接读 App 的 http_cache（里面存着原始的 bookList 响应），
按 bid 把 totalWords / num / lastChapter 的精确值写回 entries。

    python backend/tools/backfill_exact.py                     # 用 rev/ 里的快照
    python backend/tools/backfill_exact.py <qqreader_flutter.db 路径>

注意：缓存里只有最近几次请求过的榜单，覆盖不到全库 —— 没覆盖到的书
words_exact 仍是 0，前端会退回显示「25.6 万字」。重新跑一次采集即可全量精确。
"""
import json
import os
import sqlite3
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BASE)
sys.path.insert(0, BASE)

from app import db                        # noqa: E402

DEFAULT_CACHE = os.path.join(ROOT, "rev", "com.qq.reader", "databases", "qqreader_flutter.db")


def read_cache(path):
    """从 http_cache 里捞出所有 bookList 响应，按 bid 去重（后出现的覆盖先出现的）。"""
    con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    con.row_factory = sqlite3.Row
    books = {}
    hits = 0
    for row in con.execute("SELECT response FROM http_cache"):
        resp = row["response"]
        if not resp or "bookList" not in resp:
            continue
        try:
            data = json.loads(resp)
        except (TypeError, ValueError):
            continue
        items = data.get("bookList") or []
        if not items:
            continue
        hits += 1
        col = data.get("columnInfo") or {}
        print("  %-28s cid=%-8s %3d 本" % (
            (col.get("name") or "?")[:28], col.get("cid"), len(items)))
        for b in items:
            bid = b.get("bid")
            if bid is None:
                continue
            books[str(bid)] = {
                "total_words": b.get("totalWords"),
                "collect_num": b.get("num"),
                "last_chapter": b.get("lastChapter"),
            }
    con.close()
    return books, hits


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CACHE
    if not os.path.isfile(path):
        print("找不到缓存库：%s" % path)
        print("用法：python backend/tools/backfill_exact.py <qqreader_flutter.db>")
        return 1

    print("读取缓存：%s" % path)
    books, hits = read_cache(path)
    print("\n%d 份响应，去重后 %d 本" % (hits, len(books)))

    db.init()
    updated = 0
    matched = 0
    with db.tx() as con:
        dbids = {str(r["bid"]) for r in con.execute("SELECT DISTINCT bid FROM entries")}
        print("库内在榜书目：%d 本" % len(dbids))
        for bid, info in books.items():
            if bid not in dbids:
                continue
            matched += 1
            tw = info["total_words"]
            try:
                tw = int(tw or 0)
            except (TypeError, ValueError):
                tw = 0
            cn = info["collect_num"]
            try:
                cn = int(float(cn)) if cn not in (None, "") else None
            except (TypeError, ValueError):
                cn = None
            lc = info["last_chapter"]
            try:
                lc = int(lc) if lc is not None else None
            except (TypeError, ValueError):
                lc = None

            cur = con.execute(
                "UPDATE entries SET words_exact = ? WHERE bid = ?"
                " AND (words_exact IS NULL OR words_exact <> ?)",
                (tw, bid, tw))
            updated += cur.rowcount or 0
            if cn is not None:
                con.execute("UPDATE entries SET collect_num = ? WHERE bid = ?", (cn, bid))
            if lc is not None:
                con.execute("UPDATE entries SET last_chapter = ? WHERE bid = ?", (lc, bid))

        # 让正在跑的服务丢掉 dataset 缓存
        con.execute("INSERT INTO meta (k, v) VALUES ('revision', ?)"
                    " ON CONFLICT(k) DO UPDATE SET v = excluded.v", (str(int(time.time() * 1000)),))

    print("命中库内书目：%d 本，更新精确字数：%d 条" % (matched, updated))
    rest = len(dbids) - matched
    if rest:
        print("仍有 %d 本没有精确字数（缓存里没有），重新采集一次即可补齐。" % rest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
