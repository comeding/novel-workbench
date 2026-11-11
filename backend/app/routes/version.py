"""版本信息与更新入口。

两种执行方式，按部署形态自动选：

1. **挂载模式（默认）**：容器里装了 git、项目目录整个挂进去，后端自己就能
   `git pull`，文件经挂载落到宿主机，supervisor 几秒内重启。全程不需要
   额外进程，也不需要 docker 权限 —— 见 `updater_local`。
2. **纯镜像 / 裸机**：容器里没有 .git，自己更新不了，就转发给宿主上的
   `dev/agent.py`（老路子，保留兼容）。

不管走哪条，页面都是请求同源的 /api/agent/*，由后端决定谁去干活。
浏览器在哪儿打开都一样 —— 这是从「直连 127.0.0.1:8766」那个坑里学到的。
"""
import json
import os
import urllib.error
import urllib.request

from .. import db, updater_local
from ..config import Config, get_token
from ..router import Router, json_response

router = Router()

DATA_DIR = os.path.dirname(Config.DB_PATH)
CACHE = os.path.join(DATA_DIR, "version.json")

# 宿主更新服务的默认地址。改了端口就在 .env 里设 WB_AGENT_PORT。
AGENT_PORT = os.environ.get("WB_AGENT_PORT", "8766")


def agent_base():
    """执行器地址。

    容器内走 host.docker.internal（compose 里加了 host-gateway，Linux 也能解析），
    裸机直接 127.0.0.1。都能用 WB_AGENT_URL 覆盖，比如执行器在另一台机器上。
    """
    v = (os.environ.get("WB_AGENT_URL") or "").strip()
    if v:
        return v.rstrip("/")
    host = "host.docker.internal" if os.path.exists("/.dockerenv") else "127.0.0.1"
    return "http://%s:%s" % (host, AGENT_PORT)


def _call(path, method="GET", body=None, timeout=6):
    """转发一次，返回 (payload, status)。

    连不上不抛异常：页面需要知道「是执行器没开」而不是「看板挂了」，
    所以降级成 502 + 一句能照着做的话。
    """
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(agent_base() + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    tok = get_token()
    if tok:
        req.add_header("X-Token", tok)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        return json.loads(raw or b"{}"), 200
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read() or b"{}")
        except (ValueError, OSError):
            payload = {"ok": False, "error": "执行器返回 HTTP %d" % e.code}
        return payload, e.code
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        return {"ok": False,
                "error": "连不上宿主更新服务（%s）。容器自己更新不了（纯镜像部署），"
                         "请用命令行：python dev/update.py" % reason}, 502
    except ValueError:
        return {"ok": False, "error": "执行器返回的不是 JSON"}, 502


def local_mode():
    """容器能不能自己更新。不能就走转发。"""
    return updater_local.available()[0]


def _busy(why):
    """把「已经在跑」和「根本不可用」区分开，页面好给不同提示。"""
    return json_response({"ok": False, "error": why},
                         409 if why == updater_local.BUSY else 400)


@router.get(r"/api/agent/state")
def agent_state(req):
    if local_mode():
        return json_response(updater_local.state())
    payload, code = _call("/api/state", timeout=4)
    return json_response(payload, code)


@router.post(r"/api/agent/check")
def agent_check(req):
    if local_mode():
        ok, why = updater_local.start("check")
        return json_response({"ok": True}, 200) if ok else _busy(why)
    payload, code = _call("/api/check", "POST", {}, timeout=8)
    return json_response(payload, code)


@router.post(r"/api/agent/update")
def agent_update(req):
    try:
        body = req.json()
    except ValueError:
        body = {}
    if local_mode():
        ok, why = updater_local.start("update", force=bool(body.get("force")))
        return json_response({"ok": True}, 200) if ok else _busy(why)
    payload, code = _call("/api/update", "POST",
                          {"force": bool(body.get("force"))}, timeout=8)
    return json_response(payload, code)


@router.post(r"/api/agent/auto")
def agent_auto(req):
    try:
        body = req.json()
    except ValueError:
        body = {}
    if local_mode():
        enabled = body.get("enabled")
        return json_response({
            "ok": True,
            "auto": updater_local.set_auto(
                enabled=None if enabled is None else bool(enabled),
                interval_min=body.get("interval_min")),
        })
    payload, code = _call("/api/auto", "POST", body, timeout=6)
    return json_response(payload, code)


@router.get(r"/api/version")
def version(req):
    cached = None
    error = ""
    try:
        with open(CACHE, encoding="utf-8") as f:
            cached = json.load(f)
    except FileNotFoundError:
        error = "还没检查过，或者更新服务从没启动过"
    except (OSError, ValueError) as e:
        error = "缓存读不出来：%s" % e

    s = db.stats()
    return {
        "ok": True,
        "agent_url": "http://127.0.0.1:%s" % AGENT_PORT,
        "cached": cached,
        "error": error,
        "server": {
            "collect_enabled": Config.collect_enabled(),
            "mode": "docker" if os.path.exists("/.dockerenv") else "bare",
            "db": Config.DB_PATH,
            "entries": s.get("entries"),
            "books": s.get("books"),
            "collected_at": s.get("collected_at"),
        },
    }
