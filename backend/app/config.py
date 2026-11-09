"""运行配置。全部走环境变量，方便 Docker / 本机两种形态共用一套代码。"""
import os
import secrets
import sys
import time

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

    # 数据超过这么久（小时）就在启动时自动补一轮；默认 6
    AUTO_COLLECT_AGE = float(os.environ.get("WB_AUTO_COLLECT_AGE") or 6) * 3600

    @classmethod
    def collect_enabled(cls):
        """能不能在这台机器上真采。

        采集器依赖 Windows 上的 MuMu + adb，所以「本机」还不够，
        必须是 Windows 且有采集脚本，否则页面按钮会误导人。
        """
        if cls.ALLOW_COLLECT is not None:
            return cls.ALLOW_COLLECT
        return (cls.HOST in ("127.0.0.1", "localhost")
                and sys.platform == "win32"
                and os.path.exists(cls.COLLECT_SCRIPT))

    @classmethod
    def auto_collect(cls, entries=0, collected_at=""):
        """启动时要不要自动抓一轮最新数据。

        - `WB_AUTO_COLLECT=0` 一律不采（纯展示端）
        - `WB_AUTO_COLLECT=1` 每次启动都采
        - 不设：空库必采；有数据但超过 6 小时也补一次（页面先显示旧数据，后台悄悄刷新）
        """
        v = os.environ.get("WB_AUTO_COLLECT", "").strip().lower()
        if v in ("0", "false", "no", "off"):
            return False
        if not cls.collect_enabled():
            return False
        if v in ("1", "true", "yes", "on"):
            return True
        if not entries:
            return True
        try:
            age = time.time() - time.mktime(time.strptime(collected_at, "%Y-%m-%d %H:%M:%S"))
        except (TypeError, ValueError, OverflowError):
            return True
        return age > cls.AUTO_COLLECT_AGE


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
