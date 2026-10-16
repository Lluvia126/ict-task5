"""Neo4j 图数据库访问层。

图谱是 SQLite 抽取结果的**投影**：只允许通过本模块写入，并且每条关系都带
`announcement_ids`（来源公告编号列表），用于：
1. 按"当前有效公告范围"查询（历史/演示数据没有该属性，永远不会被查出来）；
2. 删除公告时精确摘掉这条记录贡献的关系，而不是清库；
3. 判断图谱是否与 SQLite 一致（见 services/graph_sync.py）。

历史遗留数据说明：早期由 scripts/import_to_neo4j.py 手工批量导入的关系没有
announcement_ids，属于"无来源关系"，所有查询都会忽略它；执行全量重建
（clear_graph + 重新导入）可以彻底清除。
"""

from __future__ import annotations

import itertools

from neo4j import GraphDatabase

from services.config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER

_driver = None

NODE_LABELS = ("Project", "Purchaser", "Supplier", "Bidder", "Product")

# 关系成员累加（幂等：重复导入同一条公告不会重复计数）
_SET_MEMBER = """
    SET r.announcement_ids = CASE
            WHEN $aid IN coalesce(r.announcement_ids, []) THEN r.announcement_ids
            ELSE coalesce(r.announcement_ids, []) + $aid END
"""


def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return _driver


def close_driver():
    global _driver
    if _driver:
        _driver.close()
        _driver = None


def check_connection() -> dict:
    """连接自检，供 /graph/status 使用（不抛异常）。"""
    try:
        get_driver().verify_connectivity()
        return {"connected": True, "uri": NEO4J_URI, "user": NEO4J_USER}
    except Exception as exc:
        return {
            "connected": False,
            "uri": NEO4J_URI,
            "user": NEO4J_USER,
            "error": "%s: %s" % (type(exc).__name__, exc),
        }


def init_constraints():
    """建立唯一约束，避免重复节点。"""
    queries = [
        "CREATE CONSTRAINT IF NOT EXISTS FOR (p:Purchaser) REQUIRE p.name IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (s:Supplier) REQUIRE s.name IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (b:Bidder) REQUIRE b.name IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (pr:Project) REQUIRE pr.name IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (pd:Product) REQUIRE pd.name IS UNIQUE",
    ]
    with get_driver().session() as session:
        for query in queries:
            session.run(query)


# ========== 写入：单条抽取结果 ==========

def import_extraction(data: dict) -> dict:
    """把一条抽取结果写入图谱（幂等，可重复调用）。"""
    aid = str((data or {}).get("announcement_id") or "").strip()
    if not aid:
        raise ValueError("抽取结果缺少 announcement_id，无法同步图谱")

    project_name = str(data.get("project_name") or "").strip()
    purchaser = str(data.get("purchaser") or "").strip()
    project_code = data.get("project_code") or ""
    project_amount = data.get("project_amount")
    stats = {"relationships": 0, "products": 0}

    with get_driver().session() as session:
        if project_name:
            session.run(
                """
                MERGE (pr:Project {name: $project_name})
                SET pr.code = $project_code, pr.amount = $project_amount
                """,
                project_name=project_name,
                project_code=project_code,
                project_amount=project_amount,
            )

        if purchaser and project_name:
            session.run(
                """
                MERGE (p:Purchaser {name: $purchaser})
                MERGE (pr:Project {name: $project_name})
                MERGE (p)-[r:PUBLISHED]->(pr)
                """
                + _SET_MEMBER,
                purchaser=purchaser,
                project_name=project_name,
                aid=aid,
            )
            stats["relationships"] += 1

        # 中标供应商
        for supplier in _unique(data.get("winning_suppliers")):
            if not project_name:
                break
            session.run(
                """
                MERGE (s:Supplier {name: $supplier})
                MERGE (pr:Project {name: $project_name})
                MERGE (s)-[r:WON]->(pr)
                """
                + _SET_MEMBER,
                supplier=supplier,
                project_name=project_name,
                aid=aid,
            )
            stats["relationships"] += 1

        # 投标参与方
        for bidder in _unique(data.get("bid_participants")):
            if not project_name:
                break
            session.run(
                """
                MERGE (b:Bidder {name: $bidder})
                MERGE (pr:Project {name: $project_name})
                MERGE (b)-[r:BID]->(pr)
                """
                + _SET_MEMBER,
                bidder=bidder,
                project_name=project_name,
                aid=aid,
            )
            stats["relationships"] += 1

        # 标的
        for product in data.get("products") or []:
            if not isinstance(product, dict) or not project_name:
                continue
            pname = str(product.get("product_service_name") or "").strip()
            if not pname:
                continue
            session.run(
                """
                MERGE (pd:Product {name: $pname})
                SET pd.category = $category,
                    pd.brand = $brand,
                    pd.spec_model = $spec_model
                WITH pd
                MERGE (pr:Project {name: $project_name})
                MERGE (pr)-[r:CONTAINS]->(pd)
                """
                + _SET_MEMBER,
                pname=pname,
                category=product.get("category") or "",
                brand=product.get("brand") or "",
                spec_model=product.get("spec_model") or "",
                project_name=project_name,
                aid=aid,
            )
            stats["products"] += 1
            stats["relationships"] += 1

        # 协同投标：同一公告内的两两组合（按名称排序，保证方向唯一）
        bidders = sorted(set(_unique(data.get("winning_suppliers")) + _unique(data.get("bid_participants"))))
        for a, b in itertools.combinations(bidders, 2):
            session.run(
                """
                MERGE (x:Supplier {name: $a})
                MERGE (y:Supplier {name: $b})
                MERGE (x)-[r:CO_BID]->(y)
                """
                + _SET_MEMBER
                + """
                SET r.count = size(coalesce(r.announcement_ids, []))
                """,
                a=a,
                b=b,
                aid=aid,
            )
            stats["relationships"] += 1

    return stats


def _unique(values) -> list:
    out = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


# ========== 删除：按公告编号精确摘除 ==========

def remove_extractions(announcement_ids) -> dict:
    """摘掉这些公告贡献的图关系；关系没有其他来源时删除，并清理孤立节点。"""
    ids = _unique(announcement_ids)
    if not ids:
        return {"removed_relationships": 0, "removed_nodes": 0}

    with get_driver().session() as session:
        session.run(
            """
            MATCH ()-[r]->()
            WHERE any(x IN coalesce(r.announcement_ids, []) WHERE x IN $ids)
            SET r.announcement_ids = [x IN r.announcement_ids WHERE NOT x IN $ids]
            """,
            ids=ids,
        )
        removed_rels = session.run(
            """
            MATCH ()-[r]->()
            WHERE r.announcement_ids IS NOT NULL AND size(r.announcement_ids) = 0
            DELETE r
            RETURN count(*) AS c
            """
        ).single()["c"]
        session.run(
            """
            MATCH ()-[r:CO_BID]->()
            WHERE r.announcement_ids IS NOT NULL
            SET r.count = size(r.announcement_ids)
            """
        )
        removed_nodes = session.run(
            """
            MATCH (n)
            WHERE NOT (n)--() AND any(l IN labels(n) WHERE l IN $labels)
            DELETE n
            RETURN count(*) AS c
            """,
            labels=list(NODE_LABELS),
        ).single()["c"]

    return {"removed_relationships": removed_rels, "removed_nodes": removed_nodes}


def remove_extraction(announcement_id: str) -> dict:
    return remove_extractions([announcement_id])


def clear_graph() -> dict:
    """清空图谱（仅用于全量重建，会一并清除历史演示数据）。"""
    with get_driver().session() as session:
        removed = session.run(
            "MATCH (n) DETACH DELETE n RETURN count(*) AS c"
        ).single()["c"]
    return {"removed_nodes": removed}


# ========== 统计 ==========

def graph_stats() -> dict:
    """图谱现状：节点/关系总数、带来源的关系数、无来源（历史）关系数、已入库公告编号。"""
    with get_driver().session() as session:
        nodes = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        rels = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        scoped = session.run(
            """
            MATCH ()-[r]->()
            WHERE r.announcement_ids IS NOT NULL AND size(r.announcement_ids) > 0
            RETURN count(r) AS c
            """
        ).single()["c"]
        ids = [
            row["aid"]
            for row in session.run(
                """
                MATCH ()-[r]->()
                WHERE r.announcement_ids IS NOT NULL
                UNWIND r.announcement_ids AS aid
                RETURN DISTINCT aid
                """
            )
        ]
    return {
        "nodes": nodes,
        "relationships": rels,
        "scoped_relationships": scoped,
        "legacy_relationships": rels - scoped,
        "announcement_ids": sorted(ids),
    }