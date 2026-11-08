"""采集记录。"""
from ..router import Router
from ..services import query

router = Router()


@router.get(r"/api/runs")
def runs(req):
    items = query.run_list(limit=req.arg_int("limit", 50))
    return {"ok": True, "runs": items}
