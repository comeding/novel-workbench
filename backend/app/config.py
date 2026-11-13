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
    # 默认走 app_api 版：直接请求 App 的榜单接口，不依赖模拟器与 adb，
    # 跨平台、8 秒采完 19 个数据集。想回到模拟器方案就设：
    #   WB_COLLECT_SCRIPT=collector/collect.py
    COLLECT_SCRIPT = _path("WB_COLLECT_SCRIPT", os.path.join("collector", "api_rank.py"))

    # 数据超过这么久（小时）就在启动时自动补一轮；默认 6
    AUTO_COLLECT_AGE = float(os.environ.get("WB_AUTO_COLLECT_AGE") or 6) * 3600

    # 常驻运行时的定时采集：每隔这么多小时检查一次，数据过期就补一轮。
    # 0 = 关闭（只靠启动时那一次）。容器里建议开着，否则不重启就一直吃老数据。
    COLLECT_EVERY = float(os.environ.get("WB_COLLECT_EVERY") or 0) * 3600

    # 书架数快照：每天记一次「加入书架」人数，供「近 24 小时新增收藏」做差。
    # 没有历史就没有增量，所以这条要跟榜单采集一起常驻。0 = 关闭。
    SHELF_SNAPSHOT_SCRIPT = _path("WB_SHELF_SCRIPT",
                                  os.path.join("collector", "shelf_snapshot.py"))
    SHELF_SNAPSHOT_EVERY = float(os.environ.get("WB_SHELF_EVERY") or 24) * 3600

    # 补「加入书架」数和「催更」次数：榜单接口只给热度，这两项要去书籍页抓。
    # 每次采集后新出现的书都得补一次，否则那两列只能显示「—」。
    DETAIL_SCRIPT = _path("WB_DETAIL_SCRIPT", os.path.join("collector", "detail.py"))
    DETAIL_LIMIT = int(os.environ.get("WB_DETAIL_LIMIT") or 400)

    # 开发模式：前端静态资源一律 no-store。
    # 生产默认给 .js/.css 一个小时的缓存，改完要强刷才看得到；
    # 开发时（配合 docker-compose.dev.yml 的 bind mount）设 WB_DEV=1 省掉这一步。
    DEV = os.environ.get("WB_DEV", "").strip().lower() in ("1", "true", "yes", "on")

    @classmethod
    def api_mode(cls):
        """是否使用直连接口采集（无需模拟器）。

        模拟器方案（collect.py）依赖 Windows + MuMu + adb；接口方案
        （api_rank.py）只发 HTTP 请求，任何系统都能跑。
        """
        return os.path.basename(cls.COLLECT_SCRIPT) in ("api_rank.py", "h5_rank.py")

    @classmethod
    def collect_enabled(cls):
        """能不能在这台机器上真采。

        模拟器方案要求「本机 + Windows + 脚本存在」；接口方案只要求
        「本机 + 脚本存在」，所以 Linux / macOS 也能在页面上点采集。
        """
        if cls.ALLOW_COLLECT is not None:
            return cls.ALLOW_COLLECT
        if not (cls.HOST in ("127.0.0.1", "localhost")
                and os.path.exists(cls.COLLECT_SCRIPT)):
            return False
        return cls.api_mode() or sys.platform == "win32"

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
