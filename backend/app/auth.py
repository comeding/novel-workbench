"""上报鉴权。部署到公网后，采集端必须带对 token 才能写库。"""
import hmac

from .config import get_token
from .router import json_response


def require_token(req):
    """校验通过返回 None，否则返回一个 401 响应。"""
    want = get_token()
    got = (req.headers.get("X-Token") or req.arg("token") or "").strip()
    # 比字节而不是比 str：口令里可能有中文，compare_digest 遇到非 ASCII 的 str 会抛异常
    if not got or not hmac.compare_digest(got.encode("utf-8"), want.encode("utf-8")):
        return json_response({"ok": False, "error": "token 无效"}, 401)
    return None
