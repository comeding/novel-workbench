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

if __name__ == "__main__":
    raise SystemExit(main())
