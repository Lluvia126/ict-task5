import json
import os

from .config import PROCESSED_DIR, ensure_dirs, empty_result
from .extractors.qwen_extractor import QwenExtractor


def batch_extract(texts, extractor=None, save_path=None,
                  announcement_ids=None, file_ids=None, source_files=None):
    """批量抽取：每条文本单独调用模型，单条失败不影响整体。

    announcement_ids/file_ids/source_files 为与 texts 等长的可选列表，
    缺省时 announcement_id 按序号生成，其余为空串。
    返回：
    [
        {"id": 1, "text": "...", "result": {...}, "error": null},
        ...
    ]
    """
    if extractor is None:
        extractor = QwenExtractor()

    records = []
    total = len(texts)
    for i, text in enumerate(texts):
        ann_id = (announcement_ids[i]
                  if announcement_ids and i < len(announcement_ids)
                  else str(i + 1))
        fid = file_ids[i] if file_ids and i < len(file_ids) else ""
        sf = source_files[i] if source_files and i < len(source_files) else ""

        rec = {"id": i + 1, "text": text,
               "result": empty_result(ann_id, fid, sf), "error": None}
        try:
            rec["result"] = extractor.extract(text, ann_id, fid, sf)
        except Exception as e:
            rec["error"] = str(e)
        records.append(rec)
        print(f"[{i + 1}/{total}] {'成功' if rec['error'] is None else '失败'}")

    if save_path:
        ensure_dirs()
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        print("结果已保存:", save_path)

    return records
