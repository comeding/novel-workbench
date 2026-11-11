"""HTTP 服务入口：API + 前端静态文件，一个进程搞定。

  python backend/server.py                 本机启动并打开浏览器
  python backend/server.py --no-open       只启动
  python backend/server.py --host 0.0.0.0  允许外部访问（部署形态）
"""
import gzip
import os
import sys
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import db
from .config import Config
from .router import Request, Router, json_response
from .routes import boards, books, collect, ingest, runs, system, version

VERSION = "2.0"


def build_router():
    r = Router()
    for mod in (system, boards, books, runs, ingest, collect, version):
        r.routes.extend(mod.router.routes)
    return r


ROUTER = build_router()
QUIET = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)
COMPRESSIBLE = ("application/json", "text/", "application/javascript", "image/svg+xml")


class Handler(SimpleHTTPRequestHandler):
    server_version = "NovelWorkbench/" + VERSION
    protocol_version = "HTTP/1.1"

    # ---------- 生命周期 ----------
    def handle_one_request(self):
        try:
            super().handle_one_request()
        except QUIET:
            self.close_connection = True

    def end_headers(self):
        # 前端可能独立托管，接口一律放开跨域（鉴权走 header token，不吃 cookie）
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", getattr(self, "_cache", "no-store"))
        super().end_headers()

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/") and self.command == "POST":
            sys.stderr.write("  %s %s\n" % (self.command, self.path))

    # ---------- 分发 ----------
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Token")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            return self._api("GET", parsed, b"")
        # 开发模式下一律不缓存，免得改了 js 还要手动强刷
        if not Config.DEV and parsed.path != "/" and not parsed.path.endswith(".html"):
            self._cache = "public, max-age=3600"
        return super().do_GET()

    def do_HEAD(self):
        if urlparse(self.path).path.startswith("/api/"):
            self.send_response(405)
            self.end_headers()
            return
        return super().do_HEAD()

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if parsed.path.startswith("/api/"):
            return self._api("POST", parsed, body)
        return json_response({"ok": False, "error": "接口不存在"}, 404)

    def _api(self, method, parsed, body):
        req = Request(
            method=method,
            path=parsed.path,
            query=parse_qs(parsed.query),
            headers=self.headers,
            body=body,
            client=self.client_address[0] if self.client_address else "",
            server_port=self.server.server_port,
        )
        res = ROUTER.dispatch(req)

        payload = res.body
        headers = dict(res.headers)
        if (len(payload) > 1024 and res.ctype.split(";")[0] in COMPRESSIBLE
                and "gzip" in (self.headers.get("Accept-Encoding") or "")):
            payload = gzip.compress(payload, 6)
            headers["Content-Encoding"] = "gzip"

        try:
            self.send_response(res.status)
            self.send_header("Content-Type", res.ctype)
            self.send_header("Content-Length", str(len(payload)))
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except QUIET:
            self.close_connection = True


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], QUIET):
            return
        super().handle_error(request, client_address)


def _already_running(port):
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % port, timeout=1.5) as r:
            return r.status == 200
    except Exception:                            # noqa: BLE001
        return False


def _auto_collect(push_url):
    """后台抓一轮最新数据，失败只打印，不影响服务本身。"""
    ok, err = collect.start(push_url=push_url)
    if ok:
        print("  自动采集    已开始，进度见页面（%s）" % (
            "约 15 秒" if Config.api_mode() else "约 3 分钟"))
    else:
        print("  自动采集    没起来：%s" % err)
        print("              可以先双击「③ 环境自检.bat」看看缺什么")


def main():
    argv = sys.argv[1:]
    host = Config.HOST
    port = Config.PORT
    open_browser = "--no-open" not in argv
    for flag, name in (("--host", "host"), ("--port", "port")):
        if flag in argv:
            try:
                val = argv[argv.index(flag) + 1]
            except IndexError:
                print("%s 后面要跟参数" % flag)
                return 2
            if name == "host":
                host = val
            else:
                try:
                    port = int(val)
                except ValueError:
                    print("--port 要跟数字")
                    return 2

    if not os.path.isdir(Config.FRONTEND_DIR):
        print("找不到前端目录：%s" % Config.FRONTEND_DIR)
        return 1

    db.init()
    st = db.stats()

    local_only = host in ("127.0.0.1", "localhost")
    if local_only and _already_running(port):
        url = "http://localhost:%d/" % port
        print("服务已经在跑了：%s" % url)
        if open_browser:
            webbrowser.open(url)
        return 0

    handler = lambda *a, **kw: Handler(*a, directory=Config.FRONTEND_DIR, **kw)  # noqa: E731
    httpd = None
    for p in range(port, port + 12):
        try:
            httpd = Server((host, p), handler)
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        print("端口 %d~%d 都被占用了，用 --port 换一个" % (port, port + 11))
        return 1

    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    url = "http://%s:%d/" % (shown, port)
    print("")
    print("  网文工作站  %s" % url)
    print("  数据库      %s（%d 条在榜 / %d 本书）" % (
        Config.DB_PATH, st["entries"], st["books"]))
    print("  触发采集    %s" % (
        "允许" if Config.collect_enabled() else "禁止（只读看板）"))
    print("  采集方式    %s" % ("直连接口（无需模拟器）" if Config.api_mode() else "模拟器 + adb"))
    if not Config.TOKEN:
        print("  上报口令    data/.token（部署到公网请改用 WB_TOKEN 环境变量）")

    push_url = "http://127.0.0.1:%d" % port

    # 第一次跑（库是空的）或数据放太久了，就直接去抓最新一轮，不用先去命令行敲采集。
    # --collect 强制每次都采；WB_AUTO_COLLECT=0 可完全关掉。
    if "--collect" in argv or Config.auto_collect(st["entries"], st.get("collected_at", "")):
        threading.Timer(0.8, _auto_collect, args=(push_url,)).start()
    elif st["entries"]:
        print("  数据新鲜度  %s 采的，先直接用" % (st.get("collected_at") or "?"))
        print("  抓最新数据  点页面右上角「立即采集」，或跑 %s" % os.path.relpath(
            Config.COLLECT_SCRIPT, os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))))

    # 常驻（容器 / NAS）时顺手开个定时巡检，不重启也能一直有新数据
    if collect.start_scheduler(push_url):
        print("  定时采集    每 %g 小时检查一次，数据过期就自动补一轮" % (
            Config.COLLECT_EVERY / 3600))

    print("")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
