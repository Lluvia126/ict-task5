"""生成 V4_vs_V4.1.csv 对比表。

读取 V4 和 V4.1 的评估结果 JSON，输出 7 字段 P/R/F1 + Delta_F1 对比。
"""
import csv
import json
from pathlib import Path

BASE = Path(__file__).parent.parent
V4_RESULT = BASE / "data" / "validation_attachment_test" / "run_v4_prompt_v22" / "evaluation_v4_result.json"
V41_RESULT = BASE / "data" / "validation_attachment_test" / "run_v4_1_prompt_v22" / "evaluation_v4_1_result.json"
OUT_CSV = BASE / "data" / "validation_attachment_test" / "run_v4_1_prompt_v22" / "V4_vs_V4.1.csv"

FIELDS_CN = [
    "产品/服务名称", "品目", "品牌", "规格型号", "单价", "数量", "总价",
]


def main():
    v4 = json.loads(V4_RESULT.read_text(encoding="utf-8"))
    v41 = json.loads(V41_RESULT.read_text(encoding="utf-8"))

    rows = [["Field", "V4_P", "V4_R", "V4_F1", "V4.1_P", "V4.1_R", "V4.1_F1", "Delta_F1"]]

    for cn in FIELDS_CN:
        v4f = v4["fields"][cn]
        v41f = v41["fields"][cn]
        delta = round(v41f["F1"] - v4f["F1"], 4)
        rows.append([
            cn,
            v4f["Precision"], v4f["Recall"], v4f["F1"],
            v41f["Precision"], v41f["Recall"], v41f["F1"],
            delta,
        ])

    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)

    print("===== V4 vs V4.1 对比表 =====\n")
    print(f"{'Field':<14}{'V4_P':>8}{'V4_R':>8}{'V4_F1':>8}{'V4.1_P':>9}{'V4.1_R':>9}{'V4.1_F1':>9}{'ΔF1':>9}")
    for row in rows[1:]:
        print(f"{row[0]:<14}{row[1]:>8.4f}{row[2]:>8.4f}{row[3]:>8.4f}"
              f"{row[4]:>9.4f}{row[5]:>9.4f}{row[6]:>9.4f}{row[7]:>+9.4f}")

    print(f"\nV4  Macro-F1: {v4['macro_f1']}  Pred={v4['pred_products']}  Matched={v4['matched']}  UG={v4['unmatched_gold']}  UP={v4['unmatched_pred']}")
    print(f"V4.1 Macro-F1: {v41['macro_f1']}  Pred={v41['pred_products']}  Matched={v41['matched']}  UG={v41['unmatched_gold']}  UP={v41['unmatched_pred']}")
    print(f"\nMacro ΔF1: {v41['macro_f1'] - v4['macro_f1']:+.4f}")
    print(f"CSV: {OUT_CSV}")


if __name__ == "__main__":
    main()
