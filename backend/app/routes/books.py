"""书籍详情。"""
from ..router import Router, json_response
from ..services import query

router = Router()


@router.get(r"/api/bids")
def bids(req):
    """在榜书目 bid 列表，给采集器用（板 key：new / vip / all）。

    故意做成轻量接口：采集器不用连库，也就不会和容器里的服务抢 SQLite 锁。
    """
    board = (req.arg("board") or "new").strip().lower()
    out = query.onboard_bids(board)
    return {"ok": True, "board": board, "count": len(out), "bids": out,
            "last_snapshot_at": query.shelf_last_at()}


@router.get(r"/api/books/(?P<bid>[^/]+)")
def detail(req):
    b = query.book_detail(req.params["bid"])
    if not b:
        return json_response({"ok": False, "error": "没有这本书"}, 404)
    return b
