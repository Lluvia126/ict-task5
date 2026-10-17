"""通用表格预处理器的验证集测试（纯结构，不调用 Qwen）。

仅处理手工挑选的 10 个代表性公告（来自 ai_extraction/data/validation/）：
  并行列表 / 伪并行（外部指引）/ 普通表（规格描述含标点）/
  大型普通表 / 天津式 rowspan 整表 / 纯服务类。

输出：
  ai_extraction/data/table_test/<announcement_id>_preprocessed.txt
  ai_extraction/data/table_test/preprocessor_test_summary.json
"""
import json
from pathlib import Path

from ai_extraction.preprocessing.html_table_preprocessor import (
    parse_announcement, render_report,
)

ROOT = Path(__file__).resolve().parents[1]
VAL_DIR = ROOT / "data" / "validation"
OUT_DIR = ROOT / "data" / "table_test"

# (announcement_id, 选样理由 / 期望行为)
SAMPLES = [
    ("t20260811_27115458", "已知复杂并行列表（2整包行→47子项，期望全部对齐）"),
    ("t20260707_26888876", "多宗：包1真并行35↔35含大量同位重复定制；包2指引；包3不等长+等"),
    ("t20260306_26238138", "伪并行（H3C、惠普等详见投标文件）+ 个别普通单值行"),
    ("t20260509_26538802", "11行全部外部指引（详见投标文件《三、分项报价表》）"),
    ("t20260810_27100396", "8行全部外部指引（按招标文件要求、详见项目清单报价）"),
    ("t20260710_26908531", "普通产品表：规格描述文本含;/、/句号；另含服务表"),
    ("t20260408_26371064", "普通产品表：规格=详见投标文件三、分项报价表（顿号在引用语内）"),
    ("t20260807_27091873", "大型普通产品表（63个一行一产品）"),
    ("t20260601_26666286", "天津式标的表：rowspan+整行多列并行，但结尾等/未尽"),
    ("t20260506_26515760", "纯服务类公告（信息系统维保，无品牌/规格列）"),
]


# ---------------------------------------------------------------- 机械自检

def mechanical_checks(parsed):
    """对 split/keep_raw 行做可自动核验的检查。

    返回 dict：
      fabricated     : split 子项中出现原文里不存在的 brand/spec（应为0）
      order_violation: split 子项在原文中的出现位置非单调递增（应为0）
      raw_lost       : keep_raw 行未保留原始单元格（应为0）
      merged_rows    : split 后子项数 < 列表段数（被去重，应为0）
    """
    bad = {"fabricated": 0, "order_violation": 0, "raw_lost": 0, "merged_rows": 0}
    for t in parsed["tables"]:
        if t["table_type"] != "product":
            continue
        for r in t["rows"]:
            d = r["decision"]
            brand_raw = r["raw_columns"].get("品牌", "")
            spec_raw = r["raw_columns"].get("规格型号", "")
            if d == "split":
                # 用偏移搜索：重复值（多个"定制"）各自匹配原文中下一出现位置
                last_b = last_s = -1
                n_b_parts = r.get("brand_count") or 0
                n_s_parts = r.get("spec_count") or 0
                if len(r["sub_items"]) != max(n_b_parts, n_s_parts):
                    bad["merged_rows"] += 1
                for it in r["sub_items"]:
                    b, s = it.get("brand") or "", it.get("spec_model") or ""
                    pb = brand_raw.find(b, last_b + 1 if last_b >= 0 else 0)
                    ps = spec_raw.find(s, last_s + 1 if last_s >= 0 else 0)
                    if pb < 0 or ps < 0:
                        bad["fabricated"] += 1
                    if pb <= last_b or ps <= last_s:
                        bad["order_violation"] += 1
                    last_b, last_s = pb, ps
            elif d == "keep_raw":
                if not brand_raw or not spec_raw:
                    bad["raw_lost"] += 1
    return bad


# ---------------------------------------------------------------- 主流程

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    records = []

    for ann, note in SAMPLES:
        src = VAL_DIR / f"{ann}.html"
        if not src.exists():
            print(f"[缺失] {src}")
            continue
        parsed = parse_announcement(
            src.read_text(encoding="utf-8", errors="ignore"),
            announcement_id=ann,
        )
        report = render_report(parsed)
        (OUT_DIR / f"{ann}_preprocessed.txt").write_text(report, encoding="utf-8")

        st = parsed["stats"]
        checks = mechanical_checks(parsed)
        triggered = st["split_rows"] > 0

        # 文件级分类
        if st["n_product_tables"] == 0:
            kind = "service_only"
        elif triggered:
            kind = "split_triggered"
        elif st["normal_rows"] > 0 and st["keep_raw_rows"] == 0:
            kind = "normal"
        elif st["normal_rows"] > 0:
            kind = "normal_with_ref_rows"
        else:
            kind = "all_ref_keep_raw"

        # 拆分前结构 = 产品表原始行数；拆分后 = 子项总数
        n_product_rows = st["normal_rows"] + st["split_rows"] + st["keep_raw_rows"]
        rec = {
            "announcement_id": ann,
            "sample_note": note,
            "file_class": kind,
            "split_triggered": triggered,
            "n_product_rows_before": n_product_rows,
            "n_sub_items_after": st["sub_items_total"],
            "normal_rows": st["normal_rows"],
            "split_rows": st["split_rows"],
            "keep_raw_rows": st["keep_raw_rows"],
            "aligned_pairs": st["aligned_pairs"],
            "duplicates_preserved": st["duplicates_preserved"],
            "uncertain_rows": st["uncertain_rows"],
            "brand_spec_order_kept": checks["order_violation"] == 0,
            "mechanical_checks": checks,
        }
        records.append(rec)

        # ---- 每个文件的简短人工检查输出
        print("=" * 94)
        print(f"{ann}  （{note}）")
        print(f"  触发拆分: {'是' if triggered else '否'} | "
              f"拆分前产品行={n_product_rows} → 拆分后子项={st['sub_items_total']}")
        print(f"  normal={st['normal_rows']} split={st['split_rows']} "
              f"keep_raw={st['keep_raw_rows']} | 对齐={st['aligned_pairs']} "
              f"重复保留={st['duplicates_preserved']}")
        print(f"  品牌↔型号顺序保持: {'是' if rec['brand_spec_order_kept'] else '否'} | "
              f"错误拆分/臆造={checks['fabricated']} 错误合并={checks['merged_rows']} "
              f"原文丢失={checks['raw_lost']}")
        print(f"  无法判断行(keep_raw): {st['keep_raw_rows']}；"
              f"明细见 {ann}_preprocessed.txt")

    # ---- 汇总
    agg = {
        "n_files": len(records),
        # 文件级
        "files_split_triggered": sum(r["split_triggered"] for r in records),
        "files_not_triggered": sum(not r["split_triggered"] for r in records),
        "files_normal": sum(r["file_class"] in ("normal", "normal_with_ref_rows")
                           for r in records),
        "files_service_only": sum(r["file_class"] == "service_only" for r in records),
        "files_structure_recovered": sum(r["split_triggered"]
                                        and r["mechanical_checks"]["fabricated"] == 0
                                        and r["mechanical_checks"]["order_violation"] == 0
                                        for r in records),
        "files_with_uncertain": sum(r["uncertain_rows"] > 0 for r in records),
        # 行级
        "rows_normal": sum(r["normal_rows"] for r in records),
        "rows_split": sum(r["split_rows"] for r in records),
        "rows_keep_raw_unsafe_refused": sum(r["keep_raw_rows"] for r in records),
        "aligned_brand_spec_pairs": sum(r["aligned_pairs"] for r in records),
        "duplicates_preserved": sum(r["duplicates_preserved"] for r in records),
        # 机械自检问题计数
        "fabricated_items": sum(r["mechanical_checks"]["fabricated"] for r in records),
        "order_violations": sum(r["mechanical_checks"]["order_violation"] for r in records),
        "merged_rows": sum(r["mechanical_checks"]["merged_rows"] for r in records),
        "raw_lost_rows": sum(r["mechanical_checks"]["raw_lost"] for r in records),
    }
    summary = {"aggregate": agg, "files": records}
    (OUT_DIR / "preprocessor_test_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 94)
    print("汇总：")
    for k, v in agg.items():
        print(f"  {k}: {v}")
    print(f"\n输出目录: {OUT_DIR}")


if __name__ == "__main__":
    main()
