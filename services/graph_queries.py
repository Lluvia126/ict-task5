"""Neo4j 业务查询封装（任务二五大场景 + 图谱可视化）。

**范围过滤**：所有查询都只看"当前有效公告"（SQLite 里已入库的抽取结果）贡献的关系。
关系上用 `announcement_ids` 记录来源公告，历史演示数据没有该属性 → 永远不会被查出来，
因此不会再出现"抽取结果已清空、图谱还显示旧项目"的情况。
"""

from neo4j.exceptions import Neo4jError

from services.graph_db import get_driver
from services.storage import list_announcement_ids


class GraphUnavailable(RuntimeError):
    """Neo4j 不可用（连接失败/查询报错），由接口层转 503。"""


# 关系是否属于"当前有效公告范围"
SCOPE = "any(x IN coalesce(%s.announcement_ids, []) WHERE x IN $ids)"


def current_ids() -> list:
    """当前有效公告编号（范围过滤的唯一依据，直接读 SQLite）。"""
    return list_announcement_ids()


def _run(query: str, **params):
    if "ids" not in params:
        params["ids"] = current_ids()
    try:
        with get_driver().session() as session:
            return [dict(record) for record in session.run(query, **params)]
    except Neo4jError as exc:
        raise GraphUnavailable(str(exc)) from exc
    except Exception as exc:  # 连接层异常（ServiceUnavailable 等）
        raise GraphUnavailable("%s: %s" % (type(exc).__name__, exc)) from exc


# ========== 场景 1：采购单位 → 长期合作供应商 ==========

def purchaser_suppliers(purchaser_name: str, top: int = 20):
    query = """
    MATCH (p:Purchaser {name: $name})-[pub:PUBLISHED]->(pr:Project)<-[won:WON]-(s:Supplier)
    WHERE %s AND %s
    RETURN s.name AS supplier,
           count(DISTINCT pr) AS coop_count,
           sum(coalesce(pr.amount, 0)) AS total_amount
    ORDER BY coop_count DESC, total_amount DESC
    LIMIT $top
    """ % (SCOPE % "pub", SCOPE % "won")
    return _run(query, name=purchaser_name, top=top)


# ========== 场景 2：采购单位 → 高频投标主体 TOP5 ==========

def purchaser_top_bidders(purchaser_name: str, top: int = 5):
    query = """
    MATCH (p:Purchaser {name: $name})-[pub:PUBLISHED]->(pr:Project)
    WHERE %s
    MATCH (pr)<-[rel:BID|WON]-(b)
    WHERE %s
    RETURN b.name AS bidder,
           count(DISTINCT pr) AS freq
    ORDER BY freq DESC
    LIMIT $top
    """ % (SCOPE % "pub", SCOPE % "rel")
    return _run(query, name=purchaser_name, top=top)


# ========== 场景 2 附加：高频协同投标组合 ==========

def purchaser_co_bid_pairs(purchaser_name: str, top: int = 5):
    query = """
    MATCH (p:Purchaser {name: $name})-[pub:PUBLISHED]->(pr:Project)
    WHERE %s
    MATCH (pr)<-[rel:BID|WON]-(a)-[cab:CO_BID]-(b)
    WHERE a.name < b.name AND %s AND %s
    RETURN a.name AS bidder_a,
           b.name AS bidder_b,
           count(DISTINCT pr) AS co_count
    ORDER BY co_count DESC
    LIMIT $top
    """ % (SCOPE % "pub", SCOPE % "rel", SCOPE % "cab")
    return _run(query, name=purchaser_name, top=top)


# ========== 场景 3：中标供应商 → 高频共同竞标方 ==========

def supplier_co_bidders(supplier_name: str, top: int = 5):
    query = """
    MATCH (s:Supplier {name: $name})-[cab:CO_BID]-(other:Supplier)
    WITH other.name AS co_bidder,
         size([x IN coalesce(cab.announcement_ids, []) WHERE x IN $ids]) AS c
    WHERE c > 0
    RETURN co_bidder,
           sum(c) AS co_count
    ORDER BY co_count DESC
    LIMIT $top
    """
    return _run(query, name=supplier_name, top=top)


# ========== 场景 3 附加：供应商历史参与项目 ==========

def supplier_projects(supplier_name: str):
    query = """
    MATCH (s:Supplier {name: $name})-[rel:WON|BID]->(pr:Project)
    WHERE %s
    OPTIONAL MATCH (p:Purchaser)-[pub:PUBLISHED]->(pr)
    WHERE %s
    RETURN DISTINCT pr.name AS project_name,
           pr.code AS project_code,
           pr.amount AS project_amount,
           p.name AS purchaser
    ORDER BY pr.amount DESC
    """ % (SCOPE % "rel", SCOPE % "pub")
    return _run(query, name=supplier_name)


# ========== 场景 4：多供应商 → 共同合作采购单位 ==========

def common_purchasers(supplier_names: list):
    query = """
    MATCH (s:Supplier)-[won:WON]->(pr:Project)<-[pub:PUBLISHED]-(p:Purchaser)
    WHERE s.name IN $names AND %s AND %s
    WITH p, collect(DISTINCT s.name) AS matched,
         count(DISTINCT pr) AS coop_count,
         sum(coalesce(pr.amount, 0)) AS total_amount
    WHERE size(matched) = size($names)
    RETURN p.name AS purchaser,
           matched AS suppliers,
           coop_count,
           total_amount
    ORDER BY coop_count DESC, total_amount DESC
    """ % (SCOPE % "won", SCOPE % "pub")
    return _run(query, names=supplier_names)


# ========== 场景 5：多供应商 → 共同竞标项目 ==========

def common_projects(supplier_names: list):
    query = """
    MATCH (s)-[rel:WON|BID]->(pr:Project)
    WHERE s.name IN $names AND %s
    WITH pr, collect(DISTINCT s.name) AS matched
    WHERE size(matched) = size($names)
    RETURN pr.name AS project_name,
           pr.code AS project_code,
           pr.amount AS project_amount,
           matched AS suppliers
    ORDER BY pr.amount DESC
    """ % (SCOPE % "rel")
    return _run(query, names=supplier_names)


# ========== 附加：图谱可视化数据（仅当前有效公告范围） ==========

def graph_overview(limit: int = 200):
    query = """
    MATCH (a)-[r]->(b)
    WHERE %s
    RETURN a.name AS source, labels(a)[0] AS source_type,
           type(r) AS relation,
           b.name AS target, labels(b)[0] AS target_type
    LIMIT $limit
    """ % (SCOPE % "r")
    return _run(query, limit=limit)