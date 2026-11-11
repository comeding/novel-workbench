#!/usr/bin/env python
"""更新内核：查版本、比较远端、拉代码、重建容器、探活。

`dev/update.py`（命令行）和 `dev/agent.py`（页面上那个按钮背后的执行器）
都走这里，免得两处逻辑各写一遍，日后一边改了另一边没改。

只用标准库，只依赖外部的 git 和 docker。
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERRIDE = os.path.join(ROOT, "docker-compose.override.yml")
COMPOSE = os.path.join(ROOT, "docker-compose.yml")
DATA = os.path.join(ROOT, "data")
DB = os.path.join(DATA, "workbench.db")


def sh(args, cwd=ROOT, capture=True, timeout=None):
    """跑一条外部命令。找不到命令返回 127，不抛异常。"""
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=capture, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    if capture:
        out = (p.stdout or "").strip()
        # 失败时 git 把原因写在 stderr，stdout 是空的，别让它丢掉
        if p.returncode != 0 and not out:
            out = (p.stderr or "").strip()
        return p.returncode, out
    return p.returncode, ""


def is_repo(root=ROOT):
    return os.path.isdir(os.path.join(root, ".git"))


def port():
    return os.environ.get("WB_PORT") or "8765"


def probe_health(timeout=1.0, p=None):
    """问一下看板自己：起来了没。拿不到就是 None。"""
    url = "http://127.0.0.1:%s/api/health" % (p or port())
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)
    except Exception:                       # noqa: BLE001
        return None


def _fetch(root, timeout=45):
    """问一次远端。配了代理但代理抽风时，再直连试一次——
    这台机器上代理时好时坏是常态，两个都失败才叫真连不上。"""
    code, out = sh(["git", "fetch", "origin", "--prune"], cwd=root, timeout=timeout)
    if code == 0:
        return 0, out
    if code == 124:
        return 124, "%d 秒没连上 github.com（网络或代理断了）" % timeout
    _, proxy = sh(["git", "config", "--get", "http.proxy"], cwd=root)
    if proxy:
        code2, out2 = sh(["git", "-c", "http.proxy=", "-c", "https.proxy=",
                          "fetch", "origin", "--prune"], cwd=root, timeout=timeout)
        if code2 == 0:
            return 0, out2
        if code2 == 124:
            return 124, "换直连也没连上 github.com（%d 秒超时）" % timeout
        return code2, (out2 or out)
    return code, out


def status(root=ROOT, fetch=False):
    """当前版本 + 和远端的差距。fetch=True 时先问一次远端（慢一点，会走网络）。"""
    info = {
        "ok": False, "root": root, "repo": False, "branch": "", "commit": "", "short": "",
        "subject": "", "date": "", "dirty": False, "upstream": "", "behind": None,
        "ahead": None, "commits": [], "error": "", "checked_at": "",
    }
    if not is_repo(root):
        info["error"] = "这个目录不是 git 仓库"
        return info
    info["repo"] = True

    code, branch = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    if code != 0 or not branch:
        info["error"] = "读不出当前分支（仓库可能不完整）"
        return info
    info["branch"] = branch

    _, short = sh(["git", "rev-parse", "--short", "HEAD"], cwd=root)
    _, subject = sh(["git", "log", "-1", "--format=%s"], cwd=root)
    _, date = sh(["git", "log", "-1", "--format=%ci"], cwd=root)
    _, dirty = sh(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root)
    info.update({"commit": short, "short": short, "subject": subject,
                 "date": date[:16], "dirty": bool(dirty)})

    if fetch:
        code, out = _fetch(root)
        if code != 0:
            info["error"] = "连不上远端（网络或代理）：" + (out.splitlines()[-1] if out else "fetch 失败")
            info["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            return info

    info["upstream"] = "origin/" + branch
    code, up = sh(["git", "rev-parse", "--short", info["upstream"]], cwd=root)
    if code != 0:
        info["error"] = "远端没有分支 " + info["upstream"]
        info["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return info
    info["remote_short"] = up

    code, counts = sh(["git", "rev-list", "--left-right", "--count", "HEAD..." + info["upstream"]], cwd=root)
    if code == 0:
        try:
            ahead, behind = [int(x) for x in counts.split()]
            info["ahead"], info["behind"] = ahead, behind
        except ValueError:
            pass

    if info["behind"]:
        code, log = sh(["git", "log", "--format=%h%x1f%ci%x1f%s", "HEAD.." + info["upstream"],
                        "--max-count=40"], cwd=root)
        if code == 0:
            for line in log.splitlines():
                parts = line.split("\x1f")
                if len(parts) == 3:
                    info["commits"].append({"hash": parts[0], "time": parts[1][:16],
                                            "subject": parts[2]})

    info["ok"] = True
    info["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return info


def pull(root=ROOT, branch=None):
    """只接受快进。有本地改动或两边分叉时直接失败，让调用方去解释。"""
    branch = branch or status(root)["branch"] or "main"
    code, out = sh(["git", "pull", "--ff-only", "origin", branch], cwd=root)
    return code == 0, out


def compose_args(image_mode=False):
    """想跑「纯镜像」形态就显式指定基础文件，否则用默认（会自动叠加 override）。"""
    args = ["docker", "compose"]
    if image_mode or not os.path.exists(OVERRIDE):
        args += ["-f", os.path.basename(COMPOSE)]
    return args + ["up", "-d", "--build"]


def rebuild(image_mode=False, restart_cmd=None, log=print):
    """让新代码生效：容器就重建重启，裸机就跑调用方给的重启命令。"""
    if restart_cmd:
        log("重启服务：%s" % restart_cmd)
        code, out = sh(restart_cmd, cwd=ROOT, capture=False)
        return code == 0, out or ""

    if sh(["docker", "--version"])[0] != 0:
        return False, "没找到 docker：裸机部署请加 --restart-cmd，或手动重启服务"

    args = compose_args(image_mode)
    log("重建容器：%s" % " ".join(args))
    code, out = sh(args, cwd=ROOT, capture=False)
    if code != 0:
        return False, "docker compose 失败，看日志：docker compose logs --tail=50"
    return True, "容器已重建"


def wait_health(seconds=30, log=print):
    for _ in range(seconds):
        h = probe_health()
        if h:
            return h
        time.sleep(1)
    return None


def backup_hint():
    """库是 WAL 模式，不能直接 cp。给一条能在容器里跑的备份命令。"""
    return ("docker compose exec web python -c \"import sqlite3;"
            "s=sqlite3.connect('/data/workbench.db');d=sqlite3.connect('/data/backup.db');s.backup(d)\"")


def version_info():
    """给页面看的静态信息：本机 Python、平台、库大小。"""
    info = {"python": sys.version.split()[0], "platform": sys.platform,
            "root": ROOT, "db": DB, "db_mb": None}
    try:
        info["db_mb"] = round(os.path.getsize(DB) / 1048576.0, 1)
    except OSError:
        pass
    return info
