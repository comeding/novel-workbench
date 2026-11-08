"""把采集结果上报给后端 /api/ingest。

单独跑也行（补推某次采集）：
    python collector/push.py data/raw/qqreader_app_20261108_1520.json
    python collector/push.py data/raw/xxx.json --push http://你的域名:8765 --token xxx

地址优先级：--push / 环境变量 WB_PUSH / 默认 http://127.0.0.1:8765
口令优先级：--token / WB_TOKEN / 项目 data/.token / 问本机后端要
"""
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILE = os.path.join(ROOT, "data", ".token")
DEFAULT_BASE = "http://127.0.0.1:8765"

# 不走系统代理：上报目标是本机或自己的服务器，被代理拦一道只会白白超时
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def resolve_base(base=None):
    return (base or os.environ.get("WB_PUSH") or DEFAULT_BASE).rstrip("/")


def resolve_token(base, token=None):
    t = (token or os.environ.get("WB_TOKEN") or _read(TOKEN_FILE) or "").strip()
    if t:
        return t
    try:
        with _OPENER.open(base + "/api/token", timeout=3) as r:
            return json.load(r).get("token") or ""
    except Exception:                            # noqa: BLE001
        return ""


def publish(payload, base=None, token=None, timeout=180, endpoint="/api/ingest"):
    base = resolve_base(base)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + endpoint, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Token": resolve_token(base, token)})
    with _OPENER.open(req, timeout=timeout) as res:
        return json.load(res)


def publish_file(path, **kw):
    with open(path, encoding="utf-8") as f:
        return publish(json.load(f), **kw)


def main(argv):
    if not argv:
        print(__doc__)
        return 2

    files = []
    base = token = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--push", "--token") and i + 1 < len(argv):
            if a == "--push":
                base = argv[i + 1]
            else:
                token = argv[i + 1]
            i += 2
            continue
        files.append(a)
        i += 1

    if not files:
        print("要给一个采集结果 json 的路径")
        return 2

    base = resolve_base(base)
    for path in files:
        try:
            r = publish_file(path, base=base, token=token)
        except urllib.error.HTTPError as e:
            print("%s 上报失败：HTTP %s %s" % (os.path.basename(path), e.code,
                                            e.read().decode("utf-8", "replace")[:200]))
            continue
        except Exception as e:                   # noqa: BLE001
            print("%s 上报失败：%s" % (os.path.basename(path), e))
            continue
        if not r.get("ok", True):
            print("%s 被拒绝：%s" % (os.path.basename(path), r.get("error")))
            continue
        print("%s 已入库：%s 条 / %s 个分类（%s）" % (
            os.path.basename(path), r.get("inserted"),
            r.get("cats"), " / ".join(r.get("boards") or [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
