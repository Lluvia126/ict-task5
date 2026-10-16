"""任务二：五大业务查询 API。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List

from services import graph_queries as gq
from services import graph_sync
from services.security import protect

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def _check_empty(rows, msg):
    if not rows:
        raise HTTPException(status_code=404, detail=msg)


@router.get("/purchaser/{name}/suppliers")
def api_purchaser_suppliers(name: str, top: int = 20):
    """场景1：采购单位的长期合作供应商。"""
    rows = gq.purchaser_suppliers(name, top=top)
    return {"purchaser": name, "count": len(rows), "items": rows}


@router.get("/purchaser/{name}/top-bidders")
def api_purchaser_top_bidders(name: str, top: int = 5):
    """场景2：采购单位高频投标主体 TOP5。"""
    rows = gq.purchaser_top_bidders(name, top=top)
    return {"purchaser": name, "count": len(rows), "items": rows}


@router.get("/purchaser/{name}/co-bid-pairs")
def api_purchaser_co_bid_pairs(name: str, top: int = 5):
    """场景2附加：采购单位内高频协同投标组合。"""
    rows = gq.purchaser_co_bid_pairs(name, top=top)
    return {"purchaser": name, "count": len(rows), "items": rows}


@router.get("/supplier/{name}/co-bidders")
def api_supplier_co_bidders(name: str, top: int = 5):
    """场景3：中标供应商的高频共同竞标方。"""
    rows = gq.supplier_co_bidders(name, top=top)
    return {"supplier": name, "count": len(rows), "items": rows}


@router.get("/supplier/{name}/projects")
def api_supplier_projects(name: str):
    """场景3附加：供应商历史项目。"""
    rows = gq.supplier_projects(name)
    return {"supplier": name, "count": len(rows), "items": rows}


@router.get("/suppliers/common-purchasers")
def api_common_purchasers(names: List[str] = Query(..., description="供应商名称列表")):
    """场景4：多个供应商共同合作的采购单位。"""
    if len(names) < 2:
        raise HTTPException(status_code=400, detail="至少需要 2 个供应商")
    rows = gq.common_purchasers(names)
    return {"suppliers": names, "count": len(rows), "items": rows}


@router.get("/suppliers/common-projects")
def api_common_projects(names: List[str] = Query(..., description="供应商名称列表")):
    """场景5：多个供应商共同竞标的项目。"""
    if len(names) < 2:
        raise HTTPException(status_code=400, detail="至少需要 2 个供应商")
    rows = gq.common_projects(names)
    return {"suppliers": names, "count": len(rows), "items": rows}


@router.get("/graph/status")
def api_graph_status():
    """图谱是否与当前抽取数据一致：in_sync / missing_in_graph / stale_in_graph / 上次同步时间。"""
    return graph_sync.status()


@router.post("/graph/sync", dependencies=[Depends(protect("graph-sync", allow_anon=False))])
def api_graph_sync(
    mode: str = Query("incremental", description="incremental=增量补差；rebuild=清空图谱后按抽取结果重建"),
):
    """手动同步图谱（抽取/删除时会自动同步，这里用于修复不一致或清除历史演示数据）。"""
    if mode not in ("incremental", "rebuild"):
        raise HTTPException(status_code=400, detail="不支持的 mode=%s，可选 incremental/rebuild" % mode)
    if mode == "rebuild":
        return graph_sync.rebuild_all()
    return graph_sync.sync_all()


@router.get("/graph/overview")
def api_graph_overview(limit: int = 200):
    """图谱可视化数据。"""
    rows = gq.graph_overview(limit=limit)
    return {"count": len(rows), "items": rows}