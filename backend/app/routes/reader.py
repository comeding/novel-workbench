"""拆书：抓目录、读章节。

数据来自 QQ阅读 网页版，不用登录、不用模拟器。两件事分开做：

  GET /api/reader/{bid}/toc              目录（缓存，?refresh=1 重抓）
  GET /api/reader/{bid}/chapter/{no}     某一章正文（缓存）
  GET /api/reader/history                最近读过

免费 / 付费
----------
免费章节能拿到完整正文；付费章节网页只给两百字左右的试读，末尾是省略号，
页面底部写着「登录订阅本章」——这不是抓取失败，是平台只给这么多。
所以接口如实返回 paid=true，正文只放试读片段，前端会写明原因。

目录里每章有 free 字段，但 SSR 压缩后是变量名（free:a / free:d），值读不出来。
抓第 1 章看它是免费还是付费，就能反推这个变量代表什么，一次请求标好全本。
"""
import json
import os
import sys
import threading
import time

from .. import db
from ..config import ROOT as ROOT_DIR
from ..router import Router, json_response

# 抓取逻辑放在 collector/chapter.py：它同时是个能单独跑的脚本（python collector/chapter.py 123 --read 1），
# 后端直接复用，免得同一套解析规则写两份。
_CH_DIR = os.path.join(ROOT_DIR, "collector")
if _CH_DIR not in sys.path:
    sys.path.insert(0, _CH_DIR)

import chapter as chapter_src          # noqa: E402

router = Router()

# 同一本书的目录同时只让一个请求去抓，其余等结果，避免两个人同时点造成重复下载
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(bid):
    with _LOCKS_GUARD:
        lk = _LOCKS.get(bid)
        if lk is None:
            lk = _LOCKS[bid] = threading.Lock()
        return lk


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------- 读库 ----------

def _book_row(bid):
    with db.reader() as con:
        return con.execute("SELECT * FROM read_books WHERE bid = ?", (bid,)).fetchone()


def _chapters(bid):
    with db.reader() as con:
        return con.execute(
            "SELECT no, title, free, at FROM read_chapters WHERE bid = ? ORDER BY no",
            (bid,)).fetchall()


def _text_row(bid, no):
    with db.reader() as con:
        return con.execute("SELECT * FROM read_text WHERE bid = ? AND no = ?",
                           (bid, no)).fetchone()


def _toc_json(bid, book, rows, cached):
    return {
        "ok": True,
        "cached": cached,
        "bid": bid,
        "title": book["title"] or "",
        "author": book["author"] or "",
        "total": book["total"] or len(rows),
        "free_count": book["free_count"] or 0,
        "calibrated": bool(book["free_token"]),
        "fetched_at": book["fetched_at"] or "",
        "chapters": [{"n": r["no"], "t": r["title"] or "", "f": r["free"]} for r in rows],
    }


# ---------- 写库 ----------

def _save_toc(bid, data, free_info):
    free = free_info["free"] if free_info else None
    token = free_info["free_token"] if free_info else None
    stamp = _now()
    rows = []
    for i, c in enumerate(data["chapters"]):
        rows.append((bid, c["no"], c["title"] or "", -1 if free is None else free[i], c["at"] or ""))
    with db.tx() as con:
        con.execute("DELETE FROM read_chapters WHERE bid = ?", (bid,))
        con.executemany(
            "INSERT INTO read_chapters (bid, no, title, free, at) VALUES (?,?,?,?,?)", rows)
        con.execute(
            "INSERT INTO read_books (bid, title, author, total, free_count, free_token, fetched_at)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(bid) DO UPDATE SET title=excluded.title, author=excluded.author,"
            " total=excluded.total, free_count=excluded.free_count,"
            " free_token=excluded.free_token, fetched_at=excluded.fetched_at",
            (bid, data["title"] or "", data["author"] or "", len(rows),
             sum(1 for v in free if v == 1) if free else 0, token or "", stamp))


def _save_text(bid, ch):
    with db.tx() as con:
        con.execute(
            "INSERT INTO read_text (bid, no, title, paras, words, paid, fetched_at)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(bid, no) DO UPDATE SET title=excluded.title, paras=excluded.paras,"
            " words=excluded.words, paid=excluded.paid, fetched_at=excluded.fetched_at",
            (bid, ch["no"], ch["title"], json.dumps(ch["paras"], ensure_ascii=False),
             ch["words"], 1 if ch["paid"] else 0, _now()))


def _touch(bid, no):
    with db.tx() as con:
        con.execute(
            "INSERT INTO read_history (bid, no, at) VALUES (?,?,?)"
            " ON CONFLICT(bid) DO UPDATE SET no=excluded.no, at=excluded.at",
            (bid, no, _now()))


# ---------- 抓目录 ----------

def _build_toc(bid):
    """抓目录 + 校准免费标记。返回 (data, free_info)。"""
    data = chapter_src.fetch_toc(bid)

    # 第 1 章既用来校准，也顺手把正文缓存下来，用户点进来就是秒开。
    # 抓失败不影响目录本身，只是免费标记留空（前端显示「—」）。
    free_info = None
    try:
        first = chapter_src.fetch_chapter(bid, 1)
        free_info = chapter_src.mark_free(data["chapters"], first["paid"])
        _save_text(bid, first)
        # 第 1 章的名字在目录里常被压成变量，正文页有明文，补上
        if data["chapters"] and not data["chapters"][0]["title"]:
            data["chapters"][0]["title"] = first["title"]
    except Exception as e:                        # noqa: BLE001
        print("  [拆书] 第 1 章没抓到，免费标记先留空：%s" % e, flush=True)

    return data, free_info


def _load_or_fetch_toc(bid, refresh=False):
    if not refresh:
        book = _book_row(bid)
        if book:
            rows = _chapters(bid)
            if rows:
                return _toc_json(bid, book, rows, True), 200

    lk = _lock_for(bid)
    if not lk.acquire(blocking=False):
        # 另一个请求正在抓，等它写完直接读缓存
        lk.acquire()
        lk.release()
        book = _book_row(bid)
        if book:
            rows = _chapters(bid)
            if rows:
                return _toc_json(bid, book, rows, True), 200
        return {"ok": False, "error": "目录正在抓取，稍后再试"}, 409

    try:
        # 等锁期间可能已经有人抓好写库了
        if not refresh:
            book = _book_row(bid)
            if book:
                rows = _chapters(bid)
                if rows:
                    return _toc_json(bid, book, rows, True), 200

        data, free_info = _build_toc(bid)
        _save_toc(bid, data, free_info)
        book = _book_row(bid)
        rows = _chapters(bid)
        return _toc_json(bid, book, rows, False), 200
    finally:
        lk.release()


@router.get(r"/api/reader/(?P<bid>\d+)/toc")
def toc(req):
    bid = req.params["bid"]
    refresh = (req.arg("refresh") or "").lower() in ("1", "true", "yes")
    try:
        out, status = _load_or_fetch_toc(bid, refresh)
    except chapter_src.FetchError as e:
        return json_response({"ok": False, "error": str(e)}, 502)
    except Exception as e:                        # noqa: BLE001
        return json_response({"ok": False, "error": "目录抓取出错：%s" % e}, 500)
    return json_response(out, status)


# ---------- 抓正文 ----------

@router.get(r"/api/reader/(?P<bid>\d+)/chapter/(?P<no>\d+)")
def chapter(req):
    bid = req.params["bid"]
    no = int(req.params["no"])
    if no < 1:
        return json_response({"ok": False, "error": "章节号要从 1 开始"}, 400)

    if (req.arg("refresh") or "") not in ("1", "true", "yes"):
        row = _text_row(bid, no)
        if row:
            _touch(bid, no)
            return {
                "ok": True, "cached": True, "bid": bid, "no": row["no"],
                "title": row["title"] or ("第 %d 章" % no),
                "paras": json.loads(row["paras"] or "[]"),
                "words": row["words"] or 0,
                "paid": bool(row["paid"]),
                "tip": "付费章节，网页只给这么多试读" if row["paid"] else "",
            }

    try:
        ch = chapter_src.fetch_chapter(bid, no)
    except chapter_src.FetchError as e:
        return json_response({"ok": False, "error": str(e)}, 502)

    if not ch["paras"]:
        return json_response({"ok": False, "error": "这一章没有正文，可能在审核或已下架"}, 404)

    _save_text(bid, ch)
    _touch(bid, no)
    return {"ok": True, "cached": False, "bid": bid, "no": ch["no"],
            "title": ch["title"], "paras": ch["paras"], "words": ch["words"],
            "paid": ch["paid"], "tip": ch["tip"]}


# ---------- 最近读过 ----------

@router.get(r"/api/reader/history")
def history(req):
    limit = req.arg_int("limit", 8)
    with db.reader() as con:
        rows = con.execute(
            "SELECT h.bid, h.no, h.at, b.title, b.total, b.free_count,"
            "       c.title AS chapter_title"
            "  FROM read_history h"
            "  LEFT JOIN read_books b ON b.bid = h.bid"
            "  LEFT JOIN read_chapters c ON c.bid = h.bid AND c.no = h.no"
            " ORDER BY h.at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        out.append({
            "bid": r["bid"], "no": r["no"], "at": r["at"],
            "title": r["title"] or ("书 %s" % r["bid"]),
            "chapter_title": r["chapter_title"] or ("第 %s 章" % (r["no"] or 1)),
            "total": r["total"] or 0,
            "free_count": r["free_count"] or 0,
        })
    return {"ok": True, "items": out}
