"""严格 V1 对照实验：用原始 V1 Prompt 重跑 Gold Set 10 公告。

目的：判断旧 V1 高分是否受 Qwen 后端版本/服务变化影响。
  - 运行时临时替换 qwen_extractor 全局 EXTRACT_PROMPT 为 Git(f3dd923) 原始 V1。
  - 温度/分块/预处理/调用参数/输出结构/后处理全部沿用，不改业务代码。
  - 输出 baseline_output_v1_rerun/。
  - 用 evaluate_goldset_v2 评估，并与旧V1/V1.1/V2 对比。
"""
import json
from pathlib import Path

from ai_extraction.extractors import qwen_extractor
from ai_extraction.run_baseline import process_file
from ai_extraction.evaluation.evaluate_goldset_v2 import (
    read_gold_full, evaluate_version,
)
from ai_extraction.evaluation.evaluate_goldset import V1_DIR, V2_DIR

INPUT_DIR = Path(__file__).parent / "data" / "baseline_input"
OUT_DIR = Path(__file__).parent / "data" / "baseline_output_v1_rerun"

# 原始 V1 Prompt（取自 Git f3dd923，逐字一致）
V1_PROMPT_ORIGINAL = """你是政府采购/招投标信息抽取助手。只根据原文抽取，不得编造；无法确定的字段填 null。
严格输出 JSON，不要输出任何解释文字。

输出结构：
{
  "project_name": "项目名称(string|null)",
  "project_code": "项目编号(string|null)",
  "purchaser": "采购单位(string|null)",
  "winning_suppliers": ["中标供应商(list[string]，无则 [])"],
  "bid_participants": ["投标参与方(list[string]，无则 [])"],
  "project_amount": "项目金额(number|null，单位元)",
  "products": [
    {
      "product_service_name": "产品服务名称(string|null)",
      "category": "品目(string|null)",
      "brand": "品牌(string|null)",
      "spec_model": "规格型号(string|null)",
      "unit_price": "单价(number|null)",
      "quantity": "数量(number|null)",
      "total_price": "总价(number|null)"
    }
  ]
}

规则：
1. 只能根据原文抽取，不允许编造。
2. 无法确定的字段使用 null。
3. winning_suppliers、bid_participants、products 必须为数组；无内容时返回 []。
4. 数字字段(project_amount、unit_price、quantity、total_price)只返回数字，不带"元""万元""台"等单位；金额以元为单位。
5. 产品字段必须保持同一产品的字段对应关系，按原文产品逐个提取。
6. 原文如为表格，按表格行对应提取每个产品。
7. 不要自行计算产品总价；原文未明确给出 total_price 时填 null。
8. 不要输出 announcement_id、file_id、source_file 字段。
9. 最终只返回合法 JSON。

文本：
{text}
"""


def read_any_predictions(pred_dir):
    results = {}
    for jf in sorted(Path(pred_dir).glob("t*.json")):
        r = json.loads(jf.read_text(encoding="utf-8"))
        aid = r.get("announcement_id", jf.stem)
        products = r.get("result", {}).get("products", []) if r.get("result") else []
        results[aid] = products
    return results


def main():
    # 0) 校验本脚本 Prompt 与 Git 原始 V1 完全一致
    import subprocess
    git_src = subprocess.run(
        ["git", "show", "f3dd923:ai_extraction/config.py"],
        capture_output=True, text=True, encoding="utf-8",
    ).stdout
    import re
    m = re.search(r'EXTRACT_PROMPT = """(.*?)"""', git_src, re.S)
    git_prompt_body = m.group(1)
    # git 版本含末尾换行；本脚本常量取 body（去掉尾部空行后比较核心）
    match_ok = V1_PROMPT_ORIGINAL.strip() == git_prompt_body.strip()
    print("Prompt 与 Git(f3dd923) 原始 V1 逐字一致:", match_ok)
    if not match_ok:
        print(">>> 警告：Prompt 不一致，终止以保证实验严谨 <<<")
        return

    gold = read_gold_full()
    aids = sorted({g["announcement_id"] for g in gold})
    print(f"Gold Set 公告数: {len(aids)}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 临时换为原始 V1 Prompt（仅本进程）
    qwen_extractor.EXTRACT_PROMPT = V1_PROMPT_ORIGINAL
    extractor = qwen_extractor.QwenExtractor()

    print("实际调用模型名称:", extractor.model)
    print("temperature=0 | response_format=json_object | MAX_CHUNK_CHARS=4000")

    import time
    t_total = time.time()
    n_api_err = n_json_err = n_other_err = 0
    per_file = []
    for i, aid in enumerate(aids, 1):
        f = INPUT_DIR / f"{aid}.html"
        print(f"\n[{i}/{len(aids)}] {aid}")
        rec = process_file(f, extractor)
        with open(OUT_DIR / f"{aid}.json", "w", encoding="utf-8") as fo:
            json.dump(rec, fo, ensure_ascii=False, indent=2)
        nprod = len(rec["result"]["products"]) if rec.get("result") else 0
        per_file.append((aid, rec["status"], nprod, rec["elapsed_seconds"]))
        if rec["status"] != "success":
            err = str(rec.get("error"))
            if "JSON" in err:
                n_json_err += 1
            elif "API" in err:
                n_api_err += 1
            else:
                n_other_err += 1
        print(f"  -> {rec['status']} products={nprod} {rec['elapsed_seconds']}s")

    total_elapsed = round(time.time() - t_total, 2)
    total_products = sum(p[2] for p in per_file)

    # 评估四版本
    versions = {
        "旧V1": read_any_predictions(V1_DIR),
        "新V1重跑": read_any_predictions(OUT_DIR),
        "V1.1": read_any_predictions(Path(__file__).parent / "data" / "baseline_output_v1_1"),
        "V2": read_any_predictions(V2_DIR),
    }
    evals = {name: evaluate_version(gold, preds) for name, preds in versions.items()}

    field_defs = [("product_service_name","产品/服务名称"),("category","品目"),
                  ("brand","品牌"),("spec_model","规格型号"),
                  ("unit_price","单价"),("quantity","数量"),("total_price","总价")]

    print("\n" + "=" * 80)
    print("运行情况")
    print("=" * 80)
    n_success = sum(1 for _, s, _, _ in per_file if s == "success")
    print(f"成功: {n_success}/10 | 总products: {total_products} | "
          f"总耗时: {total_elapsed}s | 平均: {round(total_elapsed/10,2)}s")
    print(f"API错误: {n_api_err} | JSON错误: {n_json_err} | 其他错误: {n_other_err}")
    print("每文件 products:")
    for aid, st, nprod, el in per_file:
        print(f"  {aid}: {nprod} ({st}, {el}s)")

    print("\n" + "=" * 80)
    print("旧V1 → 新V1重跑 字段级 F1 变化")
    print("=" * 80)
    print(f"{'字段':<12}{'旧V1 F1':>10}{'新V1 F1':>10}{'变化':>10}  趋势")
    for en, cn in field_defs:
        a = evals["旧V1"][0][en]["F1"]
        b = evals["新V1重跑"][0][en]["F1"]
        d = round(b - a, 4)
        t = "提升" if d > 0.01 else ("下降" if d < -0.01 else "基本持平")
        print(f"{cn:<12}{a:>10.4f}{b:>10.4f}{d:>+10.4f}  {t}")

    print("\n" + "=" * 80)
    print("四版本 Macro-F1 总览")
    print("=" * 80)
    for name in ("旧V1", "新V1重跑", "V1.1", "V2"):
        stats, cc = evals[name]
        mf = round(sum(stats[en]["F1"] for en, _ in field_defs) / 7, 4)
        print(f"{name:<8} Macro-F1={mf}  真漏抽={cc['true_miss']} 真多抽={cc['true_extra']} pred总数={cc['total_pred']}")
    print("\n（10公告/133条Gold上的实验结果，非官方比赛最终得分）")
    print(f"结果目录: {OUT_DIR}")


if __name__ == "__main__":
    main()
