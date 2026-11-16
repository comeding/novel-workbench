# -*- coding: utf-8 -*-
"""重建番茄小说的字体映射表（collector/fanqie_font.json）。

番茄榜单里的书名/作者掺了私有区字符（U+E000~），网页靠一个
SourceHanSansSC-Normal 的子集字体把它们画成汉字。子集字体里的字形名是
`gid<原字体 glyph id>`，但版本一换 glyph 顺序就错位，所以这里不按 id 猜，
而是**比字形轮廓**：把子集字体每个字形的轮廓和思源黑体原字体里的字形比对，
轮廓完全一致的那个就是真字。

平时不用跑，只有番茄换了混淆字体（脚本会打印 `! 混淆字体换了`）时才需要：

    python -m pip install fonttools brotli
    python dev/make_fanqie_font.py                 # 榜单页 → fanqie_font.json
    python dev/make_fanqie_font.py --lib           # 书库页 → fanqie_font_lib.json

番茄的每个页面各用一套子集字体，互不通用，所以要分别生成。已固化的两套：

- 榜单页（/rank/1_1_262，hash dc027189e0ba4cd）→ collector/fanqie_font.json
- 书库页（/library/...，hash e26e946d8b2ccb7）→ collector/fanqie_font_lib.json

会用到的两个外部文件：
- 当前混淆字体：从页面拿 hash，下载 awesome-font/c/<hash>.otf（几十 KB）
- 思源黑体 2.002：从 GitHub 拉，16MB，缓存在 dev/.cache/ 下，只下一次
"""
import io
import json
import os
import re
import sys
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE, "dev", ".cache")
SRC_FONT = os.path.join(CACHE, "SourceHanSansSC-Normal-2.002.otf")
SRC_URL = ("https://cdn.jsdelivr.net/gh/adobe-fonts/source-han-sans@2.002R/"
           "OTF/SimplifiedChinese/SourceHanSansSC-Normal.otf")
FONT_HOST = "https://lf6-awef.bytetos.com/obj/awesome-font/c/%s.otf"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 要生成哪一套：页面地址 + 输出文件
TARGETS = {
    "rank": {
        "page": "https://fanqienovel.com/rank/1_1_262",
        "out": os.path.join(BASE, "collector", "fanqie_font.json"),
        "note": "番茄榜单页的书名/作者用私有区字符混淆（SourceHanSansSC-Normal 子集）。",
    },
    "lib": {
        # 书库页是纯客户端渲染，HTML 里没有字体链接；字体 key 在接口响应头
        # x-tt-zhal 里（`f=<hash>`），所以这里改成先打一次接口拿头
        "probe": ("https://fanqienovel.com/api/author/library/book_list/v0/"
                  "?page_count=18&page_index=0&gender=1&category_id=-1"
                  "&creation_status=1&word_count=0&book_type=-1&sort=0"),
        "out": os.path.join(BASE, "collector", "fanqie_font_lib.json"),
        "note": "番茄书库页（新书总榜）的书名/作者/字数/在读用私有区字符混淆，"
                "和榜单页不是同一套字体。",
    },
}


def log(msg):
    print(msg, flush=True)


def fetch(url, path=None, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read()
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        log("  已下载 %s（%.1f MB）" % (os.path.basename(path), len(data) / 1048576.0))
    return data if binary else data.decode("utf-8", "replace")


def sigs(font):
    """glyph 名 → 轮廓签名（把绘制指令和坐标拼成一个可比较的元组）。"""
    from fontTools.pens.recordingPen import RecordingPen
    gs = font.getGlyphSet()
    out = {}
    for name in font.getGlyphOrder():
        pen = RecordingPen()
        try:
            gs[name].draw(pen)
        except Exception:                      # noqa: BLE001
            continue
        out[name] = tuple((op, tuple(round(v, 1) for pt in args for v in pt))
                          for op, args in pen.value)
    return out


def pick(unis):
    """一个字形可能挂着好几个码点（康熙部首和汉字共用字形），按常用区优先挑。"""
    for lo, hi in ((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF),
                   (0x3000, 0x303F), (0xFF00, 0xFFEF)):
        hit = [u for u in unis if lo <= u <= hi]
        if hit:
            return chr(min(hit))
    return chr(min(unis))


def main(argv):
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        log("缺少 fontTools：python -m pip install fonttools brotli")
        return 1

    which = "lib" if "--lib" in argv else "rank"
    tgt = TARGETS[which]
    out = tgt["out"]
    log("目标：%s" % which)

    fhash = ""
    if "page" in tgt:
        html = fetch(tgt["page"])
        m = re.search(r"awesome-font/c/([0-9a-f]+)\.woff2", html)
        if not m:
            log("页面上没找到混淆字体，番茄可能改了实现")
            return 1
        fhash = m.group(1)
    else:
        # 客户端渲染的页面只能从接口响应头拿：x-tt-zhal: k=..;f=<hash>;d1=..
        req = urllib.request.Request(tgt["probe"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            zhal = r.headers.get("x-tt-zhal") or ""
        m = re.search(r"(?:^|;)f=([0-9a-f]+)", zhal)
        if not m:
            log("响应头里没找到字体 key（x-tt-zhal=%r）" % zhal)
            return 1
        fhash = m.group(1)
    log("当前混淆字体 %s" % fhash)

    sub_path = os.path.join(CACHE, "fanqie-%s.otf" % fhash)
    if os.path.exists(sub_path):
        log("  用缓存 %s" % os.path.basename(sub_path))
    else:
        fetch(FONT_HOST % fhash, sub_path)
    if not os.path.exists(SRC_FONT):
        log("下载思源黑体 2.002（约 16MB，只需要一次）…")
        fetch(SRC_URL, SRC_FONT)

    sub, src = TTFont(sub_path), TTFont(SRC_FONT)
    log("比对字形轮廓…")
    ssig, osig = sigs(sub), sigs(src)
    idx = {}
    for name, sig in osig.items():
        idx.setdefault(sig, name)
    rev = {}
    for uni, name in src.getBestCmap().items():
        rev.setdefault(name, []).append(uni)

    mp, miss = {}, []
    for cp, name in sub.getBestCmap().items():
        src_name = idx.get(ssig.get(name))
        unis = rev.get(src_name) or []
        if unis:
            mp[chr(cp)] = pick(unis)
        else:
            miss.append(hex(cp))

    log("还原 %d / %d 个字形，未匹配 %d 个" % (len(mp), len(sub.getBestCmap()), len(miss)))
    if miss:
        log("  未匹配：%s" % "、".join(miss[:10]))
    if len(mp) < len(sub.getBestCmap()) * 0.9:
        log("匹配率过低，原字体版本可能不是 2.002，先别覆盖映射表")
        return 1

    payload = {
        "font": fhash,
        "note": tgt["note"] + "这里是按字形反查出来的映射；字体 hash 变了就重新生成。",
        "map": mp,
    }
    with io.open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=0)
    log("已写入 %s（%s 条）" % (out, len(mp)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
