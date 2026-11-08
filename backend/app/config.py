"""运行配置。全部走环境变量，方便 Docker / 本机两种形态共用一套代码。"""
import os
import secrets

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # backend/
ROOT = os.path.dirname(BASE)                                        # 项目根


def _path(env, default):
    v = os.environ.get(env) or default
    return v if os.path.isabs(v) else os.path.join(ROOT, v)


class Config:
    # 监听地址：本机 127.0.0.1，容器内需要 0.0.0.0 才能被外部访问
    HOST = os.environ.get("WB_HOST", "127.0.0.1")
    PORT = int(os.environ.get("WB_PORT", "8765"))

    DB_PATH = _path("WB_DB", os.path.join("data", "workbench.db"))
    FRONTEND_DIR = _path("WB_FRONTEND", "frontend")
    RAW_DIR = _path("WB_RAW", os.path.join("data", "raw"))          # 采集器原始输出
    ARCHIVE_DIR = _path("WB_ARCHIVE", os.path.join("data", "archive"))   # 上报归档

    # 采集端上报用的口令。本机默认空（不校验），部署时务必设置。
    TOKEN = os.environ.get("WB_TOKEN", "").strip()

    # 是否允许从网页触发采集。只有服务跑在本机时才有意义。
    _collect_env = os.environ.get("WB_ALLOW_COLLECT", "").strip().lower()
    ALLOW_COLLECT = _collect_env in ("1", "true", "yes") if _collect_env else None

    # 采集脚本位置（触发采集时用）
    COLLECT_SCRIPT = _path("WB_COLLECT_SCRIPT", os.path.join("collector", "collect.py"))

    @classmethod
    def collect_enabled(cls):
        if cls.ALLOW_COLLECT is not None:
            return cls.ALLOW_COLLECT
        return cls.HOST in ("127.0.0.1", "localhost")


def get_token():
    """没配就现生成一个，并写进 data/.token，避免裸奔。"""
    if Config.TOKEN:
        return Config.TOKEN
    p = os.path.join(os.path.dirname(Config.DB_PATH), ".token")
    try:
        with open(p, encoding="utf-8") as f:
            t = f.read().strip()
        if t:
            return t
    except OSError:
        pass
    t = secrets.token_urlsafe(18)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(t)
    return t
