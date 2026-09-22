from services.storage import list_extractions
from services.graph_db import init_constraints, import_extraction, close_driver

init_constraints()
print("约束建立完成")

records = list_extractions(limit=1000)
print(f"准备导入 {len(records)} 条")

success, failed = 0, 0
for i, record in enumerate(records, 1):
    try:
        import_extraction(record)
        success += 1
        print(f"[{i}/{len(records)}] OK {record.get('announcement_id')}")
    except Exception as e:
        failed += 1
        print(f"[{i}/{len(records)}] FAIL {record.get('announcement_id')}: {e}")

close_driver()
print(f"完成：成功 {success}，失败 {failed}")