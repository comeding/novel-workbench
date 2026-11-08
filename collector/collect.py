#!/usr/bin/env python
"""采集 QQ阅读 榜单，采集完自动上报后端。

用法（项目根目录）：
    python collector/collect.py                    采默认榜单（会员榜 + 新书榜）
    python collector/collect.py 会员榜              只采一个榜
    python collector/collect.py --check            只做环境自检，不采集
    python collector/collect.py --push http://IP:8765   上报到远端后端
    python collector/collect.py --no-upload        只落盘到 data/raw，不上报

采集依赖本机的模拟器 + root，大约 3~6 分钟，中途别操作模拟器。
"""
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))          # collector/
ROOT = os.path.dirname(BASE)
for p in (BASE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import env      # noqa: E402
import push     # noqa: E402


def run_collect(boards):
    """采一轮，返回落盘的文件路径；失败返回 None。"""
    from adb import Device
    from collectors.qqreader_app import AppRankCollector, save

    print("\n连接设备")
    print("-" * 58)
    dev = Device()
    print("  %s  屏幕 %dx%d  缩放 %.2fx" % (dev.serial, dev.screen[0], dev.screen[1], dev.sx))
    if not env.is_root(dev.adb, dev.serial):
        print("  没有 root，尝试 adb root ...")
        if not env.try_root(dev.adb, dev.serial):
            print("  [!!] root 失败：请在模拟器设置里打开 root 开关，重启模拟器后重试。")
            return None
        time.sleep(2)

    collector = AppRankCollector(dev)
    t0 = time.time()
    result = collector.collect(boards)
    if not result["boards"]:
        print("没有采集到任何榜单")
        return None

    total = sum(len(c.get("books") or []) for b in result["boards"].values()
                for c in (b.get("categories") or {}).values())
    print("\n共 %d 个榜单 / %d 个分类数据集 / %d 条记录，耗时 %.1f 分钟" % (
        len(result["boards"]),
        sum(len(b.get("categories") or {}) for b in result["boards"].values()),
        total, (time.time() - t0) / 60))
    return save(result)


def main():
    argv = sys.argv[1:]
    flags = {a for a in argv if a.startswith("-")}
    boards = [a for a in argv if not a.startswith("-")]

    push_url = token = None
    if "--push" in argv:
        i = argv.index("--push")
        push_url = argv[i + 1] if i + 1 < len(argv) else None
    if "--token" in argv:
        i = argv.index("--token")
        token = argv[i + 1] if i + 1 < len(argv) else None

    ok, _info = env.doctor()
    if "--check" in flags:
        return 0 if ok else 1

    if not ok:
        print("\n环境没就绪，先按上面提示补齐。只想看已有数据的话，直接启动后端：")
        print("  python backend/server.py")
        return 1

    cfg = env.load_config()
    boards = boards or cfg.get("boards") or ["会员榜", "新书榜"]

    path = run_collect(boards)
    if not path:
        return 1

    if "--no-upload" in flags:
        print("\n已跳过上报（--no-upload）。手动上报：")
        print("  python collector/push.py \"%s\"" % path)
        return 0

    base = push.resolve_base(push_url)
    print("\n上报后端 %s" % base)
    print("-" * 58)
    try:
        r = push.publish_file(path, base=base, token=token)
    except Exception as e:                       # noqa: BLE001
        print("  上报失败：%s" % e)
        print("  数据已落盘，等后端起来后手动补推：")
        print("  python collector/push.py \"%s\" --push %s" % (path, base))
        return 0          # 采集本身是成功的，别让调用方误判整轮失败

    if r.get("skipped"):
        print("  后端已有这一批（#%s），跳过" % r.get("run_id"))
    else:
        print("  已入库：批次 #%s，%d 条快照，%d 个分类" % (
            r.get("run_id"), r.get("inserted", 0), r.get("cats", 0)))

    # 榜单接口只有热度，没有「加入书架」人数；顺手把新书补一遍（只补缺的，一般几十秒）
    try:
        import detail
        print()
        detail.main(["--push", base] + (["--token", token] if token else []))
    except Exception as e:                       # noqa: BLE001
        print("  补加入书架数失败（不影响榜单）：%s" % e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
