#!/usr/bin/env python
"""版本服务：页面上的「检查更新 / 立即更新 / 自动更新」背后的执行器。

为什么必须有这么个东西在宿主上跑：
容器里既没有 git 仓库、也没有 docker 权限，自己没法拉代码、更没法重建自己。
所以把「动代码」的活交给宿主机上的一个零依赖小服务，页面只负责下令和看进度。

它监听 127.0.0.1:8766（只本机可达，不对外暴露），页面同源代理过来调。

    python dev/agent.py                 # 前台跑，Ctrl+C 停
    python dev/agent.py --interval 30   # 顺带每 30 分钟自动检查一次
    python dev/agent.py --install       # 注册开机自启（Windows 计划任务）
    python dev/agent.py --uninstall     # 取消开机自启

Windows 上双击「⑤ 版本服务.bat」等价于第一条。
"""

import json
import os
import platform
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import updater

HOST = "127.0.0.1"
PORT = 8766
CFG = os.path.join(updater.DATA, "update.cfg.json")
CACHE = os.path.join(updater.DATA, "version.json")
LOGFILE = os.path.join(updater.DATA, "update.log")
TASK = "网文工作站更新服务"

argv = sys.argv[1:]
IMAGE_MODE = "--image" in argv
FORCE = "--force" in argv


def opt(name, cast=str, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            try:
                return cast(argv[i + 1])
            except ValueError:
                return default
    return default


LOCK = threading.Lock()
JOB = {"running": False, "action": "", "started_at": "", "elapsed": 0,
       "log": [], "result": "", "error": ""}
STATUS = {}                 # 最近一次 updater.status() 的结果
AUTO = {"enabled": False, "interval_min": 60, "last_check": "", "last_run": ""}
RESTART_CMD = opt("--restart-cmd")
STARTED = time.strftime("%Y-%m-%d %H:%M:%S")


def say(msg=""):
    print(msg, flush=True)
    with LOCK:
        JOB["log"].append(msg)
        JOB["log"] = JOB["log"][-240:]
        if JOB["running"]:
            try:
                with open(LOGFILE, "a", encoding="utf-8") as f:
                    f.write(time.strftime("%H:%M:%S ") + msg + "\n")
            except OSError:
                pass


def load_cfg():
    try:
        with open(CFG, encoding="utf-8") as f:
            data = json.load(f)
        AUTO["enabled"] = bool(data.get("enabled"))
        AUTO["interval_min"] = int(data.get("interval_min") or 60)
        AUTO["last_run"] = data.get("last_run") or ""
    except Exception:                       # noqa: BLE001
        pass


def save_cfg():
    os.makedirs(updater.DATA, exist_ok=True)
    try:
        with open(CFG, "w", encoding="utf-8") as f:
            json.dump({"enabled": AUTO["enabled"], "interval_min": AUTO["interval_min"],
                       "last_run": AUTO["last_run"]}, f, ensure_ascii=False, indent=2)
    except OSError as e:
        say("  配置写不进去：%s" % e)


def save_cache():
    """落一份到 data/，这样页面拿不到本服务时，后端还能显示「上次看到的版本」。"""
    os.makedirs(updater.DATA, exist_ok=True)
    payload = {
        "status": STATUS,
        "auto": AUTO,
        "agent": {"online": True, "pid": os.getpid(), "started_at": STARTED,
                  "host": platform.node(), "mode": "image" if IMAGE_MODE else "mount"},
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def state():
    with LOCK:
        job = dict(JOB)
        job["log"] = list(JOB["log"])[-160:]
    return {
        "ok": True,
        "agent": {"online": True, "pid": os.getpid(), "started_at": STARTED,
                  "interval_cli": opt("--interval", int),
                  "host": platform.node(), "platform": platform.platform(),
                  "mode": "image" if IMAGE_MODE else "mount",
                  "restart_cmd": RESTART_CMD or ""},
        "status": STATUS,
        "auto": dict(AUTO),
        "job": job,
        "local": updater.version_info(),
        "backup_hint": updater.backup_hint(),
        "now": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def run_job(action, force=False):
    """线程里跑：检查 / 更新。同一时刻只允许一个。"""
    with LOCK:
        if JOB["running"]:
            return False, "已经有一个任务在跑"
        JOB.update({"running": True, "action": action, "started_at": time.strftime("%H:%M:%S"),
                    "elapsed": 0, "log": [], "result": "", "error": ""})
    threading.Thread(target=_job, args=(action, force), daemon=True).start()
    return True, ""


def _job(action, force):
    t0 = time.time()
    global STATUS
    say("〔%s〕开始" % ("更新" if action == "update" else "检查"))
    try:
        st = updater.status(fetch=True)
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
                if force:
                    say("没有新提交，还是按当前代码重建一次")
                else:
                    say("没有新提交，不用重建")
                    with LOCK:
                        JOB["result"] = "ok"
                    return
            if st["dirty"] and not force:
                raise RuntimeError("工作区有未提交的改动，没敢拉。先 git stash，或手动处理后再来")
            if st["behind"]:
                ok, out = updater.pull(branch=st["branch"])
                if not ok:
                    raise RuntimeError("拉取失败（多半是本地改动撞上了）：" + (out.splitlines()[-1] if out else ""))
                say("拉取完成，现在 %s" % st.get("remote_short", "?"))
            ok, msg = updater.rebuild(IMAGE_MODE, restart_cmd=RESTART_CMD, log=say)
            if not ok:
                raise RuntimeError(msg)
            say("等看板起来…")
            h = updater.wait_health(40)
            if not h:
                raise RuntimeError("重建完了但 40 秒内没等到 /api/health，看 docker compose logs")
            say("就绪：http://127.0.0.1:%s" % updater.port())
            # 更新后重新读一次版本，页面刷新就能看到新提交
            with LOCK:
                STATUS = updater.status(fetch=False)

    except Exception as e:                  # noqa: BLE001
        say("〔失败〕%s" % e)
        with LOCK:
            JOB["result"] = "fail"
            JOB["error"] = str(e)
    else:
        with LOCK:
            if JOB["result"] != "fail":
                JOB["result"] = "ok"
        if action == "update":
            AUTO["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_cfg()
        say("〔完成〕用时 %d 秒" % (time.time() - t0))
    finally:
        with LOCK:
            JOB["running"] = False
            JOB["elapsed"] = int(time.time() - t0)
        AUTO["last_check"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_cache()


def auto_loop():
    """自动模式：到点问一次远端，有新版且工作区干净就自己更新。"""
    time.sleep(5)
    while True:
        try:
            interval = max(5, int(AUTO["interval_min"] or 60))
            if AUTO["enabled"] and not JOB["running"]:
                last = AUTO["last_check"]
                due = True
                if last:
                    try:
                        due = (time.time() - time.mktime(time.strptime(last, "%Y-%m-%d %H:%M:%S"))) >= interval * 60
                    except ValueError:
                        due = True
                if due:
                    say("自动检查：距上次 %d 分钟，开始问 GitHub" % interval)
                    st = updater.status(fetch=True)
                    if st["ok"] and st["behind"]:
                        if st["dirty"]:
                            say("有新版但工作区不干净，跳过（不覆盖你的改动）")
                        else:
                            run_job("update")
                    elif st["ok"]:
                        say("自动检查：已是最新")
                    else:
                        say("自动检查失败：%s" % st["error"])
                    AUTO["last_check"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    save_cache()
        except Exception as e:              # noqa: BLE001
            say("自动检查出错：%s" % e)
        time.sleep(30)


class Handler(BaseHTTPRequestHandler):
    server_version = "WBUpdater/1.0"

    def log_message(self, fmt, *args):       # 别把每个请求都打到控制台
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-store")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/api/state", "/health"):
            return self._json(state())
        if path == "/api/ping":
            return self._json({"ok": True, "pid": os.getpid()})
        return self._json({"ok": False, "error": "没有这个接口"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except ValueError:
            body = {}

        if path == "/api/check":
            ok, err = run_job("check")
            return self._json({"ok": ok, "error": err}, 200 if ok else 409)

        if path == "/api/update":
            ok, err = run_job("update", force=bool(body.get("force")) or FORCE)
            return self._json({"ok": ok, "error": err}, 200 if ok else 409)

        if path == "/api/auto":
            AUTO["enabled"] = bool(body.get("enabled"))
            if body.get("interval_min"):
                try:
                    AUTO["interval_min"] = max(5, int(body["interval_min"]))
                except (TypeError, ValueError):
                    pass
            save_cfg()
            save_cache()
            say("自动更新已%s（间隔 %d 分钟）"
                % ("开启" if AUTO["enabled"] else "关闭", AUTO["interval_min"]))
            return self._json({"ok": True, "auto": AUTO})

        return self._json({"ok": False, "error": "没有这个接口"}, 404)


def install_task():
    if sys.platform != "win32":
        say("Windows 才用得上 --install。Linux 上建一个 systemd user unit：")
        say("")
        say("  ~/.config/systemd/user/novel-agent.service")
        say("  [Unit]\\n  Description=novel-workbench updater\\n"
            "  [Service]\\n  ExecStart=%s %s\\n  Restart=always\\n"
            "  [Install]\\n  WantedBy=default.target" % (sys.executable, os.path.abspath(__file__)))
        say("")
        say("  systemctl --user enable --now novel-agent")
        return 1
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw                       # 用 pythonw 跑，不弹黑窗口
    cmd = ['schtasks', '/create', '/tn', TASK, '/sc', 'onlogon',
           '/tr', '"%s" "%s"' % (exe, os.path.abspath(__file__)), '/f']
    code, out = updater.sh(cmd, cwd=updater.ROOT, capture=True)
    if code != 0:
        say("注册失败：%s" % (out or "schtasks 返回 %d" % code))
        say("  可能需要以管理员身份运行一次，或手动把它拖进「启动」文件夹。")
        return 1
    say("已注册开机自启：%s（登录后自动在后台运行）" % TASK)
    say("取消：python dev/agent.py --uninstall")
    return 0


def uninstall_task():
    if sys.platform != "win32":
        say("Linux：systemctl --user disable --now novel-agent")
        return 0
    code, out = updater.sh(['schtasks', '/delete', '/tn', TASK, '/f'], capture=True)
    say("已取消开机自启" if code == 0 else ("没找到计划任务：%s" % (out or code)))
    return 0


def main():
    global PORT
    if "--install" in argv:
        return install_task()
    if "--uninstall" in argv:
        return uninstall_task()

    PORT = opt("--port", int, PORT) or PORT
    host = opt("--host", str, HOST) or HOST
    load_cfg()
    if opt("--interval", int):
        AUTO["enabled"] = True
        AUTO["interval_min"] = max(5, opt("--interval", int))

    say("网文工作站 · 版本服务")
    say("")
    say("  仓库      %s" % updater.ROOT)
    say("  监听      http://%s:%d" % (host, PORT))
    if not updater.is_repo():
        say("  ⚠ 这个目录不是 git 仓库，只能看版本、不能更新")
    st = updater.status(fetch=False)
    say("  当前版本  %s  %s" % (st["short"] or "?", st["subject"] or ""))
    say("  自动更新  %s（间隔 %d 分钟）"
        % ("开" if AUTO["enabled"] else "关", AUTO["interval_min"]))
    say("")
    say("  页面：左侧菜单「版本控制」。Ctrl+C 停止。")
    say("")

    global STATUS
    STATUS = st
    save_cache()

    if AUTO["enabled"] or AUTO["interval_min"]:
        threading.Thread(target=auto_loop, daemon=True).start()

    srv = None
    try:
        srv = ThreadingHTTPServer((host, PORT), Handler)
    except OSError as e:
        say("  端口 %d 起不来（%s）。多半是已经开着一个了，换一个：--port 8767" % (PORT, e))
        return 1
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        say("")
        say("  已停止")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                       # noqa: BLE001
        pass
    sys.exit(main())
