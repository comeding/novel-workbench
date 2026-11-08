"""接收采集端上报。"""
from ..auth import require_token
from ..router import Router, json_response
from ..services import ingest as ingest_service

router = Router()

MAX_BODY = 32 * 1024 * 1024      # 单批原始数据 30MB 上限，正常一份约 5MB


@router.post(r"/api/ingest")
def ingest(req):
    bad = require_token(req)
    if bad:
        return bad
    if len(req.body) > MAX_BODY:
        return json_response({"ok": False, "error": "数据太大"}, 413)
    try:
        payload = req.json()
    except ValueError as e:
        return json_response({"ok": False, "error": str(e)}, 400)
    try:
        result = ingest_service.ingest(payload)
    except ValueError as e:
        return json_response({"ok": False, "error": str(e)}, 400)
    return {"ok": True, **result}


@router.post(r"/api/details")
def details(req):
    """书籍详情（加入书架人数）上报。见 collector/detail.py。"""
    bad = require_token(req)
    if bad:
        return bad
    if len(req.body) > MAX_BODY:
        return json_response({"ok": False, "error": "数据太大"}, 413)
    try:
        payload = req.json()
    except ValueError as e:
        return json_response({"ok": False, "error": str(e)}, 400)
    try:
        result = ingest_service.ingest_details(payload)
    except ValueError as e:
        return json_response({"ok": False, "error": str(e)}, 400)
    return {"ok": True, **result}
