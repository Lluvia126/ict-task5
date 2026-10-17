"""运行 V1.1 Prompt（仅 Gold Set 的 10 个公告）并与 V1 / V2 对比。

做法：
  - 运行时临时替换 qwen_extractor 模块全局 EXTRACT_PROMPT（不修改任何文件）。
  - 复用 process_file 流程（清洗→分块→抽取→合并）。
  - 输出到 baseline_output_v1_1/。
  - 用 evaluate_goldset_v2 的评估逻辑对比 V1 / V1.1 / V2。
"""
import json
from pathlib import Path

from ai_extraction.extractors import qwen_extractor
from ai_extraction.prompt_v1_1 import EXTRACT_PROMPT_V1_1
from ai_extraction.run_baseline import process_file
from ai_extraction.evaluation.evaluate_goldset_v2 import (
    read_gold_full, evaluate_version,
)
from ai_extraction.evaluation.evaluate_goldset import V1_DIR, V2_DIR

INPUT_DIR = Path(__file__).parent / "data" / "baseline_input"
OUT_DIR = Path(__file__).parent / "data" / "baseline_output_v1_1"


def read_any_predictions(pred_dir):
    results = {}
    for jf in sorted(Path(pred_dir).glob("t*.json")):
        r = json.loads(jf.read_text(encoding="utf-8"))
        aid = r.get("announcement_id", jf.stem)
        products = r.get("result", {}).get("products", []) if r.get("result") else []
        results[aid] = products
    return results


def main():
    gold = read_gold_full()
    aids = sorted({g["announcement_id"] for g in gold})
    print(f"Gold Set 公告数: {len(aids)}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 临时换 Prompt（仅本进程生效）
    qwen_extractor.EXTRACT_PROMPT = EXTRACT_PROMPT_V1_1

    extractor = qwen_extractor.QwenExtractor()
    for i, aid in enumerate(aids, 1):
        f = INPUT_DIR / f"{aid}.html"
        print(f"\n[{i}/{len(aids)}] {aid}")
        rec = process_file(f, extractor)
        with open(OUT_DIR / f"{aid}.json", "w", encoding="utf-8") as fo:
            json.dump(rec, fo, ensure_ascii=False, indent=2)
        nprod = len(rec["result"]["products"]) if rec.get("result") else 0
        print(f"  -> {rec['status']} products={nprod} {rec['elapsed_seconds']}s")

    # 三版本评估
    versions = {
        "V1": read_any_predictions(V1_DIR),
        "V1.1": read_any_predictions(OUT_DIR),
        "V2": read_any_predictions(V2_DIR),
    }
    evals = {}
    for name, preds in versions.items():
        stats, cc = evaluate_version(gold, preds)
        evals[name] = (stats, cc)

    field_defs = [("product_service_name","产品/服务名称"),("category","品目"),
                  ("brand","品牌"),("spec_model","规格型号"),
                  ("unit_price","单价"),("quantity","数量"),("total_price","总价")]

    print("\n" + "=" * 86)
    print("V1 / V1.1 / V2 字段级 F1 对比")
    print("=" * 86)
    print(f"{'字段':<12}{'V1 P':>8}{'V1.1 P':>9}{'V2 P':>8}{'V1 R':>8}{'V1.1 R':>9}{'V2 R':>8}{'V1 F1':>8}{'V1.1 F1':>9}{'V2 F1':>8}")
    for en, cn in field_defs:
        row = f"{cn:<12}"
        for metric in ("Precision", "Recall", "F1"):
            for name, width in (("V1", 8), ("V1.1", 9), ("V2", 8)):
                row += f"{evals[name][0][en][metric]:>{width}.4f}"
        print(row)

    print("\n" + "=" * 86)
    print("V1 → V1.1 变化")
    print("=" * 86)
    print(f"{'字段':<12}{'V1 F1':>9}{'V1.1 F1':>9}{'变化':>10}  趋势")
    for en, cn in field_defs:
        a = evals["V1"][0][en]["F1"]
        b = evals["V1.1"][0][en]["F1"]
        d = round(b - a, 4)
        t = "提升" if d > 0.01 else ("下降" if d < -0.01 else "基本持平")
        print(f"{cn:<12}{a:>9.4f}{b:>9.4f}{d:>+10.4f}  {t}")

    print("\n" + "=" * 86)
    print("汇总")
    print("=" * 86)
    for name in ("V1", "V1.1", "V2"):
        stats, cc = evals[name]
        mf = round(sum(stats[en]["F1"] for en, _ in field_defs) / 7, 4)
        print(f"{name:<5} Macro-F1={mf}  matched={cc['matched']} "
              f"真漏抽={cc['true_miss']} 真多抽={cc['true_extra']} pred总数={cc['total_pred']}")
    print("\n（10公告/133条Gold上的实验结果，非官方比赛最终得分）")
    print(f"V1.1 结果目录: {OUT_DIR}")


if __name__ == "__main__":
    main()
