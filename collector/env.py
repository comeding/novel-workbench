"""环境探测：ADB 路径、模拟器实例、设备序列号、屏幕分辨率、root 状态。

换一台电脑不需要改代码——这里会自动找 MuMu 的安装位置和实例端口；
找不到就回落到 config.json / 环境变量 / PATH 里的 adb。

优先级：函数参数 > config.json > 环境变量 > 自动探测
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE, "config.json")

APP_PKG = "com.qq.reader"
APP_DB = "/data/data/%s/databases/qqreader_flutter.db" % APP_PKG

# 采集脚本里的点击坐标都是按这个基准分辨率写的，其它分辨率由 Device 自动缩放
BASE_W, BASE_H = 1080, 1920

# MuMu 各版本常见的安装目录名
_SUBDIRS = (
    "Program Files/Netease/MuMu",
    "Program Files (x86)/Netease/MuMu",
    "Program Files/MuMu Player 12",
    "Program Files/MuMuPlayer-12.0",
    "Netease/MuMu",
    "MuMu",
    "Games/MuMu",
)


# ---------------------------------------------------------------- 基础工具
def load_config():
    """读 config.json；不存在或损坏都返回空字典。"""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _run(cmd, timeout=20):
    """跑个命令，返回 (stdout+stderr 文本)。失败不抛异常。"""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except Exception:
        return ""
    out = r.stdout.decode("utf-8", "replace")
    err = r.stderr.decode("utf-8", "replace")
    return out + err


def _exists(p):
    return bool(p) and os.path.exists(p)


# ---------------------------------------------------------------- 找 MuMu
def mumu_roots():
    """返回所有像 MuMu 安装根目录的路径（已去重、已存在）。"""
    cands = []
    for var in ("MUMU_HOME", "MUMU_ROOT", "MUMU_PATH"):
        if os.environ.get(var):
            cands.append(os.environ[var])

    # 常见路径：逐个盘符试，代价只有几次 stat，很快
    for drive in "CDEFGH":
        for sub in _SUBDIRS:
            cands.append("%s:/%s" % (drive, sub))

    # 兜底：PATH 里有 MuMuManager 就往上推两层
    mm = shutil.which("MuMuManager")
    if mm:
        cands.append(os.path.dirname(os.path.dirname(mm)))

    out, seen = [], set()
    for c in cands:
        path = os.path.abspath(c)
        key = path.lower()
        if key in seen or not os.path.isdir(path):
            continue
        if os.path.isdir(os.path.join(path, "nx_device")) or \
           os.path.isdir(os.path.join(path, "nx_main")):
            seen.add(key)
            out.append(path)
    return out


def _ver_key(path):
    m = re.search(r"nx_device[\\/](\d+(?:\.\d+)?)", path)
    return float(m.group(1)) if m else 0.0


def find_adb(cfg=None):
    """定位 adb.exe。返回路径字符串，找不到返回 ""。"""
    cfg = cfg if cfg is not None else load_config()

    # 1. 配置里写死的
    p = (cfg.get("adb") or "").strip()
    if p and _exists(os.path.expanduser(p)):
        return p

    # 2. 环境变量
    for var in ("ADB", "MUMU_ADB", "ANDROID_ADB"):
        p = (os.environ.get(var) or "").strip()
        if p and _exists(p):
            return p

    # 3. MuMu 自带的 adb（版本号最大的那个）
    hits = []
    for root in mumu_roots():
        hits += glob.glob(os.path.join(root, "nx_device", "*", "shell", "adb.exe"))
    if hits:
        return max(hits, key=_ver_key)

    # 4. PATH 里的 adb（真机 / 其它模拟器 / 官方 platform-tools）
    p = shutil.which("adb")
    if p:
        return p

    # 5. 常见 platform-tools 位置
    local = os.environ.get("LOCALAPPDATA") or ""
    for p in (r"C:\platform-tools\adb.exe",
              os.path.join(local, "Android", "Sdk", "platform-tools", "adb.exe")):
        if _exists(p):
            return p
    return ""


def find_manager(root=None):
    """定位 MuMuManager.exe（用来查实例端口）。"""
    roots = [root] if root else mumu_roots()
    for r in roots:
        p = os.path.join(r, "nx_main", "MuMuManager.exe")
        if _exists(p):
            return p
    return ""


def instances(root=None):
    """列出已配置的模拟器实例：[{index, host, port, started, android}]"""
    mgr = find_manager(root)
    if not mgr:
        return []
    out = []
    for i in range(8):
        raw = _run([mgr, "info", "-v", str(i)], timeout=15)
        start = raw.find("{")
        if start < 0:
            continue
        try:
            info = json.loads(raw[start:])
        except Exception:
            continue
        if info.get("error_code") not in (0, None):
            continue
        if not info.get("adb_port"):        # 没配置的实例没有端口，跳过
            continue
        out.append({
            "index": info.get("index", str(i)),
            "host": info.get("adb_host_ip") or "127.0.0.1",
            "port": info.get("adb_port"),
            "started": bool(info.get("is_android_started")),
            "android": info.get("android_version") or "",
        })
    return out


# ---------------------------------------------------------------- 找设备
def _devices(adb):
    """adb devices 里状态为 device 的序列号。"""
    _run([adb, "start-server"], timeout=30)
    out = _run([adb, "devices"], timeout=30)
    serials = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("*") or line.startswith("List of"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def _check(adb, serial, timeout=25):
    """确认这个序列号背后是能用的设备，返回 (可用, 有QQ阅读, 有App数据库)。"""
    model = _run([adb, "-s", serial, "shell", "getprop ro.product.model"], timeout=timeout).strip()
    if not model or "error" in model.lower() or "not found" in model.lower():
        return False, False, False
    pkgs = _run([adb, "-s", serial, "shell", "pm list packages " + APP_PKG], timeout=timeout)
    has_app = APP_PKG in pkgs
    has_db = "OK" in _run(
        [adb, "-s", serial, "shell",
         'if [ -f %s ]; then echo OK; fi' % APP_DB], timeout=timeout)
    return True, has_app, has_db


def detect_device(adb, cfg=None):
    """找出装有 QQ阅读 的那台设备，返回 serial；找不到返回 ""。

    先看已经连上的（快），再按 MuMu 实例配置的端口去连（稳）。
    """
    cfg = cfg if cfg is not None else load_config()
    manual = (cfg.get("serial") or "").strip()
    if manual:
        _run([adb, "connect", manual], timeout=15)
        ok, _, _ = _check(adb, manual)
        if ok:
            return manual

    cands = list(_devices(adb))
    for inst in instances():
        if inst.get("port"):
            cands.append("%s:%s" % (inst["host"], inst["port"]))
    # MuMu 实例端口规律：16384 起步，每个实例 +32
    cands += ["emulator-5554"] + ["127.0.0.1:%d" % (16384 + 32 * i) for i in range(6)]

    best, fallback = "", ""
    seen = set()
    for s in cands:
        if s in seen:
            continue
        seen.add(s)
        if ":" in s:
            _run([adb, "connect", s], timeout=15)
        ok, has_app, has_db = _check(adb, s)
        if not ok:
            continue
        if has_app and has_db:
            return s                 # 最理想：装了 App 且已经打开过
        if has_app and not fallback:
            fallback = s             # 装了但还没生成缓存库
        elif not best:
            best = s
    return fallback or best


def screen_size(adb, serial):
    """当前设备分辨率，返回 (宽, 高)。"""
    raw = _run([adb, "-s", serial, "shell", "wm size"], timeout=20)
    m = re.search(r"Override size:\s*(\d+)x(\d+)", raw) or \
        re.search(r"Physical size:\s*(\d+)x(\d+)", raw)
    if m:
        return int(m.group(1)), int(m.group(2))
    return BASE_W, BASE_H


def is_root(adb, serial):
    raw = _run([adb, "-s", serial, "shell", "id"], timeout=20)
    return "uid=0" in raw


def try_root(adb, serial):
    """尝试 adb root（MuMu 开了 root 开关才能成功）。"""
    _run([adb, "-s", serial, "root"], timeout=20)
    return is_root(adb, serial)


# ---------------------------------------------------------------- 自检
OK = "  [OK]  "
NO = "  [!!]  "
SK = "  [--]  "


def doctor(verbose=True):
    """把移植所需的每一样环境都检查一遍，返回 (是否全部就绪, 关键信息)。"""
    info = {"adb": "", "serial": "", "root": False, "screen": (0, 0)}
    rows, blocked = [], []

    # 1. Python
    v = "%d.%d.%d" % tuple(list(__import__("sys").version_info)[:3])
    rows.append((OK, "Python %s（只用标准库，无需 pip 安装任何东西）" % v))

    # 2. ADB
    adb = find_adb()
    info["adb"] = adb
    if adb:
        rows.append((OK, "ADB：%s" % adb))
    else:
        rows.append((NO, "找不到 adb.exe —— 先在 config.json 里填 \"adb\": \"路径\""))
        blocked.append("adb")
        return _finish(rows, blocked, info, verbose)

    # 3. 模拟器实例
    insts = instances()
    if insts:
        for i in insts:
            rows.append((OK if i["started"] else SK,
                         "模拟器实例%s  %s:%s  %s" % (
                             i["index"], i["host"], i["port"],
                             "已启动" if i["started"] else "未启动")))
        if not any(i["started"] for i in insts):
            blocked.append("模拟器没启动")
    else:
        rows.append((SK, "没找到 MuMu（如果是真机/其它模拟器，可忽略）"))

    # 4. 设备
    serial = detect_device(adb)
    info["serial"] = serial
    if not serial:
        rows.append((NO, "没连上任何设备 —— 打开 MuMu，确认设置里开了「ADB 调试」"))
        blocked.append("设备")
        return _finish(rows, blocked, info, verbose)
    model = _run([adb, "-s", serial, "shell", "getprop ro.product.model"], timeout=20).strip()
    rows.append((OK, "设备：%s（%s）" % (serial, model)))

    # 5. root
    root = is_root(adb, serial) or try_root(adb, serial)
    info["root"] = root
    if root:
        rows.append((OK, "root 权限：有（可以读 App 的缓存库）"))
    else:
        rows.append((NO, "root 权限：无 —— 模拟器设置里打开 root 开关后重启模拟器"))
        blocked.append("root")

    # 6. App + 缓存库
    ok, has_app, has_db = _check(adb, serial)
    if has_app:
        rows.append((OK, "QQ阅读：已安装"))
    else:
        rows.append((NO, "QQ阅读：未安装 —— 模拟器里装上并登录自己的账号"))
        blocked.append("App")
    if has_db:
        size = _run([adb, "-s", serial, "shell", "du -h %s" % APP_DB], timeout=20).split()[0:1]
        rows.append((OK, "榜单缓存库：存在 %s" % (size[0] if size else "")))
    else:
        rows.append((SK, "榜单缓存库：还没有（第一次采集时打开一次排行榜就会生成）"))

    # 7. 分辨率
    w, h = screen_size(adb, serial)
    info["screen"] = (w, h)
    if (w, h) == (BASE_W, BASE_H):
        rows.append((OK, "分辨率：%dx%d（与采集脚本完全一致）" % (w, h)))
    else:
        rows.append((OK, "分辨率：%dx%d（坐标自动缩放 %.2fx / %.2fx）" % (
            w, h, w / BASE_W, h / BASE_H)))

    return _finish(rows, blocked, info, verbose)


def _finish(rows, blocked, info, verbose):
    if verbose:
        print("\n环境自检")
        print("-" * 58)
        for mark, text in rows:
            print(mark + text)
        print("-" * 58)
        if blocked:
            print("还差 %d 项：%s" % (len(blocked), "、".join(blocked)))
        else:
            print("全部就绪，可以开始采集。")
    return (not blocked), info


if __name__ == "__main__":
    ok, _ = doctor()
    raise SystemExit(0 if ok else 1)
