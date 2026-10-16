"""批量把 SQLite 抽取结果重建到 Neo4j（图谱 = 抽取结果的投影）。

    .venv\\Scripts\\python.exe -m scripts.import_to_neo4j

行为：清空图谱后按当前抽取结果全量重建，因此也会一并清除早期手工导入的
历史演示数据（那些关系没有 announcement_ids，查询层本来就查不到）。
只想增量补差（不动历史数据）时用接口：POST /api/analysis/graph/sync?mode=incremental
"""

from services.graph_sync import rebuild_all

if __name__ == "__main__":
    result = rebuild_all()
    print("抽取记录:", result.get("records"), "| 导入成功:", result.get("imported"),
          "| 失败:", len(result.get("failed") or []))
    for item in result.get("failed") or []:
        print("  FAIL", item)
    graph = result.get("graph") or {}
    print("图谱：节点 %s，关系 %s（无来源历史关系 %s）" % (
        graph.get("nodes"), graph.get("relationships"), graph.get("legacy_relationships")))
    print("一致性 in_sync =", result.get("in_sync"))