#!/usr/bin/env python
"""后端服务启动入口。

    python backend/server.py
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from app.main import main  # noqa: E402


def _stdio():
    """用 pythonw（无控制台）启动时，sys.stdout / sys.stderr 是 None。

    这时候任何一条 print 都会抛 AttributeError，进程连启动信息都打不出来
    就直接退出了，表现是“双击没反应”。这里把它们接到 logs/server.log，
    顺便留下排查线索。正常 python 启动时不会走到这里，行为完全不变。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    log = os.path.join(os.path.dirname(BASE), "logs", "server.log")
    try:
        os.makedirs(os.path.dirname(log), exist_ok=True)
        f = open(log, "a", encoding="utf-8", buffering=1)
    except OSError:
        f = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f


if __name__ == "__main__":
    _stdio()
    raise SystemExit(main())
