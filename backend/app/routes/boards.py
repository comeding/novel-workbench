"""榜单相关接口。"""
from ..router import Router
from ..services import query

router = Router()


@router.get(r"/api/boards")
def list_boards(req):
    return {"ok": True, "boards": query.board_list()}


@router.get(r"/api/dataset")
def dataset(req):
    """整包数据，结构和旧版 data.js 一致，前端主页面用它。"""
    return query.dataset()


@router.get(r"/api/boards/(?P<key>[^/]+)/cats")
def cats(req):
    return {"ok": True, "key": req.params["key"], "cats": query.board_cats(req.params["key"])}


@router.get(r"/api/boards/(?P<key>[^/]+)/books")
def books(req):
    return query.board_books(
        req.params["key"],
        cat=req.arg("cat", "全部"),
        q=(req.arg("q") or "").strip(),
        sort=req.arg("sort", "rank"),
        page=req.arg_int("page", 1),
        size=req.arg_int("size", 50),
    )
