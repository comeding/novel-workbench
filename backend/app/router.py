"""极简路由。标准库里没有 Web 框架，用正则表自己搭一个，够用且零依赖。"""
import json
import re
import traceback
from urllib.parse import unquote


class Request:
    def __init__(self, method, path, query, headers, body, client, server_port=0):
        self.method = method
        self.path = path
        self.query = query            # {name: [values]}
        self.headers = headers
        self.body = body
        self.client = client
        self.server_port = server_port
        self.params = {}

    def arg(self, name, default=None):
        v = self.query.get(name)
        return v[0] if v else default

    def arg_int(self, name, default=0):
        try:
            return int(self.arg(name, default))
        except (TypeError, ValueError):
            return default

    def json(self):
        if not self.body:
            return {}
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValueError("请求体不是合法 JSON：%s" % e)


class Response:
    def __init__(self, body=b"", status=200, ctype="application/json; charset=utf-8", headers=None):
        self.body = body
        self.status = status
        self.ctype = ctype
        self.headers = headers or {}


def json_response(obj, status=200, headers=None):
    body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return Response(body, status, "application/json; charset=utf-8", headers)


class Router:
    def __init__(self):
        self.routes = []

    def add(self, method, pattern, func):
        self.routes.append((method, re.compile("^" + pattern + "$"), func))

    def get(self, pattern):
        def deco(func):
            self.add("GET", pattern, func)
            return func
        return deco

    def post(self, pattern):
        def deco(func):
            self.add("POST", pattern, func)
            return func
        return deco

    def dispatch(self, req):
        for method, rx, func in self.routes:
            if method != req.method:
                continue
            m = rx.match(req.path)
            if not m:
                continue
            req.params = {k: unquote(v) for k, v in m.groupdict().items()}
            try:
                out = func(req)
            except Exception as e:                      # noqa: BLE001
                traceback.print_exc()
                return json_response({"ok": False, "error": str(e) or e.__class__.__name__}, 500)
            if isinstance(out, Response):
                return out
            if isinstance(out, tuple):
                return json_response(out[0], out[1])
            return json_response(out)
        return json_response({"ok": False, "error": "接口不存在：%s" % req.path}, 404)
