"""从网页触发采集。

这里只是"代跑"本机的采集脚本：起一个子进程，把它的 stdout 解析成进度。
真正的采集逻辑在 collector/，服务端不碰 ADB。
"""
import os
import re
import subprocess
import sys
import threading
import time

from ..config import Config
from ..router import Router, json_response

router = Router()

MAX_LINES = 400
RE_BOARD = re.compile(r"^\[(.+?)\]$")
RE_CATS = re.compile(r"分类\s*(\d+)\s*个")
RE_DONE = re.compile(r"cid=|✗ ")


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.proc = None
        self.lines = []
        self.boards = []
        self.started = None
        self.ended = None
        self.ok = None
        self.error = ""

    def reset(self):
        self.lines = []
        self.boards = []
        self.ok = None
        self.error = ""
        self.ended = None

    def feed(self, line):
        with self.lock:
            self.lines.append(line)
            if len(self.lines) > MAX_LINES:
                del self.lines[:len(self.lines) - MAX_LINES]

            m = RE_BOARD.match(line.strip())
            if m:
                self.boards.append({"name": m.group(1), "cats_total": 0, "cats_done": 0})
                return
            if not self.boards:
                return
            cur = self.boards[-1]
            m = RE_CATS.search(line)
            if m:
                cur["cats_total"] = int(m.group(1))
                return
            if RE_DONE.search(line) and cur["cats_done"] < 999:
                cur["cats_done"] += 1

    def snapshot(self):
        with self.lock:
            cur = self.boards[-1] if self.boards else None
            total = sum(b["cats_total"] for b in self.boards)
            done = sum(b["cats_done"] for b in self.boards)
            elapsed = 0
            if self.started:
                elapsed = (time.time() if self.running else self.ended or time.time()) - self.started
            return {
                "running": self.running, "ok": self.ok, "error": self.error,
                "started": self.started, "ended": self.ended,
                "elapsed": round(elapsed, 1),
                "board": cur["name"] if cur else "",
                "cats_done": cur["cats_done"] if cur else 0,
                "cats_total": cur["cats_total"] if cur else 0,
                "total_cats": total, "done_cats": done,
                "boards": list(self.boards),
                "lines": self.lines[-14:],
            }


STATE = State()


def _run(boards, push_url):
    cmd = [sys.executable, "-u", Config.COLLECT_SCRIPT] + list(boards or [])
    if push_url:
        cmd += ["--push", push_url]

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.pop("PYTHONHOME", None)

    try:
        proc = subprocess.Popen(
            cmd, cwd=os.path.dirname(Config.COLLECT_SCRIPT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace", bufsize=1,
        )
    except Exception as e:                      # noqa: BLE001
        with STATE.lock:
            STATE.running = False
            STATE.ok = False
            STATE.error = "启动采集进程失败：%s" % e
            STATE.ended = time.time()
        return

    with STATE.lock:
        STATE.proc = proc

    for line in proc.stdout:
        STATE.feed(line.rstrip("\r\n"))
    proc.wait()

    with STATE.lock:
        STATE.running = False
        STATE.proc = None
        STATE.ended = time.time()
        STATE.ok = proc.returncode == 0
        if not STATE.ok and not STATE.error:
            STATE.error = "采集进程退出码 %s" % proc.returncode


@router.post(r"/api/collect")
def collect(req):
    if not Config.collect_enabled():
        return json_response({"ok": False, "error": "这台机器不允许触发采集（部署模式）"}, 403)
    if not os.path.exists(Config.COLLECT_SCRIPT):
        return json_response({"ok": False, "error": "找不到采集脚本：%s" % Config.COLLECT_SCRIPT}, 500)

    try:
        payload = req.json()
    except ValueError:
        payload = {}
    boards = payload.get("boards")

    with STATE.lock:
        if STATE.running:
            return json_response({"ok": False, "error": "已经在采集了"}, 409)
        STATE.running = True
        STATE.started = time.time()
        STATE.reset()

    # 采集完让采集器自己把结果推回来，走同一条入库路径
    host = "127.0.0.1"
    push = "http://%s:%d" % (host, req.server_port)
    threading.Thread(target=_run, args=(boards, push), daemon=True).start()
    return {"ok": True, "started": True}


@router.get(r"/api/collect/status")
def status(req):
    return STATE.snapshot()


@router.post(r"/api/collect/cancel")
def cancel(req):
    with STATE.lock:
        proc = STATE.proc
    if proc and proc.poll() is None:
        proc.terminate()
        return {"ok": True, "cancelled": True}
    return json_response({"ok": False, "error": "没有在跑的采集"}, 409)
