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
    python dev/update.py --restart-cmd "sudo systemctl restart novel-workbench"

Windows 上直接双击「④ 更新代码.bat」等价于第一条。
页面上的「版本控制 → 立即更新」走的是同一套逻辑（dev/agent.py）。
"""

import os
import shutil
import sys

import updater

argv = sys.argv[1:]
CHECK_ONLY = "--check" in argv
NO_DOCKER = "--no-docker" in argv
IMAGE_MODE = "--image" in argv
FORCE_REBUILD = "--rebuild" in argv


def say(msg=""):
    print(msg, flush=True)


def opt(name):
    """取 --name 后面的值，没有就返回 None。"""
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def die(msg, code=1):
    say("")
    say("  " + msg)
    sys.exit(code)


def main():
    say("网文工作站 · 更新")
    say("")

    if not shutil.which("git"):
        die("没找到 git。装一个 Git for Windows，或者直接下载 zip 解压覆盖。")
    if not updater.is_repo():
        die("这个目录不是 git 仓库（大概是下载 zip 解压来的）。\n"
            "  想以后能一键更新：git clone 一份，把 data/ 整个拷过去。")

    say("  [1/4] 问一下 GitHub")
    st = updater.status(fetch=True)
    if not st["ok"] and not st["behind"] and st["error"]:
        die(st["error"])

    old, new = st["short"], st.get("remote_short", "?")
    ahead, behind = st["ahead"] or 0, st["behind"] or 0
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
        say("")
        say("  新增 %d 个提交：" % behind)
        for c in st["commits"]:
            say("    %s  %s" % (c["hash"], c["subject"]))
        say("")
        if st["dirty"]:
            say("  注意：工作区有未提交的改动，和这些提交撞上就会拉不动。")
            say("        想保留：git stash  →  拉完 git stash pop")
            say("        不要了：git checkout -- .")
            say("")

    if CHECK_ONLY:
        say("  --check：到此为止，没有动任何东西。")
        return 0

    if os.path.exists(updater.DB):
        say("  库 %.1f MB，在 data/workbench.db。要备份的话见 DEPLOY.md「备份与迁移」"
            % (os.path.getsize(updater.DB) / 1048576.0))

    say("")
    say("  [2/4] git pull --ff-only")
    ok, out = updater.pull(branch=st["branch"])
    if not ok:
        say(out or "")
        die("拉取失败：\n"
            "  · 本地改过代码 → git stash 后再跑一次，改完 git stash pop\n"
            "  · 两边都改了同一个文件 → 手动合并，或 git checkout -- <文件> 放弃本地改动\n"
            "  · 想彻底对齐远程 → git reset --hard %s" % st["upstream"], 2)
    say("  拉到 %s" % new)

    say("")
    if NO_DOCKER:
        say("  [3/4] 跳过容器（--no-docker）")
        say("  [4/4] 裸机 / systemd 部署，重启一下服务即可：")
        say("        sudo systemctl restart novel-workbench")
        return 0

    say("  [3/4] 重建并重启容器（%s）"
        % ("挂载模式" if not IMAGE_MODE and os.path.exists(updater.OVERRIDE) else "纯镜像模式"))
    ok, msg = updater.rebuild(IMAGE_MODE, restart_cmd=opt("--restart-cmd"), log=say)
    if not ok:
        die(msg, 3)

    say("")
    say("  [4/4] 等它起来")
    h = updater.wait_health(20)
    if h:
        say("  就绪：http://127.0.0.1:%s  （Python %s，库 %s）"
            % (updater.port(), h.get("python", "?"), h.get("db", "?")))
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
