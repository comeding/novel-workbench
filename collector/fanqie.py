# -*- coding: utf-8 -*-
"""番茄小说 · 扫榜采集器

两个榜：

1. **新书榜**（分都市脑洞 / 玄幻脑洞两个分类）
   网页 https://fanqienovel.com/rank/1_1_262（262 都市脑洞、257 玄幻脑洞）
   GET /api/rank/category/list?app_id=2503&rank_list_type=3&offset=0&limit=100
       &category_id=<262|257>&rank_version=&gender=1&rankMold=1

2. **新书总榜**（不分分类，男频连载按热度排）
   网页 https://fanqienovel.com/library/audience1-stat1-count0/page_1?sort=hottes
   GET /api/author/library/book_list/v0/?page_count=18&page_index=<n>
       &gender=1&category_id=-1&creation_status=1&word_count=0&book_type=-1&sort=0

坑都写在下面，脚本里逐个处理了：

1. **书名/作者是字体混淆的**（私有区字符 U+E3xx）。网页靠一套
   SourceHanSansSC-Normal 的子集字体把它们画成汉字。**两个页面用的不是同一套
   字体**，映射表得分开存：新书榜是 fanqie_font.json，总榜是
   fanqie_font_lib.json。字体 hash 变了脚本会警告，那时重跑
   `python dev/make_fanqie_font.py [--lib]` 重建。

2. **新书榜少传 gender / rankMold 会静默退回另一个榜**。只给 category_id 的话
   接口照样返回 200、照样有 100 条，但那是阅读榜的书（两三百万字的老书），跟
   页面上的 20 万字新书完全对不上，很容易误以为采对了。必须带
   gender=1&rankMold=1。rank_version 反而不重要，传空字符串就返回最新一版。

3. **总榜的字数/在读也是混淆的字符串**（"74.1万人在读" 这种），不是数字，
   要先把字还原再解析中文数量。总榜每页固定 18 条，要翻页才够数。

4. **字体 key 在响应头里**。总榜是纯客户端渲染，页面 HTML 里没有字体链接，
   `x-tt-zhal` 响应头里的 `f=<hash>` 才是当前字体。

5. **总榜的在读数是近似值，而且没有更精确的来源**。书库接口给「74.1万人在读」，
   折成整数只剩万级精度（±500）。试过从详情页 `/page/{bid}` 的 SSR 里抠
   `"readCount":740518`，但那个数走 CDN 缓存，同一本书连着请求会在新旧两版之间
   来回跳（实测 734770 / 740518 交替，差 5700），比接口的舍入还不可靠；
   榜单接口自己反而刷得快（7 分钟内 74.1万 → 73.5万）。

   所以日增统一用接口值（`reads_exact=0`，误差 ±500/次，跨天相减最大 ±1000）。
   新书榜接口给的是个位精确的 read_count（`reads_exact=1`），日增准到个位。

用「每日新增在读」的方式：后端 ingest 每次都会把 (bid, 采集时间, 在读) 落进
reads_hist 表，查询时取 18~96 小时前最近的一条做差。所以要**每天至少采一次**，
而且第一次采完当天是算不出增量的（没有基准快照）。

用法：
    python collector/fanqie.py                 # 两个榜都采并入库
    python collector/fanqie.py 番茄新书总榜      # 只采总榜
    python collector/fanqie.py --no-upload     # 只落盘
    python collector/fanqie.py --list          # 打印分类与 cid
"""
import csv
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
OUT_DIR = os.environ.get("WB_RAW") or os.path.join(ROOT, "data", "raw")
if not os.path.isabs(OUT_DIR):
    OUT_DIR = os.path.join(ROOT, OUT_DIR)
FONT_FILE = os.path.join(BASE, "fanqie_font.json")
LIB_FONT_FILE = os.path.join(BASE, "fanqie_font_lib.json")

API = "https://fanqienovel.com/api/rank/category/list"
PAGE = "https://fanqienovel.com/rank/1_1_%d"
APP_ID = 2503
GENDER = 1                              # 1 = 男频
RANK_LIST_TYPE = 3                      # 3 = 新书榜
RANK_MOLD = 1                           # 1 = 新书榜（缺了它接口会退回阅读榜）

LIB_API = "https://fanqienovel.com/api/author/library/book_list/v0/"
LIB_SIZE = 18                           # 接口每页固定 18 条，传大了也按 18 给
LIB_LIMIT = 200                         # 新书总榜取前 200 条（前几页 18 条/页，之后 10 条/页）
LIB_MAX_PAGES = 30                      # 兜底：接口异常时不至于一直翻下去
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

BOARDS = {
    "番茄新书榜": {
        "categories": {
            "都市脑洞": 262,
            "玄幻脑洞": 257,
        },
    },
    # 书库页的「男频 · 连载中 · 全部字数 · 按热度」，不分分类
    "番茄新书总榜": {"lib": True},
}

RE_FONT = re.compile(r"awesome-font/c/([0-9a-f]+)\.woff2")


def log(msg):
    print(msg, flush=True)


def _get(url, accept="text/html", with_headers=False):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": accept,
        "Accept-Encoding": "gzip", "Referer": "https://fanqienovel.com/",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        hdr = dict(r.headers)
    body = raw.decode("utf-8", "replace")
    return (body, hdr) if with_headers else body


def load_font(path):
    """私有区字符 → 汉字。读不到就退化成原样输出，至少不崩。"""
    try:
        with io.open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("map") or {}, d.get("font") or ""
    except (OSError, ValueError):
        return {}, ""


FONT_MAP, FONT_HASH = load_font(FONT_FILE)
LIB_FONT_MAP, LIB_FONT_HASH = load_font(LIB_FONT_FILE)


def decode(text, mp=None):
    """还原私有区字符。mp 默认用新书榜那套表；总榜要显式传 LIB_FONT_MAP。"""
    if not text:
        return ""
    mp = FONT_MAP if mp is None else mp
    if not mp:
        return text
    return "".join(mp.get(ch, ch) if 0xE000 <= ord(ch) <= 0xF8FF else ch
                   for ch in text)


RE_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*(亿|万)?")


def norm_intro(v, limit=2000):
    """简介：统一换行、去行尾空格和多余空行，过长才截。

    以前是 replace("\\n", " ")[:200]，换行没了、超 200 字砍掉，
    详情里就只剩半截。现在保留段落，前端按 pre-wrap 显示。
    """
    s = ("" if v is None else str(v)).replace("\r\n", "\n").replace("\r", "\n")
    out, blank = [], False
    for ln in (x.rstrip() for x in s.split("\n")):
        if not ln:
            if not blank and out:
                out.append("")
            blank = True
            continue
        blank = False
        out.append(ln)
    return "\n".join(out).strip()[:limit]


def parse_num(text):
    """「74.1万人在读」→ 741000，「23.3万字」→ 233000，「3521」→ 3521。"""
    m = RE_NUM.search(text or "")
    if not m:
        return 0
    v = float(m.group(1))
    unit = m.group(2)
    if unit == "亿":
        v *= 100000000
    elif unit == "万":
        v *= 10000
    return int(round(v))


def page_font(cid):
    """从榜单页拿字体 hash，用来判断混淆字体有没有换版本。"""
    html = _get(PAGE % cid)
    m = RE_FONT.search(html)
    return m.group(1) if m else ""


def fetch_rank(cid):
    """一个分类的一整页（limit=100 一次给全）。返回 (books, total)。"""
    q = urllib.parse.urlencode({
        "app_id": APP_ID, "rank_list_type": RANK_LIST_TYPE,
        "offset": 0, "limit": 100,
        "category_id": cid, "rank_version": "",
        "gender": GENDER, "rankMold": RANK_MOLD,
    })
    d = json.loads(_get(API + "?" + q, "application/json"))
    data = d.get("data") or {}
    return data.get("book_list") or [], int(data.get("total_num") or 0)


def fetch_category(cid):
    """字体先验一下版本，再取数据；空结果按 3 秒退避重试两次。"""
    try:
        font = page_font(cid)
    except (urllib.error.URLError, OSError):
        font = ""
    if font and FONT_HASH and font != FONT_HASH:
        log("    ! 混淆字体换了（%s → %s），书名可能还原不全，需要重做 fanqie_font.json"
            % (FONT_HASH, font))
    for step in range(3):
        books, total = fetch_rank(cid)
        if books:
            return books, total
        if step < 2:
            time.sleep(3)
    return [], 0


def fetch_lib(page_index):
    """书库（新书总榜）的一页。返回 (books, has_more, font_hash)。"""
    q = urllib.parse.urlencode({
        "page_count": LIB_SIZE, "page_index": page_index,
        "gender": GENDER, "category_id": -1,
        "creation_status": 1,           # 1 = 连载中（页面上就是 stat1）
        "word_count": 0,                # 0 = 不限字数
        "book_type": -1, "sort": 0,     # 0 = 热度（页面 ?sort=hottes）
    })
    body, hdr = _get(LIB_API + "?" + q, "application/json", with_headers=True)
    # 字体 key 只在这个响应头里：x-tt-zhal: k=..;f=<hash>;d1=..
    m = re.search(r"(?:^|;)f=([0-9a-f]+)", hdr.get("x-tt-zhal") or "")
    font = m.group(1) if m else ""
    data = (json.loads(body).get("data") or {})
    return data.get("book_list") or [], bool(data.get("has_more")), font


def fetch_total(limit=LIB_LIMIT):
    """翻页取总榜，按 bid 去重。前几页 18 条/页，翻深了变成 10 条/页。"""
    rows, seen, font = [], set(), set()
    for i in range(LIB_MAX_PAGES):
        books, has_more, f = fetch_lib(i)
        if f:
            font.add(f)
        if not books:
            break
        fresh = 0
        for b in books:
            bid = str(b.get("book_id") or "")
            if not bid or bid in seen:
                continue
            seen.add(bid)
            rows.append(b)
            fresh += 1
        log("    第 %-2d 页 +%2d 条，累计 %3d 条" % (i + 1, fresh, len(rows)))
        if len(rows) >= limit or not has_more or not fresh:
            break
        time.sleep(0.3)                 # 别把书库接口打急了
    if font and LIB_FONT_HASH not in font:
        log("    ! 混淆字体换了（%s → %s），总榜书名可能还原不全，"
            "需要重做 fanqie_font_lib.json" % (LIB_FONT_HASH, "/".join(sorted(font))))
    return rows[:limit]


def parse_lib(books):
    """总榜字段：字数/在读是混淆过的中文数量字符串，得先还原再解析。

    在读数只有「74.1万」这种精度（±500），没有更准的来源：详情页 /page/{bid}
    的 SSR 里虽然有个位精确的 readCount，但它走 CDN 缓存，同一本书连着请求会在
    新旧两版之间来回跳（实测 734770 / 740518 交替，差 5700），比接口的舍入还不可靠；
    榜单接口自己反而刷得快（7 分钟内 74.1万 → 73.5万）。日增要跨天相减，两次必须
    同口径，所以统一用接口值，`reads_exact` 标记为 0，前端会提示这是万级近似。
    """
    rows = []
    for i, b in enumerate(books, 1):
        title = decode(b.get("book_name"), LIB_FONT_MAP)
        if not title:
            continue
        bid = str(b.get("book_id") or "")
        reads = parse_num(decode(b.get("read_count"), LIB_FONT_MAP))
        words = parse_num(decode(b.get("word_count"), LIB_FONT_MAP))
        try:
            ts = int(b.get("last_chapter_time") or 0)
        except (TypeError, ValueError):
            ts = 0
        rows.append({
            "rank": i,
            "bid": bid,
            "title": title,
            "author": decode(b.get("author"), LIB_FONT_MAP),
            # 书库接口不给分类。留空，入库时不会覆盖新书榜那边带下来的分类
            "cat2": "",
            "cat3": "",
            "label": "连载" if str(b.get("creation_status")) == "1" else "完结",
            "words_wan": round(words / 10000.0, 1) if words else 0,
            "total_words": words,
            "collect_num": reads,       # 番茄没有「加入书架」，这列是在读人数
            # 0 = 由「74.1万人在读」折出来的近似值（±500），日增只能看千位以上
            "reads_exact": 0,
            "badge": "",
            "last_chapter": "",         # 书库接口不给章节名，只给时间，页面显示「—」
            "update_date": time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "",
            "intro": norm_intro(decode(b.get("abstract"), LIB_FONT_MAP)),
        })
    return [r for r in rows if r["bid"]]


def parse(books, cat):
    """转成入库行，字段和 QQ阅读 那边保持一致，ingest 一套逻辑吃两边。"""
    rows = []
    for i, b in enumerate(books, 1):
        try:
            words = int(b.get("wordNumber") or 0)
        except (TypeError, ValueError):
            words = 0
        try:
            ts = int(b.get("lastChapterUpdateTime") or 0)
        except (TypeError, ValueError):
            ts = 0
        try:
            reads = int(b.get("read_count") or b.get("readCount") or 0)
        except (TypeError, ValueError):
            reads = 0
        rows.append({
            "rank": i,
            "bid": str(b.get("bookId") or ""),
            "title": decode(b.get("bookName")),
            "author": decode(b.get("author")),
            "cat2": cat,
            "cat3": decode(b.get("categoryV2") or ""),
            "label": "连载" if str(b.get("creationStatus")) == "1" else "完结",
            "words_wan": round(words / 10000.0, 1) if words else 0,
            "total_words": words,
            "collect_num": reads,          # 番茄没有「加入书架」，这列是在读人数
            "reads_exact": 1,              # 榜单接口给的就是精确整数，不是「x.x万」
            "badge": "",
            "last_chapter": decode(b.get("lastChapterTitle")),
            "update_date": time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "",
            "intro": norm_intro(decode(b.get("abstract") or "")),
        })
    return [r for r in rows if r["bid"] and r["title"]]


def collect(board_names=None):
    t0 = time.time()
    names = board_names or list(BOARDS.keys())
    result = {
        "platform": "番茄小说",
        "source": "fanqienovel.com（榜单 /api/rank/category/list + 书库 /api/author/library/book_list）",
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "boards": {},
    }
    for name in names:
        cfg = BOARDS.get(name)
        if not cfg:
            log("[%s] 未配置分类，跳过" % name)
            continue

        # 书库那种不分分类的总榜
        if cfg.get("lib"):
            log("[%s]" % name)
            log("  书库页 · 男频 · 连载中 · 按热度，取前 %d 条" % LIB_LIMIT)
            try:
                books = fetch_total()
            except (urllib.error.URLError, OSError, ValueError) as e:
                log("  ✗ 请求失败：%s" % e)
                continue
            rows = parse_lib(books)
            if not rows:
                log("  ✗ 没有数据（书库接口可能改了）")
                continue
            column = {"cid": "", "name": "%s" % name,
                      "push_name": "番茄小说新书总榜", "unit": "在读"}
            result["boards"][name] = {
                "column": column,
                "total": len(rows),
                "books": rows,
                "categories": {"全部": {"column": column, "total": len(rows),
                                        "books": rows}},
                "full": True,
            }
            log("  共 %d 条  %s" % (len(rows), rows[0]["title"][:20]))
            continue

        cats = cfg.get("categories") or {}
        log("[%s]" % name)
        log("  分类 %d 个" % len(cats))
        data_map = {}
        for cat, cid in cats.items():
            try:
                books, total = fetch_category(cid)
            except (urllib.error.URLError, OSError, ValueError) as e:
                log("  ✗ %-6s cid=%s 请求失败：%s" % (cat, cid, e))
                continue
            rows = parse(books, cat)
            if not rows:
                log("  ✗ %-6s cid=%s 没有数据（榜单可能刚更新，稍后再试）" % (cat, cid))
                continue
            column = {"cid": str(cid), "name": "%s · %s" % (name, cat),
                      "push_name": "番茄小说新书榜（%s）" % cat, "unit": "在读"}
            data_map[cat] = {"column": column, "total": total or len(rows), "books": rows}
            log("  %-6s cid=%-4s %3d 条  %s" % (cat, cid, len(rows), rows[0]["title"][:20]))
        if not data_map:
            log("  ✗ 整个榜单都没采到")
            continue
        base = data_map.get("都市脑洞") or list(data_map.values())[0]
        result["boards"][name] = {
            "column": base["column"],
            "total": base["total"],
            "books": base["books"],
            "categories": data_map,
            "full": len(data_map) >= len(cats),
        }
    result["elapsed"] = round(time.time() - t0, 1)
    return result


def save(result):
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M")
    jpath = os.path.join(OUT_DIR, "fanqie_api_%s.json" % stamp)
    with io.open(jpath, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    cpath = os.path.join(OUT_DIR, "fanqie_api_%s.csv" % stamp)
    with io.open(cpath, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["榜单", "分类", "排名", "书名", "作者", "分类2", "标签", "字数(万)",
                    "在读", "最新章节", "更新日期", "cid", "bid", "简介"])
        for board, info in result["boards"].items():
            for cat, ci in (info.get("categories") or {}).items():
                cid = (ci.get("column") or {}).get("cid", "")
                for b in ci["books"]:
                    w.writerow([board, cat, b["rank"], b["title"], b["author"], b["cat2"],
                                b["label"], b["words_wan"], b["collect_num"],
                                b["last_chapter"], b["update_date"], cid, b["bid"],
                                b["intro"]])
    log("\n已保存：\n  %s\n  %s" % (jpath, cpath))
    return jpath


def main(argv):
    push_url = token = ""
    i = 0
    while i < len(argv):
        val = argv[i]
        if val == "--push" and i + 1 < len(argv):
            push_url = argv[i + 1]
            argv = argv[:i] + argv[i + 2:]
        elif val == "--token" and i + 1 < len(argv):
            token = argv[i + 1]
            argv = argv[:i] + argv[i + 2:]
        else:
            i += 1

    if "--list" in argv:
        for name, cfg in BOARDS.items():
            log(name)
            for cat, cid in (cfg.get("categories") or {}).items():
                log("  %-6s %s" % (cat, cid))
        return 0

    no_upload = "--no-upload" in argv
    names = [a for a in argv if not a.startswith("-")] or list(BOARDS.keys())
    result = collect(names)
    rows = sum(len(ci["books"]) for b in result["boards"].values()
               for ci in (b.get("categories") or {}).values())
    if not result["boards"]:
        log("✗ 没有采到任何数据")
        return 1
    log("\n共 %d 个榜单 / %d 条记录，耗时 %.1f 秒" % (
        len(result["boards"]), rows, result.get("elapsed", 0)))
    path = save(result)
    if no_upload:
        log("  没有入库（--no-upload）")
        return 0

    sys.path.insert(0, BASE)
    import push
    base = push.resolve_base(push_url)
    try:
        r = push.publish_file(path, base=base, token=token)
        log("  已入库：%s 条 / %s 个分类" % (r.get("inserted", 0), r.get("cats", 0)))
    except urllib.error.HTTPError as e:
        log("  上报被拒：HTTP %s" % e.code)
        return 3
    except Exception as e:                      # noqa: BLE001
        log("  上报失败：%s" % e)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
