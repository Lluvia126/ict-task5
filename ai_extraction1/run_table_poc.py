"""单文件 POC：表格结构预处理文本 + 原始 V1 Prompt → Qwen。

只处理 t20260811_27115458：
  - 运行时临时把 qwen_extractor 全局 EXTRACT_PROMPT 换成 Git(f3dd923) 原始 V1。
  - 输入为 html_table_preprocessor 生成的结构化文本（47 个子项，单 chunk）。
  - 温度/响应格式等与 V1 一致；不修改正式 qwen_extractor.py。
  - 输出 data/table_test/t20260811_27115458_table_poc.json。

实验后与原始 V1 baseline 同文件结果对比（不重跑其他数据）。
"""
import json
from pathlib import Path

from ai_extraction.extractors import qwen_extractor
from ai_extraction.preprocessing.html_table_preprocessor import (
    extract_lots, render_preprocessed_text,
)

BASE = Path(__file__).parent
SRC = BASE / "data" / "validation" / "t20260811_27115458.html"
PRE_TXT = BASE / "data" / "table_test" / "t20260811_27115458_preprocessed.txt"
OUT_JSON = BASE / "data" / "table_test" / "t20260811_27115458_table_poc.json"
V1_JSON = BASE / "data" / "baseline_output" / "t20260811_27115458.json"

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


def compact(result):
    if not result:
        return []
    rows = []
    for p in result["products"]:
        rows.append({
            "name": p.get("product_service_name"),
            "brand": p.get("brand"),
            "spec": p.get("spec_model"),
            "unit_price": p.get("unit_price"),
            "quantity": p.get("quantity"),
            "total_price": p.get("total_price"),
        })
    return rows


def main():
    # 1) 生成/读取预处理文本
    html = SRC.read_text(encoding="utf-8", errors="ignore")
    lots = extract_lots(html)
    pre_text = render_preprocessed_text(lots)
    PRE_TXT.write_text(pre_text, encoding="utf-8")
    n_sub = sum(len(l["sub_items"]) for l in lots)
    print(f"预处理: {len(lots)} 宗, {n_sub} 子项, 文本长度={len(pre_text)}")

    # 2) 临时换原始 V1 Prompt；按"宗"分别调用，避免 47 项 JSON 超出输出长度
    qwen_extractor.EXTRACT_PROMPT = V1_PROMPT_ORIGINAL
    extractor = qwen_extractor.QwenExtractor()
    print("模型:", extractor.model, "| temperature=0 | json_object | 分批调用(失败自动重试)")

    from ai_extraction.config import empty_result
    merged = empty_result("t20260811_27115458", "", SRC.name)
    lot_errors = []

    # 摊平为 (lot, sub_item)，按每组 BATCH 个分批调用
    # （单次输出约 6000 字符封顶，28 项会被截断；12 项稳妥容纳）
    BATCH = 12
    flat = [(lot, it) for lot in lots for it in lot["sub_items"]]
    groups = [flat[i:i + BATCH] for i in range(0, len(flat), BATCH)]
    print(f"\n分 {len(groups)} 批调用（每批≤{BATCH}项）")

    def render_group(group):
        ls = []
        for lot, it in group:
            ls.append(f"【宗/包】{lot['lot_no'] or ''}")
            ls.append(f"【品目名称】{lot['category'] or ''}")
            ls.append(f"【采购标的】{lot['target'] or ''}")
            ls.append(f"【数量（单位）】{lot['quantity'] or ''}")
            ls.append("【子项】")
            ls.append(f"品牌：{it['brand'] or ''}")
            ls.append(f"规格型号：{it['spec_model'] or ''}")
            ls.append("")
        return "\n".join(ls)

    for bi, group in enumerate(groups, 1):
        gtxt = render_group(group)
        print(f"\n--- 批{bi}/{len(groups)} 项数={len(group)} 文本长度={len(gtxt)} ---")
        r = None
        last_err = None
        for attempt in range(1, 4):  # 最多 3 次（含重试），应对偶发输出截断
            try:
                r = extractor.extract(
                    gtxt,
                    announcement_id=f"t20260811_27115458_b{bi}",
                    source_file=SRC.name,
                )
                break
            except Exception as e:
                last_err = e
                print(f"    第{attempt}次失败: {type(e).__name__}")
        if r is not None:
            for f in ("project_name", "project_code", "purchaser", "project_amount"):
                if merged[f] is None and r.get(f) is not None:
                    merged[f] = r[f]
            for s in r.get("winning_suppliers", []):
                if s not in merged["winning_suppliers"]:
                    merged["winning_suppliers"].append(s)
            for s in r.get("bid_participants", []):
                if s not in merged["bid_participants"]:
                    merged["bid_participants"].append(s)
            merged["products"].extend(r.get("products", []))
            print(f"    成功 products={len(r.get('products', []))}")
        else:
            lot_errors.append(f"批{bi}: {type(last_err).__name__}: {last_err}")
            print(f"    3 次均失败: {last_err}")

    record = {
        "announcement_id": "t20260811_27115458",
        "source_file": SRC.name,
        "mode": "table_preprocessed(per-lot) + V1 prompt",
        "sub_items_in_input": n_sub,
        "status": "success" if not lot_errors else "partial_failed",
        "lot_errors": lot_errors,
        "result": merged,
    }
    OUT_JSON.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    result = merged

    # 3) 与原始 V1 baseline 对比
    v1 = json.loads(V1_JSON.read_text(encoding="utf-8"))
    v1_result = v1.get("result")
    v1_rows = compact(v1_result)
    poc_rows = compact(record.get("result"))

    print("\n" + "=" * 80)
    print("① 原始 V1（清洗文本直接抽取）")
    print("=" * 80)
    print(f"products = {len(v1_rows)}")
    for r in v1_rows:
        print(f"  name={r['name']!r}\n      brand={r['brand']!r} spec={r['spec']!r}")

    print("\n" + "=" * 80)
    print("② 表格预处理 + V1 Prompt")
    print("=" * 80)
    print(f"products = {len(poc_rows)}")
    for r in poc_rows[:20]:
        print(f"  name={r['name']!r} brand={r['brand']!r} spec={r['spec']!r}")
    if len(poc_rows) > 20:
        print(f"  … 其余 {len(poc_rows)-20} 条")

    # 4) 专项检查
    print("\n" + "=" * 80)
    print("③ 专项检查")
    print("=" * 80)
    joined_names = " ".join(str(r["name"]) for r in poc_rows)
    brand_as_name = any(r["name"] and len(str(r["name"])) > 12 and
                        ("、" in str(r["name"]) or "；" in str(r["name"]))
                        for r in poc_rows)
    trunc = any(r["spec"] and str(r["spec"]).endswith(("下", "（", "(")) for r in poc_rows)
    paired = sum(1 for r in poc_rows if r["brand"] and r["spec"])
    print(f"品牌串被当成产品名: {'是' if brand_as_name else '否'}")
    print(f"品牌与型号整列错位: 见上（预处理版 brand/spec 为分列单值）")
    print(f"型号被截断: {'是' if trunc else '否'}")
    print(f"同时含品牌+型号的产品数: {paired} / {len(poc_rows)}")
    print(f"明显多抽: products={len(poc_rows)} vs 输入子项={n_sub}")
    print(f"\n输出: {OUT_JSON}")


if __name__ == "__main__":
    main()
