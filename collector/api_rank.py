# -*- coding: utf-8 -*-
"""QQ阅读榜单直连采集器（无需模拟器、无需登录）

数据来源：App 自身的榜单接口
    GET https://commontgw.reader.qq.com/v7_6_6/listDispatch
        ?actionId=<榜单cid>&action=rank&actionTag=boy
        &pagestamp=1&pageSize=200&rankFlag=1&cateId=0

返回的是明文 JSON，字段与 App 缓存里的完全一致（bid/title/author/catel2name/
catel3name/totalWords/lastChapter/num/free/vipstatus/updateTime/sellWords…）。

每个榜单 cid 对应一个「榜单或分类子榜」，一页即全量（pagestamp=2 返回空）。
一轮 32 个数据集（会员榜 16 + 新书榜 16）实测 14 秒左右，相比模拟器方案
（4~5 分钟 + 环境依赖）快两个数量级。

用法：
    python collector/api_rank.py                  # 采会员榜 + 新书榜，采完入库
    python collector/api_rank.py 会员榜            # 只采一个
    python collector/api_rank.py --push http://127.0.0.1:8765
    python collector/api_rank.py --push https://rank.example.com --token 口令
    python collector/api_rank.py --no-upload      # 只落盘，不入库
    python collector/api_rank.py --list           # 只打印榜单 cid 清单
    python collector/api_rank.py --check          # 自检：网络 / 接口 / 本机服务
"""
import csv
import gzip
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
# 原始 JSON 落盘位置。容器里把 WB_RAW 指到卷上（/data/raw），重建镜像也不丢。
OUT_DIR = os.environ.get("WB_RAW") or os.path.join(ROOT, "data", "raw")
if not os.path.isabs(OUT_DIR):
    OUT_DIR = os.path.join(ROOT, OUT_DIR)
CID_FILE = os.path.join(BASE, "rank_cids.json")

API = "https://commontgw.reader.qq.com/v7_6_6/listDispatch"
UA = ("Mozilla/5.0 (Linux; Android 15; V2364A Build/V417IR; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
      "Chrome/110.0.5481.154 Safari/537.36")

# 榜单 cid 表。cid 会随 App 改版变动，可随时改 collector/rank_cids.json 覆盖。
# 分类顺序 = App 里标签条从左到右的顺序；诸天无限是后来单开的 cid，排在最后。
DEFAULT_CIDS = {
    "会员榜": {
        "tag": "boy",
        "categories": {
            "全部": 516604,
            "玄幻": 533292,
            "奇幻": 533293,
            "武侠": 533294,
            "仙侠": 533295,
            "都市": 533296,
            "历史": 533297,
            "军事": 533298,
            "悬疑": 533299,
            "科幻": 533300,
            "游戏": 533301,
            "体育": 533302,
            "轻小说": 533303,
            "现实": 533304,
            "短篇": 533305,
            "诸天无限": 543428,
        },
    },
    "新书榜": {
        "tag": "boy",
        "categories": {
            "全部": 535331,
            "玄幻": 541256,
            "奇幻": 541257,
            "武侠": 541258,
            "仙侠": 541259,
            "都市": 541260,
            "历史": 541261,
            "军事": 541262,
            "悬疑": 541263,
            "科幻": 541264,
            "游戏": 541265,
            "体育": 541266,
            "轻小说": 541267,
            "现实": 541268,
            "短篇": 541269,
            "诸天无限": 543424,
        },
    },
}


def log(msg):
    print(msg, flush=True)


def load_cids():
    if not os.path.exists(CID_FILE):
        return DEFAULT_CIDS
    try:
        with io.open(CID_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data:
            return data
    except (OSError, ValueError) as e:
        log("  [!] rank_cids.json 读取失败（%s），用内置表" % e)
    return DEFAULT_CIDS


def fetch(cid, tag="boy", size=200, page=1, timeout=25, retry=3):
    """请求一个榜单。失败重试，返回解析后的 dict（失败返回 None）。"""
    qs = urllib.parse.urlencode({
        "actionId": cid,
        "action": "rank",
        "actionTag": tag,
        "pagestamp": page,
        "pageSize": size,
        "rankFlag": 1,
        "cateId": 0,
    })
    url = API + "?" + qs
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "Referer": "https://commontgw.reader.qq.com/",
    })
    last = None
    for i in range(retry):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = e
            time.sleep(0.8 * (i + 1))
    log("    ✗ cid=%s 请求失败：%s" % (cid, last))
    return None


def parse_books(data):
    """把接口返回的 bookList 转成入库行，字段与 App 采集器完全一致。"""
    rows = []
    for i, b in enumerate(data.get("bookList") or [], 1):
        badges = [sw["desc"] for sw in (b.get("sellWords") or [])
                  if isinstance(sw, dict) and sw.get("desc")]
        try:
            total_words = int(b.get("totalWords") or 0)
        except (TypeError, ValueError):
            total_words = 0
        upd = b.get("updateTime")
        rows.append({
            "rank": i,
            "bid": b.get("bid"),
            "title": b.get("title"),
            "author": b.get("author"),
            "cat2": b.get("catel2name") or "",
            "cat3": b.get("catel3name") or "",
            "label": b.get("leftBottomLabel") or "",
            "words_wan": round(total_words / 10000, 1) if total_words else 0,
            "total_words": total_words,
            "collect_num": b.get("num"),
            "badge": " / ".join(badges),
            "last_chapter": b.get("lastChapter"),
            "update_ts": upd,
            "update_date": time.strftime("%Y-%m-%d", time.localtime(upd)) if upd else "",
            "free": b.get("free"),
            "vip_status": b.get("vipstatus"),
            "intro": (b.get("intro") or "").replace("\n", " ")[:200],
        })
    return rows


def fetch_dataset(cid, tag, size=200):
    """拉一个数据集，自动翻页（服务端一页够用时只请求一次）。"""
    data = fetch(cid, tag, size, 1)
    if not data or str(data.get("code")) != "0":
        return None
    books = list(data.get("bookList") or [])
    total = int(data.get("total") or len(books))
    page = 1
    while len(books) < total and len(books) < 600:
        page += 1
        more = fetch(cid, tag, size, page)
        got = (more or {}).get("bookList") or []
        if not got:
            break
        books.extend(got)
    data["bookList"] = books
    return data


def collect(board_names, size=200):
    cids = load_cids()
    t0 = time.time()
    result = {
        "platform": "QQ阅读 App",
        "source": "commontgw.reader.qq.com /v7_6_6/listDispatch",
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "boards": {},
    }
    for name in board_names:
        cfg = cids.get(name)
        if not cfg:
            log("[%s] 未配置 cid，跳过" % name)
            continue
        tag = cfg.get("tag", "boy")
        cats = cfg.get("categories") or {}
        log("[%s]" % name)
        log("  分类 %d 个" % len(cats))
        data_map = {}
        for cat, cid in cats.items():
            data = fetch_dataset(cid, tag, size)
            if not data:
                log("  ✗ %-6s cid=%s 无数据" % (cat, cid))
                continue
            column = data.get("columnInfo") or {"cid": str(cid)}
            books = parse_books(data)
            data_map[cat] = {
                "column": column,
                "total": int(data.get("total") or len(books)),
                "books": books,
            }
            log("  %-6s cid=%-7s %3d 条  %s" % (
                cat, cid, len(books), str(column.get("name"))[:34]))
        if not data_map:
            log("  ✗ 整个榜单都没采到")
            continue
        base = data_map.get("全部") or list(data_map.values())[0]
        result["boards"][name] = {
            "column": base["column"],
            "total": base["total"],
            "books": base["books"],
            "categories": data_map,
            # 只有「配置里每个分类都采到了」才算全量：中途某个分类请求失败时，
            # 不能因为这一轮没采到就把库里它已有的数据当成已下线清掉。
            "full": len(data_map) >= len(cats),
        }
    result["elapsed"] = round(time.time() - t0, 1)
    return result


def save(result):
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M")
    jpath = os.path.join(OUT_DIR, "qqreader_api_%s.json" % stamp)
    with io.open(jpath, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    cpath = os.path.join(OUT_DIR, "qqreader_api_%s.csv" % stamp)
    with io.open(cpath, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["榜单", "分类", "排名", "书名", "作者", "分类2", "子分类", "标签",
                    "字数(万)", "收藏数", "徽章", "最新章节", "更新日期", "cid", "bid", "简介"])
        for board, info in result["boards"].items():
            for cat, ci in (info.get("categories") or {}).items():
                cid = (ci.get("column") or {}).get("cid", "")
                for b in ci["books"]:
                    w.writerow([board, cat, b["rank"], b["title"], b["author"], b["cat2"],
                                b["cat3"], b["label"], b["words_wan"], b["collect_num"],
                                b["badge"], b["last_chapter"], b["update_date"],
                                cid, b["bid"], b["intro"]])
    log("\n已保存：\n  %s\n  %s" % (jpath, cpath))
    return jpath


def check():
    """自检：Python 版本、榜单接口连通性、本机服务是否在跑。"""
    ok = True
    log("Python      %s（%s）" % (sys.version.split()[0], sys.executable))
    log("采集脚本    %s" % os.path.abspath(__file__))

    cids = load_cids()
    n = sum(len((c or {}).get("categories") or {}) for c in cids.values())
    log("榜单配置    %d 个榜单 / %d 个数据集（%s）" % (
        len(cids), n, os.path.basename(CID_FILE) if os.path.exists(CID_FILE) else "内置表"))

    log("接口地址    %s" % API)
    t0 = time.time()
    data = fetch(516604, "boy", 1, 1)
    if data and str(data.get("code")) == "0":
        nb = len(data.get("bookList") or [])
        log("接口连通    OK  %.1f 秒，返回 %d 条（pageSize=1）" % (time.time() - t0, nb))
    else:
        ok = False
        log("接口连通    ✗ 请求失败，检查网络 / 代理 / 防火墙")

    seen = set()
    for base in ("http://127.0.0.1:8765", os.environ.get("WB_PUSH") or ""):
        base = (base or "").rstrip("/")
        if not base or base in seen:
            continue
        seen.add(base)
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=2) as r:
                h = json.loads(r.read().decode("utf-8", "replace"))
            log("本机服务    %s  OK（%s）" % (
                base, "可触发采集" if h.get("collect_enabled") else "只读"))
        except Exception as e:                   # noqa: BLE001
            log("本机服务    %s  未运行（%s）" % (base, e.__class__.__name__))

    log("")
    log("结论：%s" % ("可以采集，直接跑 python collector/api_rank.py" if ok
                     else "接口不通，先解决网络再采集"))
    return 0 if ok else 1


def main(argv):
    if "--check" in argv:
        return check()

    if "--list" in argv:
        for name, cfg in load_cids().items():
            log("%s（actionTag=%s）" % (name, cfg.get("tag", "boy")))
            for cat, cid in (cfg.get("categories") or {}).items():
                log("  %-6s %s" % (cat, cid))
        return 0

    push_url = token = None
    for flag in ("--push", "--token"):
        if flag in argv:
            i = argv.index(flag)
            val = argv[i + 1] if i + 1 < len(argv) else None
            if flag == "--push":
                push_url = val
            else:
                token = val
            argv = argv[:i] + argv[i + 2:]

    # 默认就入库：单点「② 采集数据.bat」的人不需要再记一个 --push 参数。
    # 想只要 CSV/JSON 不上库，加 --no-upload。后端没在跑也不会让采集算失败。
    no_upload = "--no-upload" in argv
    argv = [a for a in argv if a != "--no-upload"]

    names = [a for a in argv if not a.startswith("-")]
    if not names:
        names = list(load_cids().keys())

    result = collect(names)
    n_boards = len(result["boards"])
    n_rows = sum(len(ci["books"]) for b in result["boards"].values()
                 for ci in (b.get("categories") or {}).values())
    if not n_boards:
        log("✗ 没有采到任何数据")
        return 1
    log("\n共 %d 个榜单 / %d 条记录，耗时 %.1f 秒" % (
        n_boards, n_rows, result.get("elapsed", 0)))
    path = save(result)

    if no_upload:
        log("  没有入库（--no-upload），数据在上面两个文件里")
        return 0

    sys.path.insert(0, BASE)
    import push
    base = push.resolve_base(push_url)
    try:
        r = push.publish_file(path, base=base, token=token)
        log("  已入库：%s 条 / %s 个分类" % (r.get("inserted", 0), r.get("cats", 0)))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:160]
        log("  上报被拒：HTTP %s %s" % (e.code, body))
        if e.code == 401:
            log("  口令不对。看服务端 data/.token，或设 WB_TOKEN 环境变量")
        # 数据是采到了（json/csv 都在），只是没进库：退出码 3，让页面显示原因
        return 3
    except Exception as e:          # noqa: BLE001
        log("  上报失败：%s" % e)
        log("  后端没在跑？先双击「① 看榜单.bat」，再补推：")
        log("  python collector/push.py \"%s\" --push %s" % (path, base))
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
