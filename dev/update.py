#!/usr/bin/env python
"""一键更新：从 GitHub 拉最新代码，再让容器（或本机服务）用上它。

为什么单独写一个脚本：拉完代码「怎么让新代码生效」取决于部署形态——
挂载模式重启就行，纯镜像模式必须重新 build，裸机要重跑服务。
这里判断一次，省得每次翻文档。

用法：
    python dev/update.py              # 拉代码 + 重建并重启容器
    python dev/update.py --check      # 只看有没有新版本，什么都不动
    python dev/update.py --no-docker  # 只拉代码，不碰容器（裸 Python / systemd）
    python dev/update.py --image      # 纯镜像形态：docker compose -f docker-compose.yml
    python dev/update.py --rebuild    # 已经是最新，也强制重建一次

Windows 上直接双击「④ 更新代码.bat」等价于第一条。
只依赖 git 和（可选的）docker，不需要装任何 Python 包。
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERRIDE = os.path.join(ROOT, "docker-compose.override.yml")
COMPOSE = os.path.join(ROOT, "docker-compose.yml")
DB = os.path.join(ROOT, "data", "workbench.db")

argv = sys.argv[1:]
CHECK_ONLY = "--check" in argv
NO_DOCKER = "--no-docker" in argv
IMAGE_MODE = "--image" in argv
FORCE_REBUILD = "--rebuild" in argv


def say(msg=""):
    print(msg, flush=True)


def run(args, capture=True):
    """返回 (退出码, 输出)。找不到命令时给 127，不抛异常。"""
    try:
        p = subprocess.run(args, cwd=ROOT, capture_output=capture, text=True)
    except FileNotFoundError:
        return 127, ""
    if capture:
        return p.returncode, (p.stdout or "").strip()
    return p.returncode, ""


def git(*args):
    return run(["git"] + list(args))


def die(msg, code=1):
    say("")
    say("  " + msg)
    sys.exit(code)


def port():
    return os.environ.get("WB_PORT") or "8765"


def health(timeout=1.0):
    url = "http://127.0.0.1:%s/api/health" % port()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)
    except Exception:                       # noqa: BLE001
        return None


def main():
    say("网文工作站 · 更新")
    say("")

    if not shutil.which("git"):
        die("没找到 git。装一个 Git for Windows，或者直接下载 zip 解压覆盖。")
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        die("这个目录不是 git 仓库（大概是下载 zip 解压来的）。\n"
            "  想以后能一键更新：git clone 一份，把 data/ 整个拷过去。")

    code, branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if code != 0 or not branch:
        die("读不出当前分支，仓库可能坏了：git status 看看。")
    if branch == "HEAD":
        die("当前处于游离 HEAD 状态，先切回主分支：git checkout main")

    say("  [1/4] 问一下 GitHub")
    code, _ = git("fetch", "origin", "--prune")
    if code != 0:
        die("拉不动远程。多半是网络（国内直连 GitHub 时好时坏），\n"
            "  过一会儿重试；或者临时挂代理再跑一次。")

    upstream = "origin/" + branch
    _, old = git("rev-parse", "--short", "HEAD")
    c, new = git("rev-parse", "--short", upstream)
    if c != 0:
        die("远程没有分支 %s，确认一下仓库地址：git remote -v" % upstream)

    _, counts = git("rev-list", "--left-right", "--count", "HEAD..." + upstream)
    try:
        ahead, behind = [int(x) for x in counts.split()]
    except ValueError:
        ahead = behind = 0

    say("  本地 %s    远程 %s    领先 %d / 落后 %d" % (old, new, ahead, behind))
    if ahead:
        say("  本地有 %d 个提交是远程没有的（自己改过东西）。" % ahead)

    if behind == 0:
        say("")
        say("  已经是最新版，没有要拉的。")
        if not FORCE_REBUILD:
            say("  想强制按当前代码重建容器：加 --rebuild")
            return 0
        say("  --rebuild：还是重建一次。")
    else:
        _, log = git("log", "--oneline", "--no-decorate", "HEAD.." + upstream)
        say("")
        say("  新增 %d 个提交：" % behind)
        for line in log.splitlines():
            say("    " + line)
        say("")

        _, dirty = git("status", "--porcelain", "--untracked-files=no")
        if dirty:
            say("  注意：工作区有未提交的改动，和这些提交撞上就会拉不动。")
            say("        想保留：git stash  →  拉完 git stash pop")
            say("        不要了：git checkout -- .")
            say("")

    if CHECK_ONLY:
        say("  --check：到此为止，没有动任何东西。")
        return 0

    if os.path.exists(DB):
        size = os.path.getsize(DB) / 1048576.0
        say("  库 %.1f MB，在 data/workbench.db。要备份的话见 DEPLOY.md「备份与迁移」" % size)

    say("")
    say("  [2/4] git pull --ff-only")
    code, out = git("pull", "--ff-only", "origin", branch)
    if code != 0:
        say(out or "")
        die("拉取失败：\n"
            "  · 本地改过代码 → git stash 后再跑一次，改完 git stash pop\n"
            "  · 两边都改了同一个文件 → 手动合并，或 git checkout -- <文件> 放弃本地改动\n"
            "  · 想彻底对齐远程 → git reset --hard %s" % upstream, 2)
    say("  拉到 %s" % new)

    say("")
    if NO_DOCKER:
        say("  [3/4] 跳过容器（--no-docker）")
        say("  [4/4] 裸机 / systemd 部署，重启一下服务即可：")
        say("        sudo systemctl restart novel-workbench")
        return 0

    if not shutil.which("docker"):
        say("  [3/4] 没找到 docker，跳过。代码已经拉好了，")
        say("        用系统服务跑的就去 restart；容器在别的机器上就把代码同步过去。")
        return 0

    use_override = os.path.exists(OVERRIDE) and not IMAGE_MODE
    cmd = ["docker", "compose"]
    if not use_override:
        cmd += ["-f", os.path.basename(COMPOSE)]
    cmd += ["up", "-d", "--build"]

    say("  [3/4] 重建并重启容器（%s）" % ("挂载模式" if use_override else "纯镜像模式"))
    say("        " + " ".join(cmd))
    say("")
    code, _ = run(cmd, capture=False)
    if code != 0:
        die("compose 失败了，看日志：docker compose logs --tail=50", 3)

    say("")
    say("  [4/4] 等它起来")
    ok = None
    for _ in range(20):
        ok = health()
        if ok:
            break
        import time
        time.sleep(1)
    if ok:
        say("  就绪：http://127.0.0.1:%s  （Python %s，库 %s）"
            % (port(), ok.get("python", "?"), ok.get("db", "?")))
    else:
        say("  20 秒内没等到 /api/health。看日志：docker compose logs --tail=50")

    say("")
    say("  升级完成。数据在 data/ 里，没动过。")
    say("  不满意可以回退：git log --oneline -5 找到旧提交，")
    say("    git checkout <旧提交> -- .  然后 python dev/update.py --rebuild")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                       # noqa: BLE001
        pass
    sys.exit(main())
