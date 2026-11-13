#!/usr/bin/env python
"""补齐「加入书架」人数（App 详情页封面上那个数字）+「催更」次数。

为什么要单独采一轮
------------------
榜单接口（http_cache 里的 bookList）只给 num —— 那是榜单热度，不是书架数。
真正的「加入书架」人数在书籍详情里：

    App ：introinfo.staticScoreInfo.staticInfo = [{"name":"加书架","number":"12.2万"}]
    网页：book.qq.com/book-detail/{bid} 的 SSR HTML，rating-item--favor → 14.3万

两者是同一个口径（同一套服务端统计），网页版只是更新得更勤。所以这里直接抓网页版：
不用模拟器、不用 root、可以并发，2849 本大约 4 分钟。

催更数在哪
----------
App 里把一本书翻到最后一页（读完最后一章），页脚会显示「N 次催更」，那是整本书
的累计催更次数。数据在书末页接口里，游客也能取：

    GET https://commontgw.reader.qq.com/book/chapterOver?bid={bid}
    → endPageInfo: [{"support": true, "bx": "9386", "unit": "次"}, ...]

bx 就是次数，可能是 "1.0" 这种小数写法。它跟榜单热度、加入书架都不是一回事，
单独存一列（books.urge）。这个接口很轻（约 0.2 秒），跟详情页一起采。

用法（项目根目录）：
    python collector/detail.py                  补齐库里还没有书架数 / 催更数的书
    python collector/detail.py --all            全部重采一遍
    python collector/detail.py --limit 50       先试 50 本
    python collector/detail.py --no-upload      只落盘，不上报
    python collector/detail.py --push http://IP:8765 --token xxx

产出 data/raw/detail_YYYYmmdd_HHMM.json，结构：
    {"platform": "...", "source": "...", "collected_at": "...", "kind": "details",
     "items": [{"bid": "...", "shelf": "14.3万", "shelf_num": 143000,
                "fans": "4.4万", "fans_num": 44000, "score": 9.0, "ratings": 328,
                "urge": 9386}]}
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
# 催更接口要 App 的 UA，网页版 UA 会 403
APP_UA = ("Mozilla/5.0 (Linux; Android 15; V2364A Build/V417IR; wv) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Version/4.0 Chrome/110.0.5481.154 Safari/537.36")
DETAIL_URL = "https://book.qq.com/book-detail/%s"
URGE_URL = "https://commontgw.reader.qq.com/book/chapterOver?bid=%s"

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
    """抓一本书的详情页 + 书末页催更数，返回 item；两边都没拿到返回 None。"""
    item = _detail(bid)
    urge = fetch_urge(bid)
    if urge is not None:
        if item is None:
            item = {"bid": str(bid), "shelf": "", "shelf_num": 0,
                    "fans": "", "fans_num": 0, "score": None, "ratings": 0}
        item["urge"] = urge
    return item


def _detail(bid):
    """网页详情页：加入书架 / 书友 / 评分。没有这本书的详情页返回 None。"""
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


def fetch_urge(bid):
    """书末页的催更累计次数（读完最后一章时页脚显示的那个数）。

    返回 int；接口没给或请求失败返回 None（和「0 次催更」是两回事，
    0 要如实保留，不能和没采到混在一起）。
    """
    url = URGE_URL % bid
    last = None
    for i in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": APP_UA,
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            })
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
            data = json.loads(raw.decode("utf-8", "replace"))
            if str(data.get("code")) != "0":
                return None
            for info in (data.get("endPageInfo") or []):
                if isinstance(info, dict) and "bx" in info:
                    return num_of(info.get("bx"))
            return None
        except Exception as e:                  # noqa: BLE001
            last = e
        time.sleep(0.5 * (i + 1))
    log("  ✗ 催更 %s %s" % (bid, last))
    return None


def pending_bids(all_books=False):
    """从库里取需要补的 bid：默认只要还缺书架数或催更数的。"""
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH.replace("\\", "/"), uri=True)
    con.row_factory = sqlite3.Row
    cols = {r["name"] for r in con.execute("PRAGMA table_info(books)")}
    if all_books or "shelf" not in cols:
        sql = "SELECT bid FROM books ORDER BY bid"
    elif "urge" in cols:
        sql = ("SELECT bid FROM books WHERE shelf IS NULL OR shelf = ''"
               " OR urge IS NULL ORDER BY bid")
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
        "platform": "QQ阅读 网页版 + App 书末页",
        "source": "book.qq.com/book-detail SSR + commontgw/chapterOver",
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
        log("没有需要补的书 —— 书架数和催更数都在库里了（要重采加 --all）")
        return 0

    log("待补 %d 本，%d 并发，预计 %.1f 分钟" % (len(bids), workers, len(bids) * 0.9 / workers / 60))
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
    n_urge = sum(1 for it in items if "urge" in it)
    log("\n共 %d 本（失败 %d，含催更 %d），耗时 %.1f 分钟\n  %s" % (
        len(items), fail, n_urge, (time.time() - t0) / 60, path))

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
    log("  已入库：%d 本补上加入书架数 / 催更数" % (r.get("updated") or 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
