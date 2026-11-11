#!/usr/bin/env python
"""每日一次：把在榜书的「加入书架」数存进历史，供「近 24 小时新增收藏」做差。

为什么需要这一步
----------------
QQ阅读 没有任何渠道直接给「新增收藏」—— 网页详情页、App 详情页、榜单接口
给的都是**累计**的「加书架」人数。想得出增量只有一条路：

    今天的书架数 - 约 24 小时前的书架数 = 这段时段的净增

所以这个脚本的作用就是"每天记一笔"。跑得越规律，差值越接近真实的近 24 小时。

用法（项目根目录）：
    python collector/shelf_snapshot.py                 # 新书榜，约 1 分钟
    python collector/shelf_snapshot.py --board vip     # 会员榜（2600 本，约 4 分钟）
    python collector/shelf_snapshot.py --board all     # 全部
    python collector/shelf_snapshot.py --limit 20      # 先试 20 本
    python collector/shelf_snapshot.py --push http://127.0.0.1:8765
    python collector/shelf_snapshot.py --no-upload     # 只落盘

精度
----
书架数超过 1 万之后，网页只给「1.6万」这种展示值，折成整数误差 ±1000。
新书榜的书基本都在 1 万以下（网页给精确个位），所以这个指标对新书榜可信；
老书（会员榜）算出来只能看个大概，别当准数。
"""
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
for p in (BASE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import detail          # noqa: E402  复用 fetch：同一套网页解析，别再写第二份
import push            # noqa: E402

OUT_DIR = os.environ.get("WB_RAW") or os.path.join(ROOT, "data", "raw")
WORKERS = 8


def log(msg):
    print(msg, flush=True)


def onboard(base, board):
    """在榜书目和上次快照时间，都从服务接口拿。

    为什么不直接读 sqlite：库通常挂在容器里，Windows 宿主机进程和容器里
    的 Linux 进程同时打开同一个 WAL 文件会直接 disk I/O error（实测踩过）。
    走 HTTP 拿一份 bid 列表最省事，也就绕开了这个坑。
    """
    url = "%s/api/bids?board=%s" % (base.rstrip("/"), board)
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            d = json.load(r)
    except Exception as e:                       # noqa: BLE001
        log("取不到在榜书目：%s" % e)
        log("  先起服务（docker compose up -d 或 python backend/server.py），再跑这个脚本")
        return [], ""
    return [str(b) for b in (d.get("bids") or [])], (d.get("last_snapshot_at") or "")


def save(items):
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M")
    path = os.path.join(OUT_DIR, "shelf_%s.json" % stamp)
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
    board = (opt("--board", "new") or "new").strip().lower()
    limit = int(opt("--limit", "0") or 0)
    workers = int(opt("--workers", WORKERS) or WORKERS)
    base = push.resolve_base(opt("--push") or os.environ.get("WB_PUSH") or "http://127.0.0.1:8765")
    token = opt("--token") or os.environ.get("WB_TOKEN") or None

    bids, last_at = onboard(base, board)
    if not bids:
        return 1

    age = None
    if last_at:
        try:
            age = (time.time() - time.mktime(time.strptime(last_at, "%Y-%m-%d %H:%M:%S"))) / 3600
        except (TypeError, ValueError, OverflowError):
            age = None
    if age is None:
        log("首次快照 —— 这次只建立基线，24 小时后才有「新增收藏」可看")
    else:
        log("距上次快照 %.1f 小时" % age)
        if age < 12:
            log("  ⚠ 太密了：两次采样间隔不足 12 小时，差值会很接近 0，参考价值不大")

    if limit:
        bids = bids[:limit]
    if not bids:
        log("库里没有在榜书目，先跑一次 collector/api_rank.py")
        return 1

    log("榜单 %s，待采 %d 本，%d 并发，预计 %.0f 秒" % (
        board, len(bids), workers, len(bids) * 0.5 / workers))
    log("-" * 58)

    items, done, fail = [], 0, 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for r in pool.map(detail.fetch, bids):
            done += 1
            if r:
                items.append(r)
            else:
                fail += 1
            if done % 100 == 0 or done == len(bids):
                cost = time.time() - t0
                log("  %4d/%d  成功 %d  失败 %d  已用 %.0fs" % (
                    done, len(bids), len(items), fail, cost))

    if not items:
        log("一本都没抓到，检查网络或网页结构是否变了")
        return 1

    path, payload = save(items)
    log("\n共 %d 本（失败 %d），耗时 %.1f 秒\n  %s" % (
        len(items), fail, time.time() - t0, path))

    if "--no-upload" in flags:
        log("  已落盘（--no-upload，未上报）")
        return 0

    try:
        r = push.publish_file(path, base=base, token=token, endpoint="/api/details")
        log("  已入库：%s 个书架的当前值 + 快照，采集时间 %s" % (
            r.get("updated", 0), r.get("collected_at", "")))
    except Exception as e:                       # noqa: BLE001
        log("  上报失败：%s" % e)
        log("  后端没在跑？先起服务，再补推：")
        log("  python collector/push.py \"%s\" --push %s" % (path, base))
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
