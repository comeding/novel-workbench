"""健康检查、库统计、本机 token 查看。"""
import sys

from .. import db
from ..config import Config, get_token
from ..router import Router, json_response

router = Router()

LOCAL = {"127.0.0.1", "::1", "localhost"}


@router.get(r"/api/health")
def health(req):
    return {
        "ok": True,
        "service": "novel-workbench",
        "python": sys.version.split()[0],
        "port": req.server_port,
        "collect_enabled": Config.collect_enabled(),
        "db": Config.DB_PATH,
    }


@router.get(r"/api/stats")
def stats(req):
    s = db.stats()
    s["ok"] = True
    return s


@router.get(r"/api/token")
def token(req):
    """只在服务端本机可读，方便采集端自动取到口令。"""
    if req.client not in LOCAL:
        return json_response({"ok": False, "error": "只允许本机查看"}, 403)
    return {"ok": True, "token": get_token()}
