# -*- coding: utf-8 -*-
"""番茄小说 · 新书榜采集器

数据来源（网页与接口是同一份数据，接口一次给全 100 条）：
    https://fanqienovel.com/rank/1_1_262          都市脑洞
    https://fanqienovel.com/rank/1_1_257          玄幻脑洞
    GET /api/rank/category/list?app_id=2503&rank_list_type=3
        &category_id=<262|257>&offset=0&limit=100&rank_version=<页面里的 rankVersion>

两个坑，脚本里都处理了：

1. **书名/作者是字体混淆的**。接口里给的是私有区字符（U+E3xx），网页靠一个
   SourceHanSansSC-Normal 的子集字体把它们画成汉字。该字体是固定的
   （awesome-font/c/dc027189e0ba4cd.woff2），映射表按字形反查后存在
   fanqie_font.json。字体 hash 变了脚本会警告 —— 那时要重新生成映射。

2. **rank_version 只认当前生效的那一版**。页面上的版本号有时比接口数据新
   一档（榜单 10 分钟一更），拿不到数据就往前退一格再试。

用法：
    python collector/fanqie.py                 # 采两个分类并入库
    python collector/fanqie.py 番茄新书榜       # 只采这个榜
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

API = "https://fanqienovel.com/api/rank/category/list"
PAGE = "https://fanqienovel.com/rank/1_1_%d"
APP_ID = 2503
RANK_LIST_TYPE = 3                      # 3 = 新书榜；页面 URL 里的第二个 1 也是这个
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

BOARDS = {
    "番茄新书榜": {
        "categories": {
            "都市脑洞": 262,
            "玄幻脑洞": 257,
        },
    },
}

RE_FONT = re.compile(r"awesome-font/c/([0-9a-f]+)\.woff2")


def log(msg):
    print(msg, flush=True)


def _get(url, accept="text/html"):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": accept,
        "Accept-Encoding": "gzip", "Referer": "https://fanqienovel.com/",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def load_font():
    """私有区字符 → 汉字。读不到就退化成原样输出，至少不崩。"""
    try:
        with io.open(FONT_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("map") or {}, d.get("font") or ""
    except (OSError, ValueError):
        return {}, ""


FONT_MAP, FONT_HASH = load_font()


def decode(text):
    if not text:
        return ""
    if not FONT_MAP:
        return text
    return "".join(FONT_MAP.get(ch, ch) if 0xE000 <= ord(ch) <= 0xF8FF else ch
                   for ch in text)


def page_meta(cid):
    """从榜单页拿 rankVersion 和字体 hash。页面本身也带前 10 名，这里只要元信息。"""
    html = _get(PAGE % cid)
    rv = 0
    i = html.find("window.__INITIAL_STATE__=")
    if i >= 0:
        try:
            st, _ = json.JSONDecoder().raw_decode(html[i + len("window.__INITIAL_STATE__="):])
            rv = int((st.get("rank") or {}).get("rankVersion") or 0)
        except (ValueError, TypeError):
            rv = 0
    m = RE_FONT.search(html)
    return rv, (m.group(1) if m else "")


def fetch_rank(cid, rv):
    """一个分类的一整页（limit=100 一次给全）。返回 (books, total)。"""
    q = urllib.parse.urlencode({
        "app_id": APP_ID, "rank_list_type": RANK_LIST_TYPE,
        "offset": 0, "limit": 100,
        "category_id": cid, "rank_version": rv,
    })
    d = json.loads(_get(API + "?" + q, "application/json"))
    data = d.get("data") or {}
    return data.get("book_list") or [], int(data.get("total_num") or 0)


def fetch_category(cid):
    """rank_version 对不上时接口会返回空，往前退一格（10 分钟）再试。"""
    rv, font = page_meta(cid)
    if font and FONT_HASH and font != FONT_HASH:
        log("    ! 混淆字体换了（%s → %s），书名可能还原不全，需要重做 fanqie_font.json"
            % (FONT_HASH, font))
    if not rv:
        rv = int(time.time()) // 600 * 600
    for step in range(3):
        books, total = fetch_rank(cid, rv - step * 600)
        if books:
            return books, total
    return [], 0


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
            "badge": "",
            "last_chapter": decode(b.get("lastChapterTitle")),
            "update_date": time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "",
            "intro": decode(b.get("abstract") or "").replace("\n", " ")[:200],
        })
    return [r for r in rows if r["bid"] and r["title"]]


def collect(board_names=None):
    t0 = time.time()
    names = board_names or list(BOARDS.keys())
    result = {
        "platform": "番茄小说",
        "source": "fanqienovel.com /api/rank/category/list",
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "boards": {},
    }
    for name in names:
        cfg = BOARDS.get(name)
        if not cfg:
            log("[%s] 未配置分类，跳过" % name)
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
