# -*- coding: utf-8 -*-
"""番茄小说的拆书抓取：目录 + 正文。

和 collector/chapter.py（QQ阅读）对外是同一套接口，后端 reader.py 按平台选一个：

    fetch_toc(bid)          -> {bid, title, author, cover, cover_expire, chapters:[...]}
    fetch_chapter(bid, no)  -> {bid, no, title, paras, words, paid, tip}
    mark_free(chapters, first_paid)   QQ 那边原样复用
    FetchError

数据来源
--------
    书籍页  https://fanqienovel.com/page/{bid}      SSR 里 page.chapterListWithVolume
    阅读页  https://fanqienovel.com/reader/{itemId} SSR 里 reader.chapterData.content

书籍页一次就把整本目录带下来了（连封面、简介、最新章节一起），所以不用像 QQ
那样再单独请求目录接口。阅读页只给当前这一章。

免费 / 付费
-----------
番茄的目录字段是明码，不用像 QQ 那样拿第 1 章反推：

    isChapterLock=False  整章，实测 2600 字上下
    isChapterLock=True   只给 160 字左右的试读，剩下要登录 / 去 App 看

所以「免费」= isChapterLock 为 False 且 needPay 为 0。新书期一般整本都放开，
上架久了的书只有前 10 章左右能看，这是平台策略，不是抓取失败。
token 用 "0"（免费）/ "1"（付费），交给 chapter.mark_free 分组，语义和 QQ 的
free 变量一致。

正文同样是字体混淆的（私有区 U+E3xx），这里直接用 fanqie.py 那张 362 条对照表
还原，不用再过一遍字体。

封面
----
page.thumbUri 是带签名的 CDN 地址（x-expires 约一天），存库会过期。这里把
签名后的地址和过期时间一起返回，后端用 /api/reader/cover/{bid} 代理：过期了
就重新取一次页面换新签名，前端只管 <img src>。

用法（项目根目录）：
    python collector/fanqie_chapter.py 7691315202393902142            # 看目录
    python collector/fanqie_chapter.py 7691315202393902142 --read 1   # 看第 1 章
"""
import html as _html
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from chapter import FetchError, mark_free       # noqa: E402  两个平台共用一套语义
from fanqie import decode                       # noqa: E402  私有区字符 → 汉字

__all__ = ["FetchError", "fetch_toc", "fetch_chapter", "mark_free",
           "cover_url", "book_meta"]

PAGE = "https://fanqienovel.com/page/%s"
READ = "https://fanqienovel.com/reader/%s"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

TIMEOUT = 25
RETRIES = 2
RE_STATE = re.compile(r"window\.__INITIAL_STATE__\s*=\s*")
RE_EXPIRES = re.compile(r"[?&]x-expires=(\d+)")
RE_P = re.compile(r"<p[^>]*>(.*?)</p>", re.S)

# 目录缓存：bid -> (chapters, put_at)。后端一个进程里跑，够用；
# 重启后第一次读某章会重新取一次书籍页（30KB 左右），不影响正确性。
_TOC_CACHE = {}
_CACHE_TTL = 1800
_CACHE_LOCK = threading.Lock()


def _get(url, accept="text/html"):
    last = None
    for i in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": accept,
                "Referer": "https://fanqienovel.com/",
            })
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as e:
            last = e
            if i < RETRIES:
                time.sleep(1.2 * (i + 1))
    raise FetchError("连不上番茄（%s）" % last)


def _state(html):
    """取页面里的 window.__INITIAL_STATE__。"""
    m = RE_STATE.search(html)
    if not m:
        raise FetchError("番茄页面里没找到 SSR 状态，可能改版了")
    try:
        obj, _ = json.JSONDecoder().raw_decode(html[m.end():])
    except ValueError as e:
        raise FetchError("番茄页面状态解析失败：%s" % e)
    return obj or {}


def _tidy(s):
    s = s.replace("\u3000", " ").replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", s).strip()


def _words(text):
    return len(re.sub(r"\s", "", text))


# ---------- 目录 ----------

def parse_toc(page, bid=""):
    """把 page 状态转成和 QQ 那套一样的目录结构。

    chapters[i] = {no, title, token, at}
    token 用 "0"/"1" 标付费，给 mark_free 分组用（和 QQ 的 free 变量同一个语义）。
    """
    vols = page.get("chapterListWithVolume") or []
    chapters = []
    for vol in vols:
        if not isinstance(vol, list):
            continue
        for c in vol:
            if not isinstance(c, dict):
                continue
            lock = bool(c.get("isChapterLock"))
            pay = bool(c.get("needPay"))
            chapters.append({
                "no": len(chapters) + 1,
                "title": decode(c.get("title") or "").strip(),
                "token": "1" if (lock or pay) else "0",
                "item_id": str(c.get("itemId") or ""),
                "at": "",                   # 番茄目录不带时间，留空由页面显示「—」
            })
    cover = page.get("thumbUri") or page.get("thumbUrl") or ""
    m = RE_EXPIRES.search(cover)
    return {
        "bid": str(bid or ""),
        "title": decode(page.get("bookName") or ""),
        "author": decode(page.get("author") or ""),
        "cover": cover,
        "cover_expire": int(m.group(1)) if m else 0,
        "latest": decode(page.get("lastChapterTitle") or ""),
        "chapters": chapters,
    }


def book_meta(bid):
    """书籍页的元信息（目录 + 封面 + 作者），不落库。"""
    html = _get(PAGE % bid)
    page = _state(html).get("page") or {}
    if not page.get("bookName") and not page.get("chapterListWithVolume"):
        raise FetchError("这本书在番茄上打不开，可能下架了")
    return parse_toc(page, bid)


def fetch_toc(bid):
    data = book_meta(bid)
    if not data["chapters"]:
        raise FetchError("目录里没解出章节，可能是这本书下架了")
    return data


def _chapters_cached(bid):
    """按章节号取 itemId，还要知道哪章免费。30 分钟内复用，省一次页面请求。"""
    key = str(bid)
    now = time.time()
    with _CACHE_LOCK:
        hit = _TOC_CACHE.get(key)
        if hit and now - hit[1] < _CACHE_TTL:
            return hit[0]
    data = fetch_toc(bid)
    with _CACHE_LOCK:
        _TOC_CACHE[key] = (data["chapters"], now)
    return data["chapters"]


# ---------- 正文 ----------

def parse_chapter(html, bid="", no=0):
    """从阅读页解出正文。返回 {bid, no, title, paras, words, paid, tip}。"""
    st = _state(html)
    cd = ((st.get("reader") or {}).get("chapterData")
          or (st.get("preview") or {}).get("chapterData") or {})
    if not cd:
        raise FetchError("这一章的页面里没有正文")

    raw = cd.get("content") or ""
    text = decode(_html.unescape(raw))
    paras = []
    for seg in RE_P.findall(text):
        s = _tidy(re.sub(r"<[^>]+>", "", seg))
        if s:
            paras.append(s)
    if not paras:                       # 没有 <p> 就按行切
        for seg in re.split(r"[\r\n]+", re.sub(r"<[^>]+>", "", text)):
            s = _tidy(seg)
            if s:
                paras.append(s)

    body = "".join(paras)
    words = _words(body)
    lock = bool(cd.get("isChapterLock"))
    pay = bool(cd.get("needPay"))
    # 锁定章给的是 160 字左右的试读，末尾没省略号，只能靠目录字段判断
    paid = bool(lock or pay)
    return {
        "bid": str(bid),
        "no": int(no or 0),
        "title": decode(cd.get("title") or "") or ("第 %s 章" % no),
        "paras": paras,
        "words": words,
        "paid": paid,
        "tip": ("这一章只有试读，番茄要登录或在 App 里才能看全" if paid else ""),
    }


def fetch_chapter(bid, no):
    no = int(no or 0)
    chapters = _chapters_cached(bid)
    if no < 1 or no > len(chapters):
        raise FetchError("第 %d 章不在目录里（共 %d 章）" % (no, len(chapters)))
    item_id = chapters[no - 1].get("item_id")
    if not item_id:
        # 缓存里没存 itemId 时退回整本重取一次
        _drop_cache(bid)
        chapters = _chapters_cached(bid)
        item_id = chapters[no - 1].get("item_id") if no <= len(chapters) else ""
        if not item_id:
            raise FetchError("没找到这一章的地址")
    return parse_chapter(_get(READ % item_id), bid, no)


def _drop_cache(bid):
    with _CACHE_LOCK:
        _TOC_CACHE.pop(str(bid), None)


def cover_url(bid):
    """当前可用的封面地址（带签名）。调用方负责按 cover_expire 判断要不要换。"""
    data = fetch_toc(bid)
    return data["cover"], data["cover_expire"]


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    bid = argv[0]
    data = fetch_toc(bid)
    free = sum(1 for c in data["chapters"] if c["token"] == "0")
    print("《%s》 %s" % (data["title"], data["author"]))
    print("  共 %d 章，其中免费 %d 章" % (len(data["chapters"]), free))
    print("  封面 %s（过期 %s）" % (
        data["cover"][:70],
        time.strftime("%Y-%m-%d %H:%M", time.localtime(data["cover_expire"]))
        if data["cover_expire"] else "未知"))
    for c in data["chapters"][:5]:
        print("   %3d %s%s" % (c["no"], c["title"], "" if c["token"] == "0" else "  [付费]"))
    if "--read" in argv:
        no = int(argv[argv.index("--read") + 1])
        ch = fetch_chapter(bid, no)
        print("\n第 %d 章 %s（%d 字，paid=%s）" % (ch["no"], ch["title"], ch["words"], ch["paid"]))
        for p in ch["paras"][:3]:
            print("  " + p[:80])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
