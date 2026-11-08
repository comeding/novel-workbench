"""书籍详情。"""
from ..router import Router, json_response
from ..services import query

router = Router()


@router.get(r"/api/books/(?P<bid>[^/]+)")
def detail(req):
    b = query.book_detail(req.params["bid"])
    if not b:
        return json_response({"ok": False, "error": "没有这本书"}, 404)
    return b
