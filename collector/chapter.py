#!/usr/bin/env python
"""拆书用的章节抓取：目录 + 正文。

数据来源
--------
QQ阅读 网页版（book.qq.com）是服务端直出，不用登录、不用模拟器：

    目录  https://book.qq.com/book-chapter/{bid}
    正文  https://book.qq.com/book-read/{bid}/{第几章}

两者都在 HTML 里内嵌了一份 SSR 状态（window.__NUXT__），章节列表和正文
就在里面。目录页一次给全书（实测 13297 章也是全量，约 700KB），
正文页只给当前这一章。

免费 / 付费的边界
-----------------
免费章节：chapterContent 是完整正文，例如 2068 字，和 totalWords 对得上。
付费章节：正文只有 200 字左右的试读片段，末尾是「...」，页面底部写着
          「登录订阅本章」。这不是抓取失败，是平台本来就只给这么多。

拆书只做免费章节：目录照样整本拉（免费标记要靠整本的 token 分布来校准），
但对外只列、只读免费的那几十章。付费章不展示，也不去请求。

免费标记怎么来
--------------
目录里每章有 free 字段，但 SSR 压缩后是变量名（free:a / free:d），
值读不出来。用第 1 章的正文反推：第 1 章免费 → free:a 就代表免费，
于是全书里 free:a 的都免费。一次请求就能给整本书标好。

用法（项目根目录）：
    python collector/chapter.py 61592341            # 看目录
    python collector/chapter.py 61592341 --read 1   # 看第 1 章
"""
import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TOC_URL = "https://book.qq.com/book-chapter/%s"
READ_URL = "https://book.qq.com/book-read/%s/%d"

RETRIES = 2
TIMEOUT = 25

# 目录里每一章的元信息。chapterName 可能是字符串，也可能是压缩后的变量名
# （长篇的第 1 章常常是变量），所以两个分支都要接。
RE_ITEM = re.compile(
    r'\{cid:\w+,chapterName:("(?:[^"\\]|\\.)*"|\w+),free:(\w+),purchased:(\w+)')
RE_META = r'<meta[^>]*property="og:novel:%s"[^>]*content="([^"]*)"'
RE_TITLE = re.compile(r'<title>(.+?)</title>', re.S)


class FetchError(Exception):
    pass


def _get(url, timeout=TIMEOUT):
    """取页面，失败重试。返回解码后的 HTML。"""
    last = None
    for i in range(RETRIES + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Referer": "https://book.qq.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = "HTTP %s" % e.code
            if e.code in (403, 404):
                raise FetchError("这本书在网页上没有%s" % ("目录" if "chapter" in url else "这一章"))
        except Exception as e:                       # noqa: BLE001
            last = str(e) or e.__class__.__name__
        if i < RETRIES:
            time.sleep(0.7 * (i + 1))
    raise FetchError("连不上 QQ阅读（%s）" % last)


# ---------- JS 字符串 ----------

_ESC = (("\\u003C", "<"), ("\\u003E", ">"), ("\\u002F", "/"),
        ("\\/", "/"), ("\\r", ""), ("\\n", ""), ("\\t", " "),
        ('\\"', '"'), ("\\'", "'"))


def _unescape(raw):
    """把 JS 字面量里的转义还原成正常文本。"""
    t = raw
    for a, b in _ESC:
        t = t.replace(a, b)
    # 剩下没覆盖到的 \uXXXX 一起处理
    t = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), t)
    return t.replace("\\\\", "\\")


def _grab_string(html, key):
    """从 SSR 状态里取 key:"..." 的字符串值，正确处理转义引号。"""
    i = html.find(key + ':"')
    if i < 0:
        return None
    j = i + len(key) + 2
    out = []
    while j < len(html):
        ch = html[j]
        if ch == "\\":
            out.append(html[j:j + 2])
            j += 2
            continue
        if ch == '"':
            break
        out.append(ch)
        j += 1
    return "".join(out)


def _meta(html, name):
    m = re.search(RE_META % re.escape(name), html)
    return m.group(1) if m else ""


# ---------- 目录 ----------

def parse_toc(html, bid=""):
    """从目录页 HTML 解出章节列表。

    返回 {bid, title, author, latest, chapters:[{no, title, token, at}], paid_hint}
    token 是 free 字段的原始值（a / d 这种变量名），用来反推免费标记。
    """
    chapters = []
    for m in RE_ITEM.finditer(html):
        name, token = m.group(1), m.group(2)
        if name.startswith('"'):
            title = _unescape(name[1:-1]).strip()
        else:
            title = ""                     # 压缩成变量了，靠序号兜底
        chapters.append({
            "no": len(chapters) + 1,
            "title": title,
            "token": token,
            "at": "",
        })

    # publishTime 单独补，正则里没带上是为了兼容字段缺失的老页面
    times = re.findall(r'free:\w+,purchased:\w+,publishTime:"([^"]*)"', html)
    for i, t in enumerate(times):
        if i < len(chapters):
            chapters[i]["at"] = t

    return {
        "bid": str(bid or ""),
        "title": _meta(html, "book_name") or _title_name(html),
        "author": _meta(html, "author"),
        "latest": _meta(html, "latest_chapter_name"),
        "chapters": chapters,
    }


def _title_name(html):
    m = RE_TITLE.search(html)
    if not m:
        return ""
    t = re.sub(r"\s+", " ", m.group(1)).strip()
    return re.split(r"[_|]", t)[0].strip()


def fetch_toc(bid):
    html = _get(TOC_URL % bid)
    data = parse_toc(html, bid)
    if not data["chapters"]:
        raise FetchError("目录里没解出章节，可能是这本书下架了")
    return data


# ---------- 正文 ----------

def parse_chapter(html, bid="", no=0):
    """从阅读页 HTML 解出正文。

    返回 {title, paras, words, paid, tip}
    paid=True 表示只拿到试读片段，paras 里仍是那段试读，前端要提示。
    """
    raw = _grab_string(html, "chapterContent")
    if raw is None:
        raw = _grab_string(html, "content")
    text = _unescape(raw or "")

    paras = []
    if "<p" in text:
        for seg in re.findall(r"<p[^>]*>(.*?)</p>", text, re.S):
            s = re.sub(r"<[^>]+>", "", seg)
            s = _tidy(s)
            if s:
                paras.append(s)
    else:
        for seg in re.split(r"[\r\n]+", re.sub(r"<[^>]+>", "", text)):
            s = _tidy(seg)
            if s:
                paras.append(s)

    body = "".join(paras)
    words = len(re.sub(r"\s", "", body))

    # 付费判断：页面底部会写「登录订阅本章」，正文末尾是省略号且明显偏短
    plain_page = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    plain_page = re.sub(r"<[^>]+>", " ", plain_page)
    hint = ("订阅本章" in plain_page
            or "上QQ阅读APP看后续" in plain_page
            or "购买本章" in plain_page)
    short = words < 600 and body.rstrip().endswith(("...", "…"))
    paid = bool(hint and (short or words < 1200))

    return {
        "bid": str(bid),
        "no": int(no or 0),
        "title": _chapter_title(html, no),
        "paras": paras,
        "words": words,
        "paid": paid,
        "tip": ("付费章节，网页只给这么多试读" if paid else ""),
    }


def _tidy(s):
    s = s.replace("\u3000", " ").replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", s).strip()


def _chapter_title(html, no):
    m = RE_TITLE.search(html)
    if not m:
        return "第 %s 章" % no
    t = re.sub(r"\s+", " ", m.group(1)).strip()
    # 列车求生，开局时空列车_第1章 骑士通票！_在线阅读-QQ阅读
    for tail in ("在线阅读-QQ阅读", "全文阅读-QQ阅读", "-QQ阅读"):
        if t.endswith(tail):
            t = t[:-len(tail)]
            break
    if "_" in t:
        t = t.split("_", 1)[1]
    return t.strip() or ("第 %s 章" % no)


def fetch_chapter(bid, no):
    html = _get(READ_URL % (bid, int(no)))
    return parse_chapter(html, bid, no)


# ---------- 免费标记校准 ----------

def mark_free(chapters, first_paid):
    """用第 1 章的实测结果反推所有章节的免费标记。

    chapters[0].token 就是第 1 章的 free 变量。若第 1 章是付费的，
    说明另一个取值才代表免费。拿不到结论时返回 None（不确定）。

    free_token 为 "-" 表示整本都是付费章（一个免费章都没有）。
    """
    if not chapters or first_paid is None:
        return None
    tokens = {c["token"] for c in chapters}
    first = chapters[0]["token"]
    if len(tokens) == 1:
        free_token = "-" if first_paid else first      # 整本同一状态
    elif first_paid:
        free_token = next(iter(tokens - {first}), None)
    else:
        free_token = first
    return {"free_token": free_token,
            "free": [1 if (free_token not in (None, "-") and c["token"] == free_token) else 0
                     for c in chapters]}


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    bid = argv[0]
    toc = fetch_toc(bid)
    first = fetch_chapter(bid, 1)
    info = mark_free(toc["chapters"], first["paid"])
    free = [c for c, ok in zip(toc["chapters"], info["free"]) if ok] if info else []
    print("%s · %s · 共 %d 章，免费 %d 章" % (
        toc["title"], toc["author"], len(toc["chapters"]), len(free)))
    if not free:
        print("  这本没有免费章节（或校准失败），拆书页会是空的")
    for c in free[:8]:
        print("  %-5d %s" % (c["no"], c["title"] or "(第 %d 章)" % c["no"]))
    if len(free) > 8:
        print("  ...  最后一个免费章：第 %d 章" % free[-1]["no"])

    if "--read" in argv:
        i = argv.index("--read")
        no = int(argv[i + 1]) if i + 1 < len(argv) else 1
        ch = fetch_chapter(bid, no)
        print("\n=== 第 %d 章 · %s（%d 字）%s" % (ch["no"], ch["title"], ch["words"],
                                              "  [付费试读]" if ch["paid"] else ""))
        for p in ch["paras"][:6]:
            print("  " + p[:80])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
