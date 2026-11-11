"""版本信息：把「宿主更新服务」的状态透给页面。

执行更新的活由宿主机上的 dev/agent.py 干（容器里没有 git、也没有 docker 权限）。
它每次检查完会把结果写进 data/version.json，这里读出来给页面兜底：
更新服务没开的时候，页面至少还能显示「上次看到的版本」，而不是一片空白。
"""
import json
import os

from .. import db
from ..config import Config
from ..router import Router

router = Router()

DATA_DIR = os.path.dirname(Config.DB_PATH)
CACHE = os.path.join(DATA_DIR, "version.json")

# 宿主更新服务的默认地址。改了端口就在 .env 里设 WB_AGENT_PORT。
AGENT_PORT = os.environ.get("WB_AGENT_PORT", "8766")


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
