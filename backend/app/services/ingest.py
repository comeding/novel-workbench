"""采集数据入库。

输入是采集器产出的原始 JSON（结构见 collector/collectors/qqreader_app.py 的 save）：
    {
      "platform": "QQ阅读 App",
      "source":   "qqreader_flutter.db / http_cache",
      "collected_at": "2026-11-08 13:07:56",
      "boards": {
        "会员榜": {"column": {...}, "books": [...], "categories": {"全部": {...}, ...}},
        ...
      }
    }

只保留最新一版：payload 里出现的榜单，会整体替换掉库里该榜的所有在榜记录。
没出现在本次 payload 里的榜单原样保留，所以只采一个榜也不会把另一个榜清空。
"""
import json
import os
import re
import time

from .. import db
from ..config import Config

# 榜单中文名 -> 前端路由用的 key。新榜单不在这里也没关系，会自动生成 key。
BOARD_KEYS = {"会员榜": "vip", "新书榜": "new"}

INTRO_MAX = 400


def _int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _float(v, default=0.0):
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return default


def _txt(v, limit=None):
    s = "" if v is None else str(v)
    return s[:limit] if limit else s


def board_key(name):
    if name in BOARD_KEYS:
        return BOARD_KEYS[name]
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", name) or "board"


def _archive(payload):
    """上报的原始 JSON 落盘一份，出问题时能对账。"""
    try:
        os.makedirs(Config.ARCHIVE_DIR, exist_ok=True)
        stamp = re.sub(r"\D", "", str(payload.get("collected_at") or ""))[:14] or str(int(time.time()))
        path = os.path.join(Config.ARCHIVE_DIR, "ingest_%s.json" % stamp)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        return path
    except OSError:
        return None


def ingest(payload):
    """把一份采集结果写库（覆盖式）。返回 {inserted, cats, boards, raw}。"""
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是 JSON 对象")

    collected_at = _txt(payload.get("collected_at")).strip()
    boards = payload.get("boards") or {}
    if not collected_at:
        raise ValueError("缺少 collected_at")
    if not isinstance(boards, dict) or not boards:
        raise ValueError("缺少 boards")

    platform = _txt(payload.get("platform")) or "unknown"
    source = _txt(payload.get("source"))
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    raw = _archive(payload)

    with db.tx() as con:
        cat_total = 0
        entry_total = 0
        names = []

        for bname, binfo in boards.items():
            binfo = binfo or {}
            key = board_key(bname)
            col = binfo.get("column") or {}
            names.append(bname)

            con.execute(
                "INSERT INTO boards (key, name, cid, push_name, updated_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET name = excluded.name, cid = excluded.cid,"
                " push_name = excluded.push_name, updated_at = excluded.updated_at",
                (key, bname, _txt(col.get("cid")), _txt(col.get("push_name")), collected_at),
            )

            cats = binfo.get("categories") or {}
            # 老数据可能只有总榜，补成一个「全部」分类，保证下游查询口径统一
            if not cats and binfo.get("books"):
                cats = {"全部": {"column": col, "books": binfo["books"]}}

            # 整榜清空是危险的：这次只采到 9 个分类时，会把上次完整的 14 个分类一起删掉。
            # 所以按分类替换，并区分「部分采集」和「完整重采」。
            old_cats = {r["name"] for r in con.execute(
                "SELECT name FROM categories WHERE board_key = ?", (key,))}
            full = len(cats) >= len(old_cats)
            if full:
                # 完整重采：本次没出现的分类视为已在 App 里下线，一并清掉
                for stale in old_cats - set(cats):
                    con.execute("DELETE FROM entries WHERE board_key = ? AND cat = ?", (key, stale))
                    con.execute("DELETE FROM categories WHERE board_key = ? AND name = ?", (key, stale))

            for cname, cinfo in cats.items():
                cinfo = cinfo or {}
                ccol = cinfo.get("column") or col
                cat_total += 1
                con.execute(
                    "INSERT INTO categories (board_key, name, cid, updated_at) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(board_key, name) DO UPDATE SET cid = excluded.cid,"
                    " updated_at = excluded.updated_at",
                    (key, cname, _txt(ccol.get("cid")), collected_at),
                )

                rows = []
                for i, b in enumerate(cinfo.get("books") or [], 1):
                    bid = _txt(b.get("bid")).strip()
                    if not bid:
                        continue
                    wx = _int(b.get("total_words"))          # 精确字数，老 payload 没有就是 0
                    w = _float(b.get("words_wan"))
                    if not w and wx:
                        w = round(wx / 10000.0, 1)
                    con.execute(
                        "INSERT INTO books (bid, title, author, cat2, cat3, intro, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)"
                        " ON CONFLICT(bid) DO UPDATE SET title = excluded.title,"
                        " author = excluded.author, cat2 = excluded.cat2, cat3 = excluded.cat3,"
                        " intro = CASE WHEN length(excluded.intro) > length(books.intro)"
                        "              THEN excluded.intro ELSE books.intro END,"
                        " updated_at = excluded.updated_at",
                        (
                            bid,
                            _txt(b.get("title")),
                            _txt(b.get("author")),
                            _txt(b.get("cat2")),
                            _txt(b.get("cat3")),
                            _txt(b.get("intro"), INTRO_MAX),
                            collected_at,
                        ),
                    )
                    rows.append((
                        key, cname, bid,
                        _int(b.get("rank")) or i,
                        w,
                        wx,
                        _int(b.get("collect_num")),
                        _txt(b.get("badge")),
                        _txt(b.get("label")),
                        _int(b.get("last_chapter")),
                        _txt(b.get("update_date")),
                    ))

                # 这个分类整体换新，避免上次多出来的书残留
                con.execute("DELETE FROM entries WHERE board_key = ? AND cat = ?", (key, cname))
                con.executemany(
                    "INSERT OR REPLACE INTO entries"
                    " (board_key, cat, bid, rank, words, words_exact, collect_num, badge, label,"
                    "  last_chapter, update_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                entry_total += len(rows)

        # 只在榜书目留在 books 里，避免长期累积无用条目
        con.execute("DELETE FROM books WHERE bid NOT IN (SELECT bid FROM entries)")

        # 同一份数据重复导入时不再刷一条日志
        seen = con.execute(
            "SELECT id FROM runs WHERE platform = ? AND collected_at = ?",
            (platform, collected_at)).fetchone()
        if not seen:
            con.execute(
                "INSERT INTO runs (platform, source, collected_at, received_at, status, boards, cats, books)"
                " VALUES (?, ?, ?, ?, 'ok', ?, ?, ?)",
                (platform, source, collected_at, now, " / ".join(names), cat_total, entry_total),
            )
        con.executemany(
            "INSERT INTO meta (k, v) VALUES (?, ?)"
            " ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            [("collected_at", collected_at), ("platform", platform),
             ("revision", str(int(time.time() * 1000)))],
        )

    return {"inserted": entry_total, "cats": cat_total, "boards": names, "raw": raw}


def ingest_details(payload):
    """书籍详情上报：只补 books 里的加入书架人数 / 书友 / 评分。

    采集器见 collector/detail.py，payload 结构：
        {"collected_at": "...", "items": [{"bid": "60858944", "shelf": "14.3万",
          "shelf_num": 143000, "fans": "4.4万", "fans_num": 44000,
          "score": 9.0, "ratings": 328}, ...]}

    只更新库里已有的书（books 里只有在榜书目），不认识的一律忽略，
    所以重复上报是安全的、也是幂等的。
    """
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是 JSON 对象")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("缺少 items")
    if len(items) > 20000:
        raise ValueError("单批太多（>20000），分批上报")

    at = _txt(payload.get("collected_at")).strip() or time.strftime("%Y-%m-%d %H:%M:%S")
    updated = 0

    with db.tx() as con:
        for it in items:
            if not isinstance(it, dict):
                continue
            bid = _txt(it.get("bid")).strip()
            shelf = _txt(it.get("shelf")).strip()
            if not bid or not shelf:
                continue
            cur = con.execute(
                "UPDATE books SET shelf = ?, shelf_num = ?, fans = ?, fans_num = ?,"
                " score = ?, ratings = ?, detail_at = ? WHERE bid = ?",
                (
                    shelf,
                    _int(it.get("shelf_num")),
                    _txt(it.get("fans")),
                    _int(it.get("fans_num")),
                    it.get("score") if isinstance(it.get("score"), (int, float)) else None,
                    _int(it.get("ratings")),
                    at,
                    bid,
                ),
            )
            updated += 1 if cur.rowcount else 0

        if updated:
            con.execute(
                "INSERT INTO meta (k, v) VALUES ('revision', ?)"
                " ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                (str(int(time.time() * 1000)),),
            )

    return {"updated": updated, "total": len(items), "collected_at": at}
