"""容器内自更新：git pull + 热重载。

以前「更新」这件事只能交给宿主机上的 dev/agent.py：容器里既没装 git，
也没有 docker 权限。代价是用户得先在宿主机双击一个 bat 把那个进程叫起来，
否则版本控制页上的按钮是灰的——对一个「clone 完就能用」的项目来说，
这道门槛很不合理。

现在镜像里装了 git，项目目录又整个挂进容器（override 里 `.:/app`），
更新的活可以在容器里自己干完：

    git pull --ff-only  →  文件经挂载落到宿主机  →  supervisor 察觉变化重启

不需要 docker 权限，也不需要额外进程。注意这里**不做 rebuild**：
挂载模式下代码本来就是宿主机目录，重启就生效；真要重建镜像，
那是「改过 Dockerfile」的场景，属于少数，走命令行更合适。

纯镜像形态（`-f docker-compose.yml`）容器里没有 .git，available() 会
返回不可用，由 routes/version.py 退回转发给宿主执行器——老部署不受影响。
"""

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import threading
import time

from .config import Config

# /app/backend/app/updater_local.py → /app
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.dirname(Config.DB_PATH)
JOB_FILE = os.path.join(DATA, "update.job.json")
CFG_FILE = os.path.join(DATA, "update.cfg.json")

LOCK = threading.Lock()
JOB = {"running": False, "action": "", "started_at": "", "elapsed": 0,
       "log": [], "result": "", "error": ""}
STATUS = {}
AUTO = {"enabled": False, "interval_min": 60, "last_check": "", "last_run": ""}
STARTED = time.strftime("%Y-%m-%d %H:%M:%S")
MODE = "container"
BUSY = "已经有一个任务在跑"

_UNSET = object()


# ---------------------------------------------------------------- git 调用

def _env_proxy():
    """容器里 127.0.0.1 指的是容器自己，宿主上配的代理要换成网关地址。

    取值顺序：WB_GIT_PROXY > 项目 .git/config 里的 http.proxy。
    都没配就直连——国内直连 GitHub 时快时慢，但至少不会指向一个死端口。
    """
    v = (os.environ.get("WB_GIT_PROXY") or "").strip()
    if v:
        return v
    code, out = _raw(["git", "-c", "safe.directory=" + ROOT, "config", "--get", "http.proxy"])
    if code == 0 and out:
        return out.strip()
    return ""


def _raw(args, cwd=ROOT, timeout=60):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    out = (p.stdout or "").strip()
    if p.returncode != 0 and not out:
        out = (p.stderr or "").strip()
    return p.returncode, out


def _git(args, timeout=60, proxy=_UNSET):
    """git 命令。带 safe.directory：挂载进来的目录属主和容器内用户对不上，
    不加会直接被 "detected dubious ownership" 拦下来。"""
    cmd = ["git", "-c", "safe.directory=" + ROOT]
    p = _env_proxy() if proxy is _UNSET else proxy
    if p:
        cmd += ["-c", "http.proxy=" + p, "-c", "https.proxy=" + p]
    return _raw(cmd + args, timeout=timeout)


def _fetch(timeout=45):
    """问一次远端。代理不通就换直连再来一次，两个都失败才算真连不上。"""
    code, out = _git(["fetch", "origin", "--prune"], timeout=timeout)
    if code == 0:
        return 0, out
    if code == 124:
        return 124, "%d 秒没连上 github.com（容器出网受限？）" % timeout
    if _env_proxy():
        code2, out2 = _git(["fetch", "origin", "--prune"], timeout=timeout, proxy="")
        if code2 == 0:
            return 0, out2
        return code2, (out2 or out)
    return code, out


# ---------------------------------------------------------------- 状态

def available():
    """容器能不能自己更新。返回 (能不能, 不能的原因)。"""
    if os.path.isfile("/.dockerenv") and not os.path.isdir(os.path.join(ROOT, ".git")):
        return False, ("容器里没有 .git（纯镜像部署）。这种形态请用命令行更新："
                       "python dev/update.py")
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        return False, "这个目录不是 git 仓库，没法拉代码"
    if not shutil.which("git"):
        return False, "镜像里没装 git"
    return True, ""


def status(fetch=False):
    """当前版本 + 与远端的差距，字段与 dev/updater.py 保持一致。"""
    info = {
        "ok": False, "root": ROOT, "repo": False, "branch": "", "commit": "",
        "short": "", "remote_short": "", "subject": "", "date": "", "dirty": False,
        "upstream": "", "behind": None, "ahead": None, "commits": [],
        "error": "", "checked_at": "",
    }
    ok, why = available()
    if not ok:
        info["error"] = why
        return info
    info["repo"] = True

    code, branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    if code != 0 or not branch:
        info["error"] = "读不出当前分支（仓库可能不完整）"
        return info
    info["branch"] = branch

    _, short = _git(["rev-parse", "--short", "HEAD"])
    _, subject = _git(["log", "-1", "--format=%s"])
    _, date = _git(["log", "-1", "--format=%ci"])
    _, dirty = _git(["status", "--porcelain", "--untracked-files=no"])
    info.update({"commit": short, "short": short, "subject": subject,
                 "date": date[:16], "dirty": bool(dirty)})

    if fetch:
        code, out = _fetch()
        if code != 0:
            info["error"] = "连不上远端：" + (out.splitlines()[-1] if out else "fetch 失败")
            info["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            return info

    info["upstream"] = "origin/" + branch
    code, up = _git(["rev-parse", "--short", info["upstream"]])
    if code != 0:
        info["error"] = "远端没有分支 " + info["upstream"]
        info["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return info
    info["remote_short"] = up

    code, counts = _git(["rev-list", "--left-right", "--count",
                         "HEAD..." + info["upstream"]])
    if code == 0:
        try:
            ahead, behind = [int(x) for x in counts.split()]
            info["ahead"], info["behind"] = ahead, behind
        except ValueError:
            pass

    if info["behind"]:
        code, log = _git(["log", "--format=%h%x1f%ci%x1f%s",
                          "HEAD.." + info["upstream"], "--max-count=40"])
        if code == 0:
            for line in log.splitlines():
                parts = line.split("\x1f")
                if len(parts) == 3:
                    info["commits"].append({"hash": parts[0], "time": parts[1][:16],
                                            "subject": parts[2]})

    info["ok"] = True
    info["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return info


# ---------------------------------------------------------------- 配置与日志

def load_cfg():
    try:
        with open(CFG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        AUTO["enabled"] = bool(data.get("enabled"))
        AUTO["interval_min"] = max(5, int(data.get("interval_min") or 60))
        AUTO["last_run"] = data.get("last_run") or ""
    except (OSError, ValueError):
        pass


def save_cfg():
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            json.dump({"enabled": AUTO["enabled"], "interval_min": AUTO["interval_min"],
                       "last_run": AUTO["last_run"]}, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def load_job():
    """把上次的任务状态捡回来。

    更新会触发重启，进程没了内存状态也就没了，页面会以为「什么都没发生」。
    落一份到 data/ 里，新进程起来接着显示，页面才知道该刷新。
    """
    try:
        with open(JOB_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    with LOCK:
        for k in ("action", "started_at", "elapsed", "result", "error"):
            if k in data:
                JOB[k] = data[k]
        JOB["log"] = list(data.get("log") or [])[-120:]
        JOB["running"] = False
        if data.get("running") and data.get("action") == "update" and not JOB.get("result"):
            # 拉取过程中被重启打断：代码多半已经落盘，算成功但留个说明
            JOB["result"] = "ok"
            JOB["error"] = "更新在重启中完成"


def save_job():
    try:
        os.makedirs(DATA, exist_ok=True)
        with LOCK:
            payload = dict(JOB)
            payload["log"] = list(JOB["log"])[-120:]
        payload["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = JOB_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, JOB_FILE)
    except OSError:
        pass


def say(msg=""):
    with LOCK:
        JOB["log"].append(msg)
        JOB["log"] = JOB["log"][-240:]


def save_cache():
    """给 routes/version.py 的兜底缓存用：更新服务不可达时页面还能显示上次版本。"""
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, "version.json"), "w", encoding="utf-8") as f:
            json.dump({"status": STATUS, "auto": AUTO,
                       "agent": state()["agent"],
                       "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                      f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------- 对外

def state():
    with LOCK:
        job = dict(JOB)
        job["log"] = list(JOB["log"])[-160:]
    db = Config.DB_PATH
    try:
        db_mb = round(os.path.getsize(db) / 1048576.0, 1)
    except OSError:
        db_mb = None
    return {
        "ok": True,
        "agent": {"online": True, "pid": os.getpid(), "started_at": STARTED,
                  "host": socket.gethostname(), "platform": platform.platform(),
                  "mode": MODE, "restart_cmd": ""},
        "status": STATUS,
        "auto": dict(AUTO),
        "job": job,
        "local": {"python": platform.python_version(), "platform": platform.system(),
                  "root": ROOT, "db": db, "db_mb": db_mb},
        "now": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def start(action="update", force=False):
    """起一个后台任务：check 或 update。"""
    ok, why = available()
    if not ok:
        return False, why
    with LOCK:
        if JOB["running"]:
            return False, BUSY
        JOB.update({"running": True, "action": action,
                    "started_at": time.strftime("%H:%M:%S"), "elapsed": 0,
                    "log": [], "result": "", "error": ""})
    save_job()
    threading.Thread(target=_job, args=(action, force), daemon=True).start()
    return True, ""


def _job(action, force):
    global STATUS
    t0 = time.time()
    say("〔%s〕开始（容器内更新）" % ("更新" if action == "update" else "检查"))
    try:
        st = status(fetch=True)
        if not st["ok"] and st["error"]:
            raise RuntimeError(st["error"])
        with LOCK:
            STATUS = st
        save_cache()

        if st["behind"] == 0:
            say("已经是最新（%s）" % st["short"])
        else:
            say("落后 %d 个提交，远程 %s" % (st["behind"], st.get("remote_short", "?")))
            for c in st["commits"][:10]:
                say("  %s  %s" % (c["hash"], c["subject"]))

        if action == "update":
            if not st["behind"]:
                say("没有新提交，不用更新")
                _finish(t0, "ok")
                return
            if st["dirty"] and not force:
                raise RuntimeError("工作区有未提交的改动，没敢拉。先 git stash 或提交后再试")
            ok, out = _git(["pull", "--ff-only", "origin", st["branch"]], timeout=120)
            if not ok:
                raise RuntimeError("拉取失败：" + (out.splitlines()[-1] if out else ""))
            say("拉取完成，正在切到新代码…")
            # 关键：先把结果落盘，再等热重载——supervisor 大约 1 秒后就会
            # 重启本进程，晚于这一步写文件就来不及了，页面会一直转圈。
            _finish(t0, "ok")

        with LOCK:
            STATUS = status(fetch=False)
        save_cache()
    except Exception as e:                  # noqa: BLE001
        say("〔失败〕%s" % e)
        with LOCK:
            JOB["result"] = "fail"
            JOB["error"] = str(e)
        _finish(t0, "fail")
    else:
        _finish(t0, JOB["result"] or "ok")


def _finish(t0, result):
    with LOCK:
        if JOB["result"] != "fail":
            JOB["result"] = result
        JOB["running"] = False
        JOB["elapsed"] = int(time.time() - t0)
    AUTO["last_check"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if JOB.get("action") == "update" and result == "ok":
        AUTO["last_run"] = AUTO["last_check"]
        save_cfg()
    say("〔完成〕用时 %d 秒" % JOB["elapsed"])
    save_job()


def set_auto(enabled=None, interval_min=None):
    if enabled is not None:
        AUTO["enabled"] = bool(enabled)
    if interval_min:
        AUTO["interval_min"] = max(5, int(interval_min))
    save_cfg()
    return dict(AUTO)


def auto_loop():
    """到点自己看一眼，有新版且工作区干净就自己拉。"""
    time.sleep(10)
    while True:
        try:
            if AUTO["enabled"] and not JOB["running"]:
                last = AUTO.get("last_check") or ""
                due = True
                if last:
                    try:
                        t = time.mktime(time.strptime(last, "%Y-%m-%d %H:%M:%S"))
                        due = time.time() - t >= AUTO["interval_min"] * 60
                    except ValueError:
                        due = True
                if due:
                    with LOCK:
                        AUTO["last_check"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    ok, why = start("update")
                    if not ok:
                        say("自动更新跳过：%s" % why)
        except Exception as e:              # noqa: BLE001
            say("自动更新出错：%s" % e)
        time.sleep(60)


_booted = False


def boot():
    """由 main.py 在启动时调用一次：捡回状态、按配置拉起自动更新。"""
    global _booted, STATUS
    if _booted:
        return
    _booted = True
    ok, _ = available()
    if not ok:
        return
    load_cfg()
    load_job()
    try:
        STATUS = status(fetch=False)
    except Exception as e:                  # noqa: BLE001
        STATUS = {"ok": False, "error": str(e)}
    threading.Thread(target=auto_loop, daemon=True).start()
