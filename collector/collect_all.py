# -*- coding: utf-8 -*-
"""采集总入口：QQ阅读 + 番茄小说，一条命令全采。

页面上的「立即采集」和后端的定时补采都跑这个脚本。各平台的采集逻辑仍在
自己的文件里（api_rank.py / fanqie.py），这里只做分派和串行：

    python collector/collect_all.py                  # 两个平台都采
    python collector/collect_all.py 番茄新书榜        # 只采番茄
    python collector/collect_all.py 会员榜 番茄新书榜  # 指定多个
    python collector/collect_all.py --no-upload      # 只落盘

每个平台采完自己 POST 回 /api/ingest，所以这里不需要再拼一次上报。
"""
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)


def log(msg):
    print(msg, flush=True)


def _split(argv):
    """把 --push/--token 的值单独拎出来，剩下的非选项才是榜名。"""
    names, rest = [], []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--push", "--token") and i + 1 < len(argv):
            rest += [a, argv[i + 1]]
            i += 2
        elif a.startswith("-"):
            rest.append(a)
            i += 1
        else:
            names.append(a)
            i += 1
    return names, rest


def run(script, argv):
    cmd = [sys.executable, "-u", os.path.join(BASE, script)] + list(argv)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.pop("PYTHONHOME", None)
    proc = subprocess.Popen(
        cmd, cwd=BASE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    for line in proc.stdout:
        print(line.rstrip("\r\n"), flush=True)
    proc.wait()
    return proc.returncode


def main(argv):
    import api_rank
    import fanqie

    qq_known = set(api_rank.load_cids().keys())
    fq_known = set(fanqie.BOARDS.keys())
    names, rest = _split(argv)

    unknown = [n for n in names if n not in qq_known and n not in fq_known]
    if unknown:
        log("不认识的榜名：%s" % "、".join(unknown))
        log("QQ阅读：%s" % "、".join(qq_known))
        log("番茄小说：%s" % "、".join(fq_known))
        return 1

    qq_names = [n for n in names if n in qq_known]
    fq_names = [n for n in names if n in fq_known]
    if not names:
        qq_names, fq_names = list(qq_known), list(fq_known)

    codes = []
    if qq_names:
        codes.append(run("api_rank.py", qq_names + rest))
    if fq_names:
        codes.append(run("fanqie.py", fq_names + rest))

    # 3 = 采到了但没入库（口令不对/后端没起），比 1 更具体，优先把它报上去
    if 3 in codes:
        return 3
    return 0 if all(c == 0 for c in codes) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
