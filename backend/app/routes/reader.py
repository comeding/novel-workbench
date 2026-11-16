"""拆书：抓目录、读章节。

数据来自 QQ阅读 网页版，不用登录、不用模拟器。两件事分开做：

  GET /api/reader/books                  拆书列表（每本一张封面卡）
  DELETE /api/reader/books/{bid}         从拆书列表里删掉一本
  GET /api/reader/{bid}/toc              目录（缓存，?refresh=1 重抓）
  GET /api/reader/{bid}/chapter/{no}     某一章正文（缓存）
  GET /api/reader/history                最近读过

  GET /api/reader/{bid}/notes            这本书的全部笔记（段落批注 + 章纲）
  POST /api/reader/{bid}/notes           写 / 改一条笔记（text 传空 = 删除）
  DELETE /api/reader/{bid}/notes/{no}/{para}   删一条
  GET /api/reader/docs                   拆书文件列表（有笔记的书）
  GET /api/reader/docs/{bid}             这本书的拆书文件（含 markdown，复制全文用）

免费 / 付费
----------
拆书只做免费章节。目录还是整本拉下来（免费标记要拿整本的 token 分布校准），
但接口只返回免费的那几十章，正文也只读这些——付费章节网页本来只给两百字试读，
列出来除了浪费点击没有别的用处。

目录里每章有 free 字段，但 SSR 压缩后是变量名（free:a / free:d），值读不出来。
抓第 1 章看它是免费还是付费，就能反推这个变量代表什么，一次请求标好全本。
"""
import json
import os
import re
import sys
import threading
import time

from .. import db
from ..config import ROOT as ROOT_DIR
from ..router import Router, Response, json_response

# 抓取逻辑放在 collector/chapter.py：它同时是个能单独跑的脚本（python collector/chapter.py 123 --read 1），
# 后端直接复用，免得同一套解析规则写两份。
_CH_DIR = os.path.join(ROOT_DIR, "collector")
if _CH_DIR not in sys.path:
    sys.path.insert(0, _CH_DIR)

import chapter as chapter_src          # noqa: E402
import fanqie_chapter as fanqie_src    # noqa: E402

# 两个平台各一个抓取模块，对外接口一样（fetch_toc / fetch_chapter / mark_free）
SRC = {"qq": chapter_src, "fq": fanqie_src}


def _src(pf):
    return SRC.get(pf or "qq") or chapter_src

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


COVER_TPL = "https://wfqqreader-1252317822.image.myqcloud.com/cover/%d/%s/t5_%s.jpg"


def _cover_url(bid):
    """按 QQ阅读 封面 CDN 的路径规则拼一个。
    bid=61368253 → cover/253/61368253/t5_61368253.jpg，中段是 bid 后三位（去掉前导零）。
    只在 read_books.cover 为空时兜底，抓过目录的书都有准确地址。
    """
    try:
        n = int(str(bid))
    except (TypeError, ValueError):
        return ""
    return COVER_TPL % (n % 1000, bid, bid)


def _cover(bid, cover, pf="qq"):
    """前端能直接用的封面地址。

    番茄的封面是带签名的 CDN 地址，一天就过期，存库那份不能直接给浏览器。
    这里给一个代理地址，由 /api/reader/cover/{bid} 现签现跳，过期了自动换新的。
    QQ 的封面路径是固定的，用存下来的地址，没有就按规则推。
    """
    if pf == "fq":
        return "/api/reader/cover/%s" % bid
    return cover or _cover_url(bid)


# 番茄封面现签地址的缓存：bid -> (url, 过期时间戳)
_COVER_CACHE = {}
_COVER_LOCK = threading.Lock()


def _fresh_cover(bid):
    """取一个还没过期的番茄封面地址，快过期就重新取一次页面。"""
    now = time.time()
    with _COVER_LOCK:
        hit = _COVER_CACHE.get(bid)
        if hit and hit[1] - now > 3600:      # 至少还能用一小时才给前端
            return hit[0]
    url, expire = fanqie_src.cover_url(bid)
    if url and expire:
        with _COVER_LOCK:
            _COVER_CACHE[bid] = (url, expire)
    return url


@router.get(r"/api/reader/cover/(?P<bid>\d+)")
def cover(req):
    """番茄封面代理：302 到当前可用的签名地址。

    签名地址一天过期，直接存库给前端的话第二天全是裂图。这里每次按需换新的，
    缓存一小时，等于把签名问题挡在后端。
    """
    bid = req.params["bid"]
    pf = _book_pf(bid)
    if pf == "qq":                            # QQ 的封面是公开 CDN，直接跳
        row = _book_row(bid)
        url = ((row["cover"] if row and ("cover" in row.keys()) else "") or _cover_url(bid))
        return Response(b"", 302, "text/plain; charset=utf-8", {"Location": url})
    try:
        url = _fresh_cover(bid)
    except chapter_src.FetchError as e:
        return json_response({"ok": False, "error": str(e)}, 404)
    if not url:
        return json_response({"ok": False, "error": "这本书没有封面"}, 404)
    return Response(b"", 302, "text/plain; charset=utf-8", {"Location": url})


# ---------- 读库 ----------

def _book_row(bid):
    with db.reader() as con:
        return con.execute("SELECT * FROM read_books WHERE bid = ?", (bid,)).fetchone()


def _book_pf(bid):
    """这本书是哪个平台的。没拆过就不知道，调用方自己再兜一个默认值。"""
    row = _book_row(bid)
    if row is None:
        return ""
    try:
        return row["pf"] or "qq"
    except (IndexError, KeyError):       # 老库还没补列
        return "qq"


def _chapters(bid):
    with db.reader() as con:
        return con.execute(
            "SELECT no, title, free, at FROM read_chapters WHERE bid = ? ORDER BY no",
            (bid,)).fetchall()


def _text_row(bid, no):
    with db.reader() as con:
        return con.execute("SELECT * FROM read_text WHERE bid = ? AND no = ?",
                           (bid, no)).fetchone()


def _free_flag(bid, no):
    """目录里这一章的免费标记：1 免费 / 0 付费 / -1 没校准 / None 没这部书的目录。"""
    with db.reader() as con:
        row = con.execute("SELECT free FROM read_chapters WHERE bid = ? AND no = ?",
                          (bid, no)).fetchone()
    return None if row is None else row["free"]


def _toc_json(bid, book, rows, cached):
    """只把免费章节发给前端。

    rows 里存的是整本目录（带 free 标记），这里筛一遍：免费 = 1，付费 = 0，
    -1 表示当天没校准成功，一律先不给，让页面提示重抓目录，而不是混进付费章。
    """
    total = book["total"] or len(rows)
    pf = (book["pf"] if "pf" in book.keys() else "") or "qq"
    free = [r for r in rows if r["free"] == 1]
    token = book["free_token"] or ""
    warn = ""
    if not token:
        warn = "这本书的免费章节没标出来（第 1 章没抓到），点右上角「重抓目录」再试"
    elif not free:
        warn = "这本书没有免费章节，网页上只能看到付费章节"
    return {
        "ok": True,
        "cached": cached,
        "bid": bid,
        "pf": pf,
        "title": book["title"] or "",
        "author": book["author"] or "",
        "cover": _cover(bid, book["cover"] if "cover" in book.keys() else "", pf),
        "total": total,
        "free_count": len(free),
        "calibrated": bool(token),
        "fetched_at": book["fetched_at"] or "",
        "chapters": [{"n": r["no"], "t": r["title"] or "", "f": 1} for r in free],
        "warn": warn,
    }


# ---------- 写库 ----------

def _save_toc(bid, data, free_info, pf="qq"):
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
            "INSERT INTO read_books (bid, pf, title, author, cover, total, free_count,"
            " free_token, fetched_at) VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(bid) DO UPDATE SET pf=excluded.pf, title=excluded.title,"
            " author=excluded.author,"
            " cover=COALESCE(NULLIF(excluded.cover, ''), read_books.cover),"
            " total=excluded.total, free_count=excluded.free_count,"
            " free_token=excluded.free_token, fetched_at=excluded.fetched_at",
            (bid, pf or "qq", data["title"] or "", data["author"] or "", data.get("cover") or "",
             len(rows), sum(1 for v in free if v == 1) if free else 0, token or "", stamp))


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

def _build_toc(bid, pf="qq"):
    """抓目录 + 校准免费标记。返回 (data, free_info)。"""
    src = _src(pf)
    data = src.fetch_toc(bid)

    # 第 1 章既用来校准，也顺手把正文缓存下来，用户点进来就是秒开。
    # 抓失败不影响目录本身，只是免费标记留空（前端显示「—」）。
    free_info = None
    try:
        first = src.fetch_chapter(bid, 1)
        free_info = src.mark_free(data["chapters"], first["paid"])
        _save_text(bid, first)
        # 第 1 章的名字在目录里常被压成变量，正文页有明文，补上
        if data["chapters"] and not data["chapters"][0]["title"]:
            data["chapters"][0]["title"] = first["title"]
    except Exception as e:                        # noqa: BLE001
        print("  [拆书] 第 1 章没抓到，免费标记先留空：%s" % e, flush=True)

    return data, free_info


def _load_or_fetch_toc(bid, refresh=False, pf="qq"):
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

        data, free_info = _build_toc(bid, pf)
        _save_toc(bid, data, free_info, pf)
        book = _book_row(bid)
        rows = _chapters(bid)
        return _toc_json(bid, book, rows, False), 200
    finally:
        lk.release()


@router.get(r"/api/reader/(?P<bid>\d+)/toc")
def toc(req):
    bid = req.params["bid"]
    refresh = (req.arg("refresh") or "").lower() in ("1", "true", "yes")
    # 平台：拆过的书以库里为准（同一个 bid 不会同时属于两个平台，书号长度都不一样），
    # 没拆过就看前端传的 ?pf=，都没有按 QQ 处理
    pf = _book_pf(bid) or (req.arg("pf") or "qq")
    try:
        out, status = _load_or_fetch_toc(bid, refresh, pf)
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

    # 拆书只做免费章节。有人从旧记录/书签直接点进来时，这里拦一下，
    # 免得把两百字试读当成正文展示。
    if _free_flag(bid, no) == 0:
        return json_response({"ok": False, "error": "这一章是付费章节，拆书只做免费章节"}, 403)

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
        src = _src(_book_pf(bid) or (req.arg("pf") or "qq"))
        ch = src.fetch_chapter(bid, no)
    except chapter_src.FetchError as e:
        return json_response({"ok": False, "error": str(e)}, 502)

    if not ch["paras"]:
        return json_response({"ok": False, "error": "这一章没有正文，可能在审核或已下架"}, 404)
    if ch["paid"]:
        # 目录说免费、正文页却只给试读，说明平台把这一章改成付费了，别缓存也别冒充
        return json_response({"ok": False,
                              "error": "这一章现在是付费章节，拆书只做免费章节"}, 403)

    _save_text(bid, ch)
    _touch(bid, no)
    return {"ok": True, "cached": False, "bid": bid, "no": ch["no"],
            "title": ch["title"], "paras": ch["paras"], "words": ch["words"],
            "paid": ch["paid"], "tip": ch["tip"]}


# ---------- 拆书列表 ----------

@router.get(r"/api/reader/books")
def books(req):
    """拆过的书，一本一条，按最近动过的排前面。

    read_books 一行就是一条「拆书记录」；saved / words 来自正文缓存，
    也就是真的在这台机器上存下来的章节数、字数，不是目录里的全书规模。
    """
    limit = max(1, min(1000, req.arg_int("limit", 200)))
    with db.reader() as con:
        rows = con.execute(
            "SELECT b.bid, b.pf, b.title, b.author, b.cover, b.total, b.free_count, b.fetched_at,"
            "       h.no AS last_no, h.at AS last_at,"
            "       (SELECT COUNT(*) FROM read_text t"
            "         WHERE t.bid = b.bid AND t.paid = 0) AS saved,"
            "       (SELECT COALESCE(SUM(t.words), 0) FROM read_text t"
            "         WHERE t.bid = b.bid AND t.paid = 0) AS words"
            "  FROM read_books b"
            "  LEFT JOIN read_history h ON h.bid = b.bid"
            " ORDER BY COALESCE(h.at, b.fetched_at) DESC LIMIT ?", (limit,)).fetchall()
    items = [{
        "bid": r["bid"],
        "pf": (r["pf"] or "qq"),
        "title": r["title"] or ("书 %s" % r["bid"]),
        "author": r["author"] or "",
        "cover": _cover(r["bid"], r["cover"] or "", r["pf"] or "qq"),
        "total": r["total"] or 0,
        "free_count": r["free_count"] or 0,
        "saved": r["saved"] or 0,
        "words": r["words"] or 0,
        "last_no": r["last_no"] or 0,
        "last_at": r["last_at"] or "",
        "fetched_at": r["fetched_at"] or "",
    } for r in rows]
    return {"ok": True, "count": len(items), "items": items}


@router.delete(r"/api/reader/books/(?P<bid>\d+)")
def drop_book(req):
    """从拆书列表里删掉一本书。

    删的是这台机器上的拆书记录：目录、缓存的正文章节、阅读进度一起清掉，
    下次再拆这本书会重新抓一遍。扫榜的 books / entries 不受影响。
    """
    bid = req.params["bid"]
    with db.tx() as con:
        texts = con.execute("DELETE FROM read_text WHERE bid = ?", (bid,)).rowcount
        chapters = con.execute("DELETE FROM read_chapters WHERE bid = ?", (bid,)).rowcount
        marks = con.execute("DELETE FROM read_marks WHERE bid = ?", (bid,)).rowcount
        con.execute("DELETE FROM read_history WHERE bid = ?", (bid,))
        books = con.execute("DELETE FROM read_books WHERE bid = ?", (bid,)).rowcount
    if not books:
        return json_response({"ok": False, "error": "这本书不在拆书列表里"}, 404)
    return {"ok": True, "bid": bid, "chapters": chapters, "texts": texts, "marks": marks}


# ---------- 拆书笔记 ----------
# 两种笔记，都是人手写的，和抓来的正文分开存：
#   note    段落批注，挂在第 no 章第 para 段（0 起）
#   outline 本章章纲，para 固定 -1
# 一本书的笔记一次全给前端：切章不用再问一次，目录也能顺手标出哪些章有笔记。

MARK_KINDS = ("note", "outline")
MAX_NOTE = 4000


@router.get(r"/api/reader/(?P<bid>\d+)/notes")
def notes(req):
    bid = req.params["bid"]
    with db.reader() as con:
        rows = con.execute(
            "SELECT no, para, kind, text, quote, at FROM read_marks WHERE bid = ?"
            " ORDER BY no, kind, para", (bid,)).fetchall()
    return {"ok": True, "bid": bid, "count": len(rows), "items": [dict(r) for r in rows]}


@router.post(r"/api/reader/(?P<bid>\d+)/notes")
def save_note(req):
    """写 / 改一条笔记。text 传空就等于删掉，前端别的不必再单独调删除。"""
    bid = req.params["bid"]
    try:
        body = req.json()
    except ValueError as e:
        return json_response({"ok": False, "error": str(e)}, 400)

    kind = (body.get("kind") or "note").strip()
    if kind not in MARK_KINDS:
        return json_response({"ok": False, "error": "kind 只能是 note 或 outline"}, 400)
    try:
        no = int(body.get("no") or 0)
        para = -1 if kind == "outline" else int(body.get("para", -1))
    except (TypeError, ValueError):
        return json_response({"ok": False, "error": "no / para 不是数字"}, 400)
    if no < 1:
        return json_response({"ok": False, "error": "章节号要从 1 开始"}, 400)
    if kind == "note" and para < 0:
        return json_response({"ok": False, "error": "段落批注要带段落序号"}, 400)

    text = (body.get("text") or "").strip()[:MAX_NOTE]
    # 锚定段落的前几十字：正文重抓后段落可能重排，有原文才好找回位置
    quote = (body.get("quote") or "").strip()[:80]

    if not text:
        with db.tx() as con:
            con.execute("DELETE FROM read_marks WHERE bid = ? AND no = ? AND para = ? AND kind = ?",
                        (bid, no, para, kind))
        return {"ok": True, "deleted": True, "bid": bid, "no": no, "para": para, "kind": kind}

    stamp = _now()
    with db.tx() as con:
        con.execute(
            "INSERT INTO read_marks (bid, no, para, kind, text, quote, at) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(bid, no, para, kind) DO UPDATE SET text = excluded.text,"
            " quote = excluded.quote, at = excluded.at",
            (bid, no, para, kind, text, quote, stamp))
    return {"ok": True, "bid": bid, "no": no, "para": para, "kind": kind,
            "text": text, "quote": quote, "at": stamp}


@router.delete(r"/api/reader/(?P<bid>\d+)/notes/(?P<no>\d+)/(?P<para>-?\d+)")
def drop_note(req):
    bid = req.params["bid"]
    no = int(req.params["no"])
    para = int(req.params["para"])
    kind = req.arg("kind") or ("outline" if para < 0 else "note")
    with db.tx() as con:
        n = con.execute("DELETE FROM read_marks WHERE bid = ? AND no = ? AND para = ? AND kind = ?",
                        (bid, no, para, kind)).rowcount
    return {"ok": True, "deleted": n, "bid": bid, "no": no, "para": para, "kind": kind}


# ---------- 拆书文件 ----------
# 拆书文件不是另存一份文档，而是「把这本书的笔记按章攒起来」的实时视图：
# 笔记改了文件立刻跟着变，不会出现两份对不上的内容。

# 目录名里的「第 12 章」：有就不用再拿章节号拼一遍，免得出现「第 12 章　第 12 章 标题」
RE_CH_NO = re.compile(r"^第\s*[0-9０-９零〇一二三四五六七八九十百千万两]+\s*章")


def _chapter_heading(no, title):
    t = (title or "").strip()
    if t and RE_CH_NO.match(t):
        return t
    return "第 %d 章" % no + ("　" + t if t else "")

def _doc_markdown(meta, chapters):
    """拆书文件的 markdown 版：「复制全文」直接用它。"""
    out = ["# 《%s》拆书文件" % meta["title"], ""]
    sub = []
    if meta["author"]:
        sub.append("作者：%s" % meta["author"])
    if meta["total"]:
        sub.append("全书 %d 章" % meta["total"])
    sub.append("%d 章有笔记　·　%d 条章纲　·　%d 条批注"
               % (meta["chapters"], meta["outlines"], meta["notes"]))
    sub.append("整理于 %s" % meta["exported_at"])
    out += ["> " + "　·　".join(sub), ""]

    for c in chapters:
        out += ["## " + c["heading"], ""]
        if c["outline"]:
            out += ["**章纲**", "", c["outline"], ""]
        if c["notes"]:
            out += ["**批注**", ""]
            for i, n in enumerate(c["notes"], 1):
                if n["quote"]:
                    out.append("%d. 「%s」" % (i, n["quote"]))
                    out.append("   > " + n["text"].replace("\n", "\n   > "))
                else:
                    out.append("%d. %s" % (i, n["text"].replace("\n", "\n   ")))
                out.append("")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


@router.get(r"/api/reader/docs")
def docs(req):
    """拆书文件列表：写过笔记的书才有文件。"""
    limit = max(1, min(500, req.arg_int("limit", 200)))
    with db.reader() as con:
        rows = con.execute(
            "SELECT m.bid,"
            "       SUM(CASE WHEN m.kind = 'note' THEN 1 ELSE 0 END) AS notes,"
            "       SUM(CASE WHEN m.kind = 'outline' THEN 1 ELSE 0 END) AS outlines,"
            "       COUNT(DISTINCT m.no) AS chapters,"
            "       MAX(m.at) AS updated_at,"
            "       b.title, b.author, b.cover, b.total, b.pf"
            "  FROM read_marks m LEFT JOIN read_books b ON b.bid = m.bid"
            " GROUP BY m.bid ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    items = [{
        "bid": r["bid"],
        "pf": (r["pf"] or "qq"),
        "title": r["title"] or ("书 %s" % r["bid"]),
        "author": r["author"] or "",
        "cover": _cover(r["bid"], r["cover"] or "", r["pf"] or "qq"),
        "total": r["total"] or 0,
        "notes": r["notes"] or 0,
        "outlines": r["outlines"] or 0,
        "chapters": r["chapters"] or 0,
        "updated_at": r["updated_at"] or "",
    } for r in rows]
    return {"ok": True, "count": len(items), "items": items}


@router.get(r"/api/reader/docs/(?P<bid>\d+)")
def doc(req):
    bid = req.params["bid"]
    with db.reader() as con:
        book = con.execute("SELECT * FROM read_books WHERE bid = ?", (bid,)).fetchone()
        rows = con.execute(
            "SELECT m.no, m.para, m.kind, m.text, m.quote, m.at, c.title AS toc_title"
            "  FROM read_marks m"
            "  LEFT JOIN read_chapters c ON c.bid = m.bid AND c.no = m.no"
            " WHERE m.bid = ? ORDER BY m.no, m.kind, m.para", (bid,)).fetchall()
    if not rows:
        return json_response({"ok": False, "error": "这本书还没有拆书笔记"}, 404)

    by_no = {}
    order = []
    for r in rows:
        c = by_no.get(r["no"])
        if c is None:
            c = by_no[r["no"]] = {"no": r["no"], "title": r["toc_title"] or "",
                                  "heading": _chapter_heading(r["no"], r["toc_title"]),
                                  "outline": "", "notes": []}
            order.append(r["no"])
        if r["kind"] == "outline":
            c["outline"] = r["text"] or ""
        elif r["text"]:
            c["notes"].append({"para": r["para"], "text": r["text"],
                               "quote": r["quote"] or "", "at": r["at"] or ""})
    # 章内按段落顺序排，前端就不用再排一次
    for no in order:
        by_no[no]["notes"].sort(key=lambda n: n["para"])
    chapters = [by_no[no] for no in order]

    note_n = sum(len(c["notes"]) for c in chapters)
    outline_n = sum(1 for c in chapters if c["outline"])
    meta = {
        "bid": bid,
        "pf": ((book["pf"] if book and "pf" in book.keys() else "") or "qq"),
        "title": (book["title"] if book else "") or ("书 %s" % bid),
        "author": (book["author"] if book else "") or "",
        "cover": _cover(bid, (book["cover"] if book and "cover" in book.keys() else "") or "",
                        (book["pf"] if book and "pf" in book.keys() else "") or "qq"),
        "total": (book["total"] if book else 0) or 0,
        "chapters": len(chapters),
        "notes": note_n,
        "outlines": outline_n,
        "updated_at": max((r["at"] or "") for r in rows),
        "exported_at": _now(),
    }
    return {"ok": True, "meta": meta, "chapters": chapters,
            "markdown": _doc_markdown(meta, chapters)}


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
