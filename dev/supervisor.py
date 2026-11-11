#!/usr/bin/env python
"""开发用：监听 .py 改动，自动重启后端。

    python dev/supervisor.py

前端不需要它：静态文件每次请求都从磁盘读，改完刷新浏览器就是新的
（配合 WB_DEV=1 关掉缓存）。只有 Python 代码必须重启进程才生效，
这个脚本就是替你按那一下重启。

为什么是轮询而不是 inotify：Windows 上 Docker 的 bind mount 事件通知不可靠，
同一个文件连续保存可能收不到事件，会漏掉改动。轮询 mtime_ns + 大小在
Windows / Linux 上都稳，代价是每秒扫几十个文件，可以忽略。

只用标准库 —— 整个项目不装第三方依赖，这个脚本也不例外。
"""
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCH = ("backend", "collector")
INTERVAL = 1.0
CMD = [sys.executable, "-u", os.path.join(ROOT, "backend", "server.py"), "--no-open"]


def say(msg):
    print("  %s" % msg, flush=True)


def snapshot():
    """扫描关注目录，返回 {绝对路径: (mtime_ns, 大小)}。"""
    state = {}
    for name in WATCH:
        base = os.path.join(ROOT, name)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for f in filenames:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(dirpath, f)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                state[path] = (st.st_mtime_ns, st.st_size)
    return state


def _changed(old, new):
    paths = [p for p, s in new.items() if old.get(p) != s]
    paths += [p for p in old if p not in new]
    paths.sort()
    return paths


def _names(paths):
    rel = [os.path.relpath(p, ROOT).replace(os.sep, "/") for p in paths]
    if len(rel) > 3:
        return "%s 等 %d 个文件" % ("、".join(rel[:3]), len(rel))
    return "、".join(rel)


def _touches_backend(paths):
    """只有 backend/ 的改动需要重启进程；collector/ 是下次采集才用，不用重启。"""
    prefix = os.path.join(ROOT, "backend") + os.sep
    return any(p.startswith(prefix) for p in paths)


def _settle(cur):
    """等文件写完再动手。

    编辑器保存一个文件常常是「截断 + 写入」两次落盘，直接重启会连着重启两回，
    日志刷两行、页面白闪两次。这里等到连续 1 秒没有新变化再继续。
    """
    quiet = 0.0
    while quiet < 1.0:
        time.sleep(0.25)
        again = snapshot()
        quiet = quiet + 0.25 if again == cur else 0.0
        cur = again
    return cur


def spawn():
    return subprocess.Popen(CMD, cwd=ROOT, start_new_session=True)


def stop(proc):
    """连同采集子进程一起停掉，别留孤儿占着 adb / 端口。"""
    if proc.poll() is not None:
        return
    _signal(proc, signal.SIGTERM, "terminate")
    try:
        proc.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    _signal(proc, getattr(signal, "SIGKILL", signal.SIGTERM), "kill")
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass


def _signal(proc, sig, fallback):
    # Windows 没有 killpg，只能 terminate（孙进程得靠主进程自己收尾）
    if hasattr(os, "killpg"):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
            return
        except OSError:
            pass
    getattr(proc, fallback)()


def main():
    if not os.path.exists(CMD[2]):
        say("找不到 %s" % CMD[2])
        return 1

    say("开发模式    监听 %s 下的 .py，改完自动重启" % " / ".join(WATCH))
    say("            前端改动不用重启，刷新浏览器即可")
    proc = spawn()
    last = snapshot()
    try:
        while True:
            time.sleep(INTERVAL)
            cur = snapshot()
            if cur != last:
                paths = _changed(last, cur)
                cur = _settle(cur)          # 等保存动作落完盘
                paths = _changed(last, cur) or paths
                if _touches_backend(paths):
                    say("改动 %s → 重启" % _names(paths))
                    stop(proc)
                    proc = spawn()
                else:
                    say("改动 %s → 采集器，下次采集自然生效" % _names(paths))
                last = snapshot()
                continue
            code = proc.poll()
            if code is not None:
                say("服务退出（code=%s），2 秒后重启" % code)
                time.sleep(2)
                proc = spawn()
                last = snapshot()
    except KeyboardInterrupt:
        say("收到 Ctrl+C，停止")
    finally:
        stop(proc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
