"""QQ阅读 App（移动端）榜单采集器 —— 走应用内缓存，而不是抓包。

原理
----
QQ阅读 的榜单页由 Flutter 渲染，请求走腾讯网关（androidtgw.reader.qq.com），
带 native 层签名，硬逆向成本极高。但 Flutter 的 HTTP 层会把响应写进
/data/data/com.qq.reader/databases/qqreader_flutter.db 的 http_cache 表。

所以采集流程变成：
  1. 删掉旧缓存（root 权限）
  2. 用 ADB 点一下榜单导航 / 分类标签（App 自己去请求，签名由它自己搞定）
  3. 从 http_cache 里把 200 条完整响应捞出来

注意：**每个分类都有独立的子榜**（columnInfo.name 形如
「包月男生畅销榜(日)-分类子榜-轻小说」，cid 也各不相同，各 200 条）。
它们不是总榜的子集，所以必须逐个分类真实请求一次，不能拿总榜做本地过滤。

优点：一次点击拿全 200 条，字段比界面显示的丰富得多（收藏数、上架时间、
标签、徽章都在），而且完全不用碰签名算法。

前提：模拟器已开 root（adb root 后 uid=0）。
"""
import csv
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from adb import Device, find, parse_nodes  # noqa: E402

DB = "/data/data/com.qq.reader/databases/qqreader_flutter.db"
OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "raw")

# 界面导航项 -> 缓存里 columnInfo.name 的特征词
BOARDS = {
    "会员榜": "包月男生畅销榜",
    "新书榜": "新书榜-男频",
}

APP = "com.qq.reader"
CAT_ROW_Y = 400      # 分类标签行的纵向范围（bound 判断用）


def log(msg):
    print(msg, flush=True)


class AppRankCollector:
    def __init__(self, dev=None):
        self.dev = dev or Device()

    # ---------- 设备侧 SQL ----------
    def sql(self, query, timeout=90):
        cmd = 'sqlite3 %s "%s"' % (DB, query.replace('"', '\\"'))
        return self.dev.shell(cmd, timeout=timeout)

    def clear_rank_cache(self):
        """删掉库里已有的榜单响应。

        只删库没用（App 内存里还有），但配合 restart_app 就有效：
        进程重启后内存没了、磁盘缓存也空了，进榜单页只能重新请求。
        """
        self.sql('delete from http_cache where response like \'%%"bookList"%%\';')
        log("  已清除旧榜单缓存")

    def max_id(self):
        out = self.sql("select ifnull(max(_id),0) from http_cache;").strip()
        try:
            return int(out.splitlines()[-1])
        except Exception:
            return 0

    def new_records(self, min_id):
        """返回 min_id 之后新增的榜单响应 [(id, columnName)]。"""
        out = self.sql(
            "select _id||'|'||ifnull(json_extract(response,'$.columnInfo.name'),'') "
            "from http_cache where _id>%d and response like '%%\"bookList\"%%' order by _id;" % min_id,
            timeout=60)
        recs = []
        for line in out.splitlines():
            line = line.strip()
            if "|" in line:
                i, n = line.split("|", 1)
                if i.isdigit():
                    recs.append((int(i), n))
        return recs

    def read_body(self, rid):
        raw = self.dev.shell('sqlite3 %s "select response from http_cache where _id=%d;"' % (DB, rid),
                             timeout=120)
        try:
            return json.loads(raw)
        except Exception:
            return None

    # ---------- 界面导航 ----------
    def foreground(self):
        """App 是不是当前前台窗口。采集前必须先确认这个，否则所有点击都是点桌面。"""
        out = self.dev.shell("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'")
        return (APP + "/") in out

    @staticmethod
    def _labels(xml, limit=14):
        """把界面上的文字串起来，报错时用来判断到底停在哪个页面。"""
        out = []
        for n in parse_nodes(xml):
            t = (n["desc"] or n["text"] or "").replace("\n", " / ").strip()
            if t:
                out.append(t[:22])
        return " | ".join(out[:limit]) or "（空界面）"

    @staticmethod
    def _on_rank_page(xml):
        return bool(find(xml, "会员榜", exact=True)) and bool(find(xml, "新书榜", exact=True))

    def _wait_for(self, pred, tries=10, interval=0.9):
        xml = ""
        for _ in range(tries):
            xml = self.dev.dump()
            if pred(xml):
                return xml
            time.sleep(interval)
        return xml

    def _skip_popups(self):
        """开屏广告 / 更新提示：先点跳过，点不到就按返回。"""
        for _ in range(3):
            xml = self.dev.dump()
            hit = None
            for kw in ("跳过广告", "跳过", "关闭"):
                # 「关闭」必须精确匹配，否则容易误点到别的按钮
                hit = find(xml, kw, exact=True) if kw == "关闭" else (
                    find(xml, kw, exact=True) or find(xml, kw))
                if hit:
                    break
            if not hit:
                return
            self.dev.tap(*hit)
            time.sleep(1.2)

    def launch_app(self, wait=25):
        """把 App 拉到前台，并等界面真正出来（不靠固定 sleep）。"""
        log("    启动 App …")
        self.dev.shell("monkey -p %s -c android.intent.category.LAUNCHER 1" % APP)
        t0 = time.time()
        ok = False
        while time.time() - t0 < wait:
            time.sleep(1.2)
            if not self.foreground():
                continue
            xml = self.dev.dump()
            if parse_nodes(xml):
                ok = True
                break
        time.sleep(1.5)
        self._skip_popups()
        return ok

    def ensure_rank_page(self):
        """确保停在排行榜页。没开 App 就先开，退到别处就退回来。"""
        xml = self.dev.dump()
        if self._on_rank_page(xml):
            return True

        # 1) 先保证 App 在前台（最常见的情况：模拟器停在桌面 / App 被杀掉了）
        if not self.foreground():
            log("  App 不在前台，先启动")
            self.launch_app()
        else:
            self._skip_popups()

        # 2) 退回到带底部导航（书架 / 书城 / 我的）的主层级
        for _ in range(5):
            xml = self.dev.dump()
            if self._on_rank_page(xml):
                return True
            if find(xml, "书架", exact=True) and find(xml, "我的", exact=True):
                break
            if not self.foreground():
                self.launch_app()
                continue
            self.dev.back()
            time.sleep(0.9)

        # 3) 切到「书城」（排行榜入口在书城首页顶部）
        xml = self.dev.dump()
        if not find(xml, "排行榜"):
            pos = find(xml, "书城", exact=True)
            if pos:
                self.dev.tap(*pos)
                time.sleep(2.5)
            xml = self.dev.dump()

        # 4) 找「排行榜」入口：先就地找，再滚回顶部找，最后逐屏往下翻
        pos = find(xml, "排行榜") or find(xml, "榜单")
        if not pos:
            for _ in range(3):
                self.dev.swipe(540, 400, 540, 1700, 250)
            pos = find(self.dev.dump(), "排行榜")
        for _ in range(4):
            if pos:
                break
            self.dev.swipe(540, 1400, 540, 600, 350)
            time.sleep(0.6)
            pos = find(self.dev.dump(), "排行榜")
        if not pos:
            raise RuntimeError("找不到排行榜入口；当前界面：" + self._labels(self.dev.dump()))
        self.dev.tap(*pos)

        # 5) 等榜单页出来（可能夹一层引导/弹窗）
        xml = self._wait_for(self._on_rank_page, tries=12)
        if not self._on_rank_page(xml):
            self._skip_popups()
            xml = self._wait_for(self._on_rank_page, tries=6)
        if not self._on_rank_page(xml):
            raise RuntimeError("点了排行榜但没进榜单页；当前界面：" + self._labels(xml))
        return True

    def current_board(self):
        """App 现在停在哪个榜 —— 取缓存里最新一条榜单响应的 column 名。

        比看界面文字可靠：分类标签两个榜长得一样，只有 column 名带得出「包月男生畅销榜」
        还是「新书榜-男频」。
        """
        out = self.sql("select ifnull(json_extract(response,'$.columnInfo.name'),'') "
                       "from http_cache where response like '%\"bookList\"%' "
                       "order by _id desc limit 1;", timeout=30)
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        return lines[-1] if lines else ""

    def click_board(self, name, before=None, wait=18):
        """切到某个榜，并确认真的切过去了。

        只看「点了没报错」不够：冷启动时那一下可能被开屏弹窗吃掉，界面上还是上一个榜，
        分类照样能读出来（只是别人的分类），响应按榜名过滤后全对不上——
        表现就是「N 个分类全没抓到」。所以点完要盯着 column 名变过去。
        """
        key = BOARDS.get(name, "")
        for attempt in range(1, 4):
            if key and key in self.current_board():
                log("  当前已在「%s」" % name)
                return True
            xml = self.dev.dump()
            pos = find(xml, name, exact=True)
            if not pos:
                raise RuntimeError("找不到「%s」的入口；当前界面：%s" % (name, self._labels(xml)))
            self.dev.tap(*pos)
            if not key:
                time.sleep(2.5)
                return True
            t0 = time.time()
            while time.time() - t0 < wait:
                if key in self.current_board():
                    return True
                time.sleep(1.2)
            log("  点了「%s」但没切过去，重试 %d/3" % (name, attempt))
            time.sleep(1.5)
        raise RuntimeError("切不到「%s」；当前界面：%s" % (name, self._labels(self.dev.dump())))

    def _tab_row_y(self, default=None):
        """分类标签条所在的 y（取界面上实际节点，页面改版也不会失效）。"""
        for n in parse_nodes(self.dev.dump()):
            if "个标签" in (n["desc"] or ""):
                return n["center"][1]
        return default

    def _swipe_y(self):
        """横滑切分类的落点：必须在标签条**下方的书目区**。
        直接在标签条上滑，App 只会把标签条轻微滚动，读不全 14 个分类。"""
        tab = self._tab_row_y()
        return (tab + 135) if tab else 318

    def _cat_nodes(self):
        """当前可见的分类标签 [(名, center)]，按界面顺序。"""
        out = []
        for n in parse_nodes(self.dev.dump()):
            d = n["desc"] or ""
            if "个标签" not in d:
                continue
            x1, y1, x2, y2 = n["bounds"]
            if y1 > CAT_ROW_Y:
                continue
            out.append((d.split("\n")[0].strip(), n["center"]))
        return out

    def read_categories(self):
        """横滑读出 App 榜单页的全部分类（含"全部"）。"""
        y = self._swipe_y()
        for _ in range(4):          # 先滑到最左
            self.dev.swipe(300, y, 980, y, 200)
            time.sleep(0.25)
        found = {}
        total = 0
        for _ in range(8):
            for n in parse_nodes(self.dev.dump()):
                d = n["desc"] or ""
                if "个标签" not in d:
                    continue
                head, _, tail = d.partition("第 ")
                if not tail:
                    continue
                try:
                    idx = int(tail.split(" ")[0])
                    cnt = int(tail.split("共 ")[1].split(" ")[0])
                except Exception:
                    continue
                found[idx] = head.strip()
                total = max(total, cnt)
            if total and len(found) >= total:
                break
            self.dev.swipe(950, y, 300, y, 300)
            time.sleep(0.8)
        for _ in range(4):
            self.dev.swipe(300, y, 980, y, 200)
            time.sleep(0.25)
        return [found[i] for i in sorted(found)]

    def click_category(self, cat):
        """把分类标签滑到可见并点击。"""
        y = self._swipe_y()
        for _ in range(4):
            self.dev.swipe(300, y, 980, y, 200)
            time.sleep(0.2)
        for _ in range(8):
            for name, center in self._cat_nodes():
                if name == cat:
                    self.dev.tap(*center)
                    time.sleep(1.2)
                    return True
            self.dev.swipe(950, y, 300, y, 300)
            time.sleep(0.8)
        return False

    @staticmethod
    def _cat_of(column_name):
        """columnInfo.name -> 分类名。总榜的 name 里没有「分类子榜-」。"""
        if not column_name:
            return None
        if "分类子榜-" in column_name:
            return column_name.split("分类子榜-")[-1].strip()
        return "全部"

    def drain(self, min_id, board_name, data_map, cats=(), wait=8):
        """把 min_id 之后到达的响应按分类收进 data_map（同一分类只留第一份）。

        App 切标签时会顺手把邻近分类也请求掉，所以一次点击往往能带回好几个分类，
        没必要一个分类死等。
        """
        key = BOARDS.get(board_name, "")
        deadline = time.time() + wait
        while True:
            for rid, col in self.new_records(min_id):
                if key and key not in col:
                    continue                      # 别的榜单的响应
                cat = self._cat_of(col)
                if not cat or cat in data_map:
                    continue
                if cats and cat not in cats:
                    continue
                data = self.read_body(rid)
                if not data or not data.get("bookList"):
                    continue
                column = data.get("columnInfo") or {}
                data_map[cat] = {"column": column, "total": data.get("total"),
                                 "books": self.parse_books(data)}
                log("  %-6s cid=%-7s %3d 条  %s" % (
                    cat, column.get("cid"), len(data_map[cat]["books"]), col))
            if cats and len(data_map) >= len(cats):
                return
            if time.time() >= deadline:
                return
            time.sleep(1.0)

    def restart_app(self):
        """杀掉重开 —— App 在内存里缓存了榜单数据，不重启就不会重新请求。"""
        self.dev.shell("am force-stop %s" % APP)
        time.sleep(2)
        self.launch_app()

    # ---------- 解析 ----------
    @staticmethod
    def parse_books(data):
        books = data.get("bookList") or []
        rows = []
        for i, b in enumerate(books, 1):
            badges = [sw["desc"] for sw in (b.get("sellWords") or [])
                      if isinstance(sw, dict) and sw.get("desc")]
            try:
                total_words = int(b.get("totalWords") or 0)
            except (TypeError, ValueError):
                total_words = 0
            # 列表只显示万位，但精确值必须原样带下去（详情页要用）
            words = round(total_words / 10000, 1) if total_words else 0
            upd = b.get("updateTime")
            rows.append({
                "rank": i,
                "bid": b.get("bid"),
                "title": b.get("title"),
                "author": b.get("author"),
                "cat2": b.get("catel2name") or "",
                "cat3": b.get("catel3name") or "",
                "label": b.get("leftBottomLabel") or "",
                "words_wan": words,
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

    # ---------- 主流程 ----------
    def collect_board(self, name):
        """采一个榜单：总榜 + 每个分类子榜。

        关键点：App 把榜单数据缓存在**内存**里，光清 cache 库它不会重新请求。
        所以每个榜单都从一个干净会话开始（force-stop 后重开），让它在首次访问
        各分类时真的发一遍请求，然后统一从 cache 库里把这一批响应捞出来。
        """
        # 清磁盘缓存 + 重启进程：两个都做，App 才会真的重新走一遍网络
        self.clear_rank_cache()
        self.restart_app()
        # 水位要在进排行榜**之前**取：App 一进榜单页就会请求总榜，
        # 取晚了这类响应会被当成「旧数据」漏掉。
        before = self.max_id()
        self.ensure_rank_page()
        self.click_board(name, before)

        cats = self.read_categories()
        log("  分类 %d 个：%s" % (len(cats), " ".join(cats)))
        if not cats:
            cats = ["全部"]

        data_map = {}

        # 读分类时的横滑通常已经把邻近分类都请求过一遍了，先把这批收下
        self.drain(before, name, data_map, cats, wait=10)

        # 剩下的逐个点，点完就把新到的响应全收一遍（一次可能带回好几个分类）
        for cat in cats:
            if cat in data_map:
                continue
            if self.click_category(cat):
                self.drain(before, name, data_map, cats, wait=10)
            if cat not in data_map:
                log("  ✗ %s 没抓到响应" % cat)

        # 还缺的话，再来一个干净会话补一轮
        missing = [c for c in cats if c not in data_map]
        if missing:
            log("  补采：%s（重开 App）" % " ".join(missing))
            self.clear_rank_cache()
            self.restart_app()
            before2 = self.max_id()
            self.ensure_rank_page()
            try:
                self.click_board(name, before2)
            except RuntimeError as e:
                log("  [!] %s" % e)
            for cat in missing:
                if self.click_category(cat):
                    self.drain(before2, name, data_map, cats, wait=10)
            still = [c for c in cats if c not in data_map]
            if still:
                log("  ✗ 仍缺：%s" % " ".join(still))

        if not data_map:
            log("  ✗ 整个榜单都没采到")
            return None
        base = data_map.get("全部") or list(data_map.values())[0]
        return {
            "column": base["column"],
            "total": base["total"],
            "books": base["books"],
            "categories": data_map,
        }

    def collect(self, board_names):
        self.ensure_rank_page()
        result = {
            "platform": "QQ阅读 App",
            "source": "qqreader_flutter.db / http_cache",
            "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "boards": {},
        }
        for name in board_names:
            log("[%s]" % name)
            info = self.collect_board(name)
            if info:
                result["boards"][name] = info
        return result


def save(result):
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M")
    jpath = os.path.join(OUT_DIR, "qqreader_app_%s.json" % stamp)
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    cpath = os.path.join(OUT_DIR, "qqreader_app_%s.csv" % stamp)
    try:
        with open(cpath, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["榜单", "分类", "排名", "书名", "作者", "分类2", "子分类", "标签",
                        "字数(万)", "收藏数", "徽章", "最新章节", "更新日期", "cid", "bid", "简介"])
            for board, info in result["boards"].items():
                for cat, ci in (info.get("categories") or {}).items():
                    for b in ci["books"]:
                        w.writerow([board, cat, b["rank"], b["title"], b["author"], b["cat2"],
                                    b["cat3"], b["label"], b["words_wan"], b["collect_num"],
                                    b["badge"], b["last_chapter"], b["update_date"],
                                    (ci.get("column") or {}).get("cid", ""), b["bid"], b["intro"]])
    except OSError as e:
        log("⚠ CSV 写入失败（%s），改用 utf-8 重试" % e)
        with open(cpath, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            for board, info in result["boards"].items():
                for cat, ci in (info.get("categories") or {}).items():
                    for b in ci["books"]:
                        w.writerow([board, cat, b["rank"], b["title"], b["author"], b["cat2"],
                                    b["cat3"], b["label"], b["words_wan"], b["collect_num"],
                                    b["badge"], b["last_chapter"], b["update_date"],
                                    (ci.get("column") or {}).get("cid", ""), b["bid"], b["intro"]])
    log("\n已保存：\n  %s\n  %s" % (jpath, cpath))
    return jpath


def main():
    names = [a for a in sys.argv[1:] if not a.startswith("-")] or ["会员榜", "新书榜"]
    dev = Device()
    log("设备：%s" % dev.serial)
    if "uid=0" not in dev.shell("id").strip():
        log("⚠ 当前不是 root，先执行 adb root")
        subprocess.run([dev.adb, "-s", dev.serial, "root"], capture_output=True)
        time.sleep(2)
    collector = AppRankCollector(dev)
    t0 = time.time()
    try:
        result = collector.collect(names)
    except RuntimeError as e:
        log("\n[!!] %s" % e)
        log("     模拟器要停在 QQ阅读 里、并且能手动点进排行榜，再重试。")
        raise SystemExit(1)
    if result["boards"]:
        total = sum(len(c["books"]) for b in result["boards"].values()
                    for c in (b.get("categories") or {}).values())
        log("\n共 %d 个榜单 / %d 个分类数据集 / %d 条记录，耗时 %.1f 分钟" % (
            len(result["boards"]),
            sum(len(b.get("categories") or {}) for b in result["boards"].values()),
            total, (time.time() - t0) / 60))
        save(result)
    else:
        log("没有采集到任何榜单")


if __name__ == "__main__":
    main()
