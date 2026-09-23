"""Neo4j 五大业务查询封装。"""

from services.graph_db import get_driver


def _run(query: str, **params):
    with get_driver().session() as session:
        result = session.run(query, **params)
        return [dict(record) for record in result]


# ========== 场景 1：采购单位 → 长期合作供应商 ==========

def purchaser_suppliers(purchaser_name: str, top: int = 20):
    query = """
    MATCH (p:Purchaser {name: $name})-[:PUBLISHED]->(pr:Project)<-[:WON]-(s:Supplier)
    RETURN s.name AS supplier,
           count(DISTINCT pr) AS coop_count,
           sum(coalesce(pr.amount, 0)) AS total_amount
    ORDER BY coop_count DESC, total_amount DESC
    LIMIT $top
    """
    return _run(query, name=purchaser_name, top=top)


# ========== 场景 2：采购单位 → 高频投标主体 TOP5 ==========

def purchaser_top_bidders(purchaser_name: str, top: int = 5):
    query = """
    MATCH (p:Purchaser {name: $name})-[:PUBLISHED]->(pr:Project)
    OPTIONAL MATCH (pr)<-[:BID|WON]-(b)
    WHERE b IS NOT NULL
    RETURN b.name AS bidder,
           count(DISTINCT pr) AS freq
    ORDER BY freq DESC
    LIMIT $top
    """
    return _run(query, name=purchaser_name, top=top)


# ========== 场景 2 附加：高频协同投标组合 ==========

def purchaser_co_bid_pairs(purchaser_name: str, top: int = 5):
    query = """
    MATCH (p:Purchaser {name: $name})-[:PUBLISHED]->(pr:Project)
    MATCH (pr)<-[:BID|WON]-(a)-[r:CO_BID]-(b)
    WHERE a.name < b.name
    RETURN a.name AS bidder_a,
           b.name AS bidder_b,
           count(DISTINCT pr) AS co_count
    ORDER BY co_count DESC
    LIMIT $top
    """
    return _run(query, name=purchaser_name, top=top)


# ========== 场景 3：中标供应商 → 高频共同竞标方 ==========

def supplier_co_bidders(supplier_name: str, top: int = 5):
    query = """
    MATCH (s:Supplier {name: $name})-[r:CO_BID]-(other:Supplier)
    RETURN other.name AS co_bidder,
           sum(coalesce(r.count, 1)) AS co_count
    ORDER BY co_count DESC
    LIMIT $top
    """
    return _run(query, name=supplier_name, top=top)


# ========== 场景 3 附加：供应商历史参与项目 ==========

def supplier_projects(supplier_name: str):
    query = """
    MATCH (s:Supplier {name: $name})-[:WON|BID]->(pr:Project)
    OPTIONAL MATCH (p:Purchaser)-[:PUBLISHED]->(pr)
    RETURN DISTINCT pr.name AS project_name,
           pr.code AS project_code,
           pr.amount AS project_amount,
           p.name AS purchaser
    ORDER BY pr.amount DESC
    """
    return _run(query, name=supplier_name)


# ========== 场景 4：多供应商 → 共同合作采购单位 ==========

def common_purchasers(supplier_names: list[str]):
    query = """
    MATCH (s:Supplier)-[:WON]->(pr:Project)<-[:PUBLISHED]-(p:Purchaser)
    WHERE s.name IN $names
    WITH p, collect(DISTINCT s.name) AS matched,
         count(DISTINCT pr) AS coop_count,
         sum(coalesce(pr.amount, 0)) AS total_amount
    WHERE size(matched) = size($names)
    RETURN p.name AS purchaser,
           matched AS suppliers,
           coop_count,
           total_amount
    ORDER BY coop_count DESC, total_amount DESC
    """
    return _run(query, names=supplier_names)


# ========== 场景 5：多供应商 → 共同竞标项目 ==========

def common_projects(supplier_names: list[str]):
    query = """
    MATCH (s)-[:WON|BID]->(pr:Project)
    WHERE s.name IN $names
    WITH pr, collect(DISTINCT s.name) AS matched
    WHERE size(matched) = size($names)
    RETURN pr.name AS project_name,
           pr.code AS project_code,
           pr.amount AS project_amount,
           matched AS suppliers
    ORDER BY pr.amount DESC
    """
    return _run(query, names=supplier_names)


# ========== 附加：图谱可视化数据 ==========

def graph_overview(limit: int = 200):
    query = """
    MATCH (a)-[r]->(b)
    RETURN a.name AS source, labels(a)[0] AS source_type,
           type(r) AS relation,
           b.name AS target, labels(b)[0] AS target_type
    LIMIT $limit
    """
    return _run(query, limit=limit)