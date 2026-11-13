#!/usr/bin/env python
"""补齐「加入书架」人数（App 详情页封面上那个数字）。

为什么要单独采一轮
------------------
榜单接口（http_cache 里的 bookList）只给 num —— 那是榜单热度，不是书架数。
真正的「加入书架」人数在书籍详情里：

    App ：introinfo.staticScoreInfo.staticInfo = [{"name":"加书架","number":"12.2万"}]
    网页：book.qq.com/book-detail/{bid} 的 SSR HTML，rating-item--favor → 14.3万

两者是同一个口径（同一套服务端统计），网页版只是更新得更勤。所以这里直接抓网页版：
不用模拟器、不用 root、可以并发，2849 本大约 4 分钟。

用法（项目根目录）：
    python collector/detail.py                  补齐库里还没有书架数的书
    python collector/detail.py --all            全部重采一遍
    python collector/detail.py --limit 50       先试 50 本
    python collector/detail.py --no-upload      只落盘，不上报
    python collector/detail.py --push http://IP:8765 --token xxx

产出 data/raw/detail_YYYYmmdd_HHMM.json，结构：
    {"platform": "...", "source": "...", "collected_at": "...", "kind": "details",
     "items": [{"bid": "...", "shelf": "14.3万", "shelf_num": 143000,
                "fans": "4.4万", "fans_num": 44000, "score": 9.0, "ratings": 328}]}
"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
for p in (BASE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import env      # noqa: E402
import push     # noqa: E402

OUT_DIR = os.path.join(ROOT, "data", "raw")
DB_PATH = os.environ.get("WB_DB") or os.path.join(ROOT, "data", "workbench.db")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
DETAIL_URL = "https://book.qq.com/book-detail/%s"

WORKERS = 8
RETRIES = 3

# 「14.3万」「6,952」「624万」「1.2亿」
RE_RATING = (
    r'rating-item--%s.*?rating-count[^>]*>\s*([^<]+?)\s*</span>\s*'
    r'<span class="rating-unit[^>]*>\s*([^<]*?)\s*</span>'
)


def log(msg):
    print(msg, flush=True)


def num_of(text):
    """把「14.3万」这样的展示值折成整数，只用于排序和趋势，不当作精确值。"""
    if not text:
        return 0
    s = str(text).replace(",", "").strip()
    m = re.match(r"^([\d.]+)\s*(万|亿)?", s)
    if not m:
        return 0
    try:
        v = float(m.group(1))
    except ValueError:
        return 0
    unit = m.group(2)
    if unit == "万":
        v *= 10000
    elif unit == "亿":
        v *= 100000000
    return int(round(v))


def _rating(html, kind):
    m = re.search(RE_RATING % kind, html, re.S)
    if not m:
        return "", 0
    text = (m.group(1).strip() + m.group(2).strip())
    return text, num_of(text)


def fetch(bid):
    """抓一本书的详情页，返回 item；抓不到返回 None。"""
    url = DETAIL_URL % bid
    last = None
    for i in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                html = r.read().decode("utf-8", "replace")
            if len(html) < 5000:
                raise ValueError("页面不完整（%d 字节）" % len(html))
            shelf, shelf_num = _rating(html, "favor")
            if not shelf:
                return None                     # 页面上没有书架数，这本书网页版没上
            fans, fans_num = _rating(html, "fans")
            score = None
            m = re.search(r'rating-item--score.*?rating-count[^>]*>\s*([\d.]+)\s*<', html, re.S)
            if m:
                try:
                    score = float(m.group(1))
                except ValueError:
                    score = None
            ratings = 0
            m = re.search(r"(\d+)人评分", html)
            if m:
                ratings = int(m.group(1))
            return {"bid": str(bid), "shelf": shelf, "shelf_num": shelf_num,
                    "fans": fans, "fans_num": fans_num,
                    "score": score, "ratings": ratings}
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = e
        except Exception as e:                  # noqa: BLE001
            last = e
        time.sleep(0.8 * (i + 1))
    log("  ✗ %s %s" % (bid, last))
    return None


def pending_bids(all_books=False):
    """从库里取需要补的 bid：默认只要还没有书架数的。"""
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH.replace("\\", "/"), uri=True)
    con.row_factory = sqlite3.Row
    cols = {r["name"] for r in con.execute("PRAGMA table_info(books)")}
    if all_books or "shelf" not in cols:
        sql = "SELECT bid FROM books ORDER BY bid"
    else:
        sql = "SELECT bid FROM books WHERE shelf IS NULL OR shelf = '' ORDER BY bid"
    try:
        return [str(r["bid"]) for r in con.execute(sql)]
    finally:
        con.close()


def save(items):
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M")
    path = os.path.join(OUT_DIR, "detail_%s.json" % stamp)
    payload = {
        "platform": "QQ阅读 网页版",
        "source": "book.qq.com/book-detail SSR",
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "kind": "details",
        "items": items,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return path, payload


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    def opt(name, default=None):
        if name in argv:
            i = argv.index(name)
            return argv[i + 1] if i + 1 < len(argv) else default
        return default

    flags = {a for a in argv if a.startswith("-")}
    limit = int(opt("--limit", "0") or 0)
    workers = int(opt("--workers", WORKERS) or WORKERS)

    if not os.path.exists(DB_PATH):
        log("找不到数据库：%s（先启动一次后端）" % DB_PATH)
        return 1

    bids = pending_bids(all_books="--all" in flags)
    if limit:
        bids = bids[:limit]
    if not bids:
        log("没有需要补的书 —— 全部都有加书架数了（要重采加 --all）")
        return 0

    log("待补 %d 本，%d 并发，预计 %.1f 分钟" % (len(bids), workers, len(bids) * 0.6 / workers / 60))
    log("-" * 58)

    items, done, fail = [], 0, 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for r in pool.map(fetch, bids):
            done += 1
            if r:
                items.append(r)
            else:
                fail += 1
            if done % 100 == 0 or done == len(bids):
                cost = time.time() - t0
                eta = cost / done * (len(bids) - done)
                log("  %4d/%d  成功 %d  失败 %d  已用 %.0fs  剩约 %.0fs" % (
                    done, len(bids), len(items), fail, cost, eta))

    if not items:
        log("一本都没抓到，检查网络或页面结构")
        return 1

    path, payload = save(items)
    log("\n共 %d 本（失败 %d），耗时 %.1f 分钟\n  %s" % (
        len(items), fail, (time.time() - t0) / 60, path))

    if "--no-upload" in flags:
        log("\n已跳过上报（--no-upload）")
        return 0

    base = push.resolve_base(opt("--push"))
    token = opt("--token")
    log("\n上报后端 %s" % base)
    log("-" * 58)
    try:
        r = push.publish(payload, base=base, token=token, endpoint="/api/details")
    except Exception as e:                       # noqa: BLE001
        log("  上报失败：%s" % e)
        log("  数据已落盘，稍后可手动补推：")
        log("  python collector/detail.py --no-upload  # 或直接用下面的文件")
        log('  curl -X POST "%s/api/details" -H "Content-Type: application/json" --data-binary @%s'
            % (base, path))
        return 0
    log("  已入库：%d 本补上加入书架数" % (r.get("updated") or 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
