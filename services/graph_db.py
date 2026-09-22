from neo4j import GraphDatabase
from services.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

_driver = None


def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            NEO4J_URI,
            auth=(NEO4J_USER, NEO4J_PASSWORD),
        )
    return _driver


def close_driver():
    global _driver
    if _driver:
        _driver.close()
        _driver = None


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
        for q in queries:
            session.run(q)


def import_extraction(data: dict):
    """把一条抽取结果导入 Neo4j。"""
    with get_driver().session() as session:
        purchaser = data.get("purchaser") or ""
        project_name = data.get("project_name") or ""
        project_code = data.get("project_code") or ""
        project_amount = data.get("project_amount")

        if purchaser and project_name:
            session.run("""
                MERGE (p:Purchaser {name: $purchaser})
                MERGE (pr:Project {name: $project_name})
                SET pr.code = $project_code, pr.amount = $project_amount
                MERGE (p)-[:PUBLISHED]->(pr)
            """, purchaser=purchaser, project_name=project_name,
                 project_code=project_code, project_amount=project_amount)

        for supplier in data.get("winning_suppliers", []) or []:
            if not supplier or not project_name:
                continue
            session.run("""
                MERGE (s:Supplier {name: $supplier})
                MERGE (pr:Project {name: $project_name})
                MERGE (s)-[:WON]->(pr)
            """, supplier=supplier, project_name=project_name)

        for bidder in data.get("bid_participants", []) or []:
            if not bidder or not project_name:
                continue
            session.run("""
                MERGE (b:Bidder {name: $bidder})
                MERGE (pr:Project {name: $project_name})
                MERGE (b)-[:BID]->(pr)
            """, bidder=bidder, project_name=project_name)

        for product in data.get("products", []) or []:
            pname = product.get("product_service_name") or ""
            if not pname:
                continue
            session.run("""
                MERGE (pd:Product {name: $pname})
                SET pd.category = $category,
                    pd.brand = $brand,
                    pd.spec_model = $spec_model
                WITH pd
                OPTIONAL MATCH (pr:Project {name: $project_name})
                FOREACH (_ IN CASE WHEN pr IS NULL THEN [] ELSE [1] END |
                    MERGE (pr)-[:CONTAINS]->(pd)
                )
            """, pname=pname,
                 category=product.get("category", ""),
                 brand=product.get("brand", ""),
                 spec_model=product.get("spec_model", ""),
                 project_name=project_name)

        all_bidders = list(set(
            (data.get("winning_suppliers") or []) +
            (data.get("bid_participants") or [])
        ))
        for i in range(len(all_bidders)):
            for j in range(i + 1, len(all_bidders)):
                a, b = all_bidders[i], all_bidders[j]
                session.run("""
                    MERGE (x:Supplier {name: $a})
                    MERGE (y:Supplier {name: $b})
                    MERGE (x)-[r:CO_BID]->(y)
                    ON CREATE SET r.count = 1
                    ON MATCH SET r.count = r.count + 1
                """, a=a, b=b)