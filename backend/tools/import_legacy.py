#!/usr/bin/env python
"""把采集结果导入数据库。

库里只留最新一版，所以这个脚本的语义是「用最新的一份 json 覆盖全库」。

一次采集往往分几次跑（会员榜、新书榜各一份 json），所以这里按「榜单」而不是
按「文件」取最新：每个榜各自认它最后一次出现的那份数据，合并成一份再入库。

用法（项目根目录）：
    python backend/tools/import_legacy.py            扫 data/raw 下所有 json
    python backend/tools/import_legacy.py 文件...     只用指定的文件
"""
import glob
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # backend/
ROOT = os.path.dirname(BASE)
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from app import db                                          # noqa: E402
from app.services.ingest import ingest                      # noqa: E402

SOURCES = [
    os.path.join(ROOT, "data", "raw", "*.json"),
    os.path.join(ROOT, "_legacy", "**", "*.json"),
]


def collect_files(argv):
    if argv:
        return list(argv)
    files = []
    for pattern in SOURCES:
        files.extend(glob.glob(pattern, recursive=True))
    return files


def load(files):
    """读文件，返回按 collected_at 升序的 [(时间, 文件名, payload)]。"""
    out = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        boards = data.get("boards")
        # 老 web 版采集器产出的是 {榜单: [书...]}，没有 column/categories，跳过
        if not isinstance(boards, dict) or not any(isinstance(v, dict) for v in boards.values()):
            print("  跳过 %s（格式不兼容）" % os.path.basename(f))
            continue
        out.append((data.get("collected_at") or "", os.path.basename(f), data))
    out.sort(key=lambda x: x[0])
    return out


def main(argv):
    db.init()
    loaded = load(collect_files(argv))
    if not loaded:
        print("没有可用的采集文件")
        return 1

    # 按榜合并：时间靠后的覆盖靠前的
    merged, origin = {}, {}
    for at, name, data in loaded:
        for bname, binfo in (data.get("boards") or {}).items():
            if not isinstance(binfo, dict):
                continue
            merged[bname] = binfo
            origin[bname] = (at, name)

    if not merged:
        print("所有文件都没有可导入的榜单")
        return 1

    latest = max(at for at, _ in origin.values())
    payload = {
        "platform": loaded[-1][2].get("platform") or "QQ阅读 App",
        "source": "import_legacy",
        "collected_at": latest,
        "boards": merged,
    }
    try:
        r = ingest(payload)
    except ValueError as e:
        print("导入失败：%s" % e)
        return 1

    print("导入完成：%d 条在榜 / %d 个分类" % (r["inserted"], r["cats"]))
    print("各榜数据来源：")
    for bname, (at, name) in sorted(origin.items()):
        print("  %-8s %s  %s" % (bname, at, name))

    st = db.stats()
    print("\n库内现状：%d 条在榜 / %d 本书 / %d 个分类 / %.1f MB"
          % (st["entries"], st["books"], st["cats"], st["size"] / 1048576))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
