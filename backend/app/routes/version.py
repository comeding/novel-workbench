"""版本信息与更新代理。

执行更新的活由宿主机上的 dev/agent.py 干（容器里没有 git、也没有 docker 权限）。
这里做两件事：

1. 把执行器写下的 data/version.json 读出来给页面兜底——服务没开时
   至少还能显示「上次看到的版本」；
2. 把页面的指令（检查 / 更新 / 自动开关）代转发给执行器。

第 2 件是要点。浏览器里的 127.0.0.1 指的是「打开页面那台电脑」，
别人在自己电脑上看你的看板时，那个地址上没有执行器。所以页面统一请求
同源的 /api/agent/*，由容器内的后端转给宿主机——这中间只有服务端一跳，
浏览器在哪儿都不影响。
"""
import json
import os
import urllib.error
import urllib.request

from .. import db
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
                "error": "连不上宿主更新服务（%s）。在跑看板的那台机器上执行："
                         "python dev/agent.py --lan" % reason}, 502
    except ValueError:
        return {"ok": False, "error": "执行器返回的不是 JSON"}, 502


@router.get(r"/api/agent/state")
def agent_state(req):
    payload, code = _call("/api/state", timeout=4)
    return json_response(payload, code)


@router.post(r"/api/agent/check")
def agent_check(req):
    payload, code = _call("/api/check", "POST", {}, timeout=8)
    return json_response(payload, code)


@router.post(r"/api/agent/update")
def agent_update(req):
    try:
        body = req.json()
    except ValueError:
        body = {}
    payload, code = _call("/api/update", "POST",
                          {"force": bool(body.get("force"))}, timeout=8)
    return json_response(payload, code)


@router.post(r"/api/agent/auto")
def agent_auto(req):
    try:
        body = req.json()
    except ValueError:
        body = {}
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
