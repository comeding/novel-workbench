"""MuMu 模拟器 ADB 封装：连接、UI dump、点击、滑动、截图。

两条移植性设计：
1. adb 路径和设备序列号由 env.py 自动探测（可用 config.json 覆盖）。
2. 所有对外坐标都按「基准分辨率」1080x1920 表达，内部自动换算成真机像素，
   所以模拟器分辨率不同也不用改采集脚本。
"""
import os
import re
import subprocess
import time

import env


class UIXml(str):
    """带缩放系数的 UI XML。parse_nodes 读到它就把坐标换算回基准空间。"""
    sx = 1.0
    sy = 1.0


class Device:
    def __init__(self, adb=None, serial=None):
        cfg = env.load_config()
        self.adb = adb or cfg.get("adb") or env.find_adb(cfg)
        if not self.adb:
            raise RuntimeError(
                "没找到 adb.exe。请在 config.json 里填 {\"adb\": \"adb.exe 的完整路径\"}，"
                "或把 MuMu 的 shell 目录加进 PATH。")
        self.serial = serial or cfg.get("serial") or env.detect_device(self.adb, cfg)
        if not self.serial:
            raise RuntimeError("没找到可用设备。请先启动模拟器，并确认已开启 ADB 调试。")
        self._ensure()
        self._setup_scale(cfg)

    # ---------- 连接与分辨率 ----------
    def _ensure(self):
        subprocess.run([self.adb, "connect", self.serial],
                       capture_output=True, text=True, timeout=30)

    def _setup_scale(self, cfg):
        base = cfg.get("base_size") or [env.BASE_W, env.BASE_H]
        try:
            base_w, base_h = int(base[0]), int(base[1])
        except Exception:
            base_w, base_h = env.BASE_W, env.BASE_H
        w, h = env.screen_size(self.adb, self.serial)
        self.screen = (w, h)
        self.sx = w / float(base_w)
        self.sy = h / float(base_h)

    # ---------- 底层 ----------
    def sh(self, *args, timeout=60):
        r = subprocess.run([self.adb, "-s", self.serial] + list(args),
                           capture_output=True, timeout=timeout)
        return r.stdout.decode("utf-8", "replace")

    def shell(self, cmd, timeout=60):
        return self.sh("shell", cmd, timeout=timeout)

    def tap(self, x, y):
        """x/y 用基准分辨率坐标。"""
        self.shell("input tap %d %d" % (round(x * self.sx), round(y * self.sy)))

    def swipe(self, x1, y1, x2, y2, ms=300):
        self.shell("input swipe %d %d %d %d %d" % (
            round(x1 * self.sx), round(y1 * self.sy),
            round(x2 * self.sx), round(y2 * self.sy), ms))
        time.sleep(0.35)

    def back(self):
        self.shell("input keyevent 4")
        time.sleep(1)

    def dump(self, retries=3):
        """返回当前界面的 UI XML 文本（UIXml，带缩放系数）。"""
        for _ in range(retries):
            out = self.shell("uiautomator dump /sdcard/window_dump.xml", timeout=60)
            if "dumped to" in out or "UI hierchary" in out:
                xml = UIXml(self.sh("shell", "cat /sdcard/window_dump.xml", timeout=60))
                xml.sx, xml.sy = self.sx, self.sy
                return xml
            time.sleep(0.5)
        return UIXml("")

    def screenshot(self, path):
        data = subprocess.run([self.adb, "-s", self.serial, "exec-out", "screencap", "-p"],
                              capture_output=True, timeout=60).stdout
        with open(path, "wb") as f:
            f.write(data)
        return path


NODE_RE = re.compile(r"<node [^>]*?/?>")
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')


def parse_nodes(xml):
    """把 UI XML 解析成 [{text, desc, bounds, center, clickable}]。

    若 xml 是 UIXml（带缩放），bounds/center 会换算回 1080x1920 基准坐标。
    """
    sx = getattr(xml, "sx", 1.0) or 1.0
    sy = getattr(xml, "sy", 1.0) or 1.0
    out = []
    for m in NODE_RE.finditer(xml):
        attrs = dict(ATTR_RE.findall(m.group(0)))
        b = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", attrs.get("bounds", ""))
        if not b:
            continue
        x1, y1, x2, y2 = map(int, b.groups())
        out.append({
            "text": unescape(attrs.get("text", "")),
            "desc": unescape(attrs.get("content-desc", "")),
            "bounds": (round(x1 / sx), round(y1 / sy), round(x2 / sx), round(y2 / sy)),
            "center": (round((x1 + x2) / 2 / sx), round((y1 + y2) / 2 / sy)),
            "clickable": attrs.get("clickable") == "true",
        })
    return out


def unescape(s):
    if not s:
        return ""
    return (s.replace("&#10;", "\n").replace("&amp;", "&")
             .replace("&#39;", "'").replace("&quot;", '"')
             .replace("\u200b", "").replace("\u200c", "").strip())


def find(xml, keyword, exact=False):
    """在界面里找含关键字（desc 或 text）的节点，返回中心坐标（基准空间）。"""
    for n in parse_nodes(xml):
        label = n["desc"] or n["text"]
        if not label:
            continue
        if (exact and label == keyword) or (not exact and keyword in label):
            return n["center"]
    return None
