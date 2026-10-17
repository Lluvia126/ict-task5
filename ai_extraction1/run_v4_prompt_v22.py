"""V4: V2.2 输入 + V4 Prompt（强实体边界）+ qwen3.8-max。

V3 → V4 唯一变化：Prompt 升级（新增情况A/B/C判断、去重规则、内部决策检查）
其余不变：V2.2 候选表 / temperature=0 / chunk 参数 / merge / binary split recovery。
"""
import contextlib
import csv
import json
import sys
import time
from pathlib import Path

from ai_extraction.extractors import qwen_extractor
from ai_extraction.pipeline import build_plan, process_html
from ai_extraction.preprocessing.cleaner import preprocess_text
from ai_extraction.preprocessing.v2_2_table_filter import TABLE_SCORE_THRESHOLD, render_table
from ai_extraction.run_validation_50 import CountingExtractor, TeeLog

BASE = Path(__file__).parent
VAL_DIR = BASE / "data" / "validation"
DATA = BASE / "data" / "validation_attachment_test"
PARSED_DIR = DATA / "parsed"
AUDIT_DIR = DATA / "run_v2_2_filter_audit"
RUN_DIR = DATA / "run_v4_prompt_v22"
LOG_DIR = RUN_DIR / "run_logs"
CSV_PATH = Path(r"E:\项目数据与统计\验证集待人工复核清单.csv")

V4_PROMPT = """你是一个面向政府采购、招投标公告和采购文件的结构化信息抽取模型。

你的任务是：从输入的招投标文本中，识别并抽取"公告一实体"的项目级信息和采购产品/服务实体。

## 一、总体原则

1. 只抽取原文中明确出现的信息，不允许推测、补全或编造。
2. 优先保证"实体边界正确"，不要因为追求召回而把参数、组成部件、技术指标、属性词、表格字段误当成独立实体。
3. 同一采购实体在原文不同位置重复出现时，只保留一个最完整、最具有代表性的实体记录。
4. 产品/服务实体必须具有明确的采购对象语义，而不是仅仅"看起来像产品名称"。

## 二、什么可以作为产品/服务实体

满足以下任一条件，并结合上下文确认其属于实际采购对象时，可以抽取：

1. 在"采购内容、采购标的、货物名称、服务名称、采购清单、报价清单"等位置作为独立采购对象出现。
2. 作为一个独立的货物、设备、软件或服务项目出现，并具有明确的采购意义。
3. 在正式采购明细表中作为独立的一行采购标的出现，并具有明确的名称、数量、单位、单价或总价等信息中的一项或多项。

## 三、以下情况默认不要单独抽取

除非原文明确说明其本身就是独立采购标的，否则不要把以下内容作为独立产品/服务实体：

1. 产品的技术参数或配置项，例如：
   CPU、内存、硬盘容量、处理器、接口、屏幕尺寸、分辨率、频率等。
2. 某个设备的组成部件，例如：
   电源模块、网卡、风扇、线缆、机架、配件等。
3. 仅用于描述性能、规格、功能的词语。
4. 仅出现在"技术参数表、配置说明、实施方案、验收标准、服务要求"中的产品词，但未明确作为独立采购对象。
5. "合计、小计、总计、税额、折扣、运费"等金额统计项。
6. 数量、金额、单位、税率等表格属性。
7. 品牌名称、型号名称、规格参数本身，不作为独立产品实体；它们应作为产品实体的属性。
8. 一种产品内部的组成项，不因为存在单独数量或参数就自动视为独立采购实体。

## 四、特别处理：报价明细和设备清单

招投标文件中可能同时存在：

* 服务项目 + 设备配置表
* 服务项目 + 设备报价明细
* 总体采购项目 + 大量设备参数
* 一个项目下的多个组成部分

此时必须根据上下文判断：

### 情况 A：明确是独立采购标的

例如表格明确存在"货物名称/采购名称"，每一行代表一个实际采购对象，并且该对象具有独立采购数量、单价或总价。

这种情况下，可以抽取。

### 情况 B：只是服务实施过程中使用的设备、材料或配置

如果这些设备只是为了完成某项服务而列出的实施配置、技术参数或内部组成，并未被明确作为独立采购标的，则不要把每个设备都抽取为产品实体。

### 情况 C：无法确定

当上下文无法判断某项到底是独立采购对象还是技术/实施组成项时：

* 不要仅因为它是一个具体设备名称就强行抽取；
* 优先选择具有明确采购标的语义的上层实体；
* 不进行猜测。

## 五、去重规则

1. 同一个产品/服务在正文、采购清单、报价表、附件中多次出现时，只保留一个实体。
2. 优先保留信息最完整的一条记录。
3. 不要因为品牌不同、格式不同或表格重复而重复生成相同实体。
4. 名称完全相同或经过简单格式规范化后相同的实体，应视为同一实体。

## 六、七个字段的抽取规则

每个 products 实体包含：

* product_service_name：产品或服务名称
* category：品目
* brand：品牌
* spec_model：规格型号
* unit_price：单价
* quantity：数量
* total_price：总价

### product_service_name

必须是明确的采购对象名称。

### category

只有原文明确出现品目/分类信息时才填写。
不要根据产品名称自行推断分类。

### brand

只有原文明确出现品牌时填写。
不要根据型号猜测品牌。

### spec_model

填写原文明确出现的规格、型号。
不要自行组合或生成型号。

### unit_price

仅填写原文明确给出的单价。

### quantity

仅填写原文明确给出的采购数量。

### total_price

仅填写原文明确给出的该实体总价。
"合计""小计"等不属于产品实体的统计金额不要单独创建实体。

## 七、空值规则

如果某字段在原文中没有明确出现：

* 字符串字段填写 null 或空字符串；
* 数值字段填写 null；
* 不允许根据上下文猜测。

## 八、最终判断标准

在输出一个 products 实体之前，内部必须回答：

1. 这是否是一个真实的采购对象？
2. 它是否在当前上下文中被作为独立对象采购？
3. 它是否只是另一个产品/服务的组成部分、参数或属性？
4. 它是否已经在前文或其他表格中出现过？

只有在确认"属于独立采购对象"后，才输出该实体。

宁可不抽取无法确认的候选项，也不要把技术参数、组成部件和实施配置大量误抽为独立产品。

## 九、输出要求

严格输出 JSON，不要输出解释文字、Markdown 或代码块。

字段结构：

{
"project_name": null,
"project_code": null,
"purchaser": null,
"winning_suppliers": [],
"bid_participants": [],
"project_amount": null,
"products": [
{
"product_service_name": null,
"category": null,
"brand": null,
"spec_model": null,
"unit_price": null,
"quantity": null,
"total_price": null
}
]
}

所有字段必须来自原文。
不要编造。
不要因为"看起来像产品"就抽取。
重点保证产品/服务实体的边界正确。

输入文本：
{text}"""


def read_validation_aids():
    rows = list(csv.DictReader(
        CSV_PATH.read_text(encoding="utf-8-sig").splitlines()))
    return [r["announcement_id"].strip() for r in rows]


def load_selected():
    parsed = {}
    for jf in sorted(PARSED_DIR.rglob("*.json")):
        rec = json.loads(jf.read_text(encoding="utf-8"))
        parsed[(rec["announcement_id"], rec["attachment_filename"])] = rec
    selected = {}
    for r in csv.DictReader(
            open(AUDIT_DIR / "selection_audit.csv", encoding="utf-8-sig")):
        if r["selected_v22"] != "True":
            continue
        aid, fn, ti = r["announcement_id"], r["attachment_name"], int(
            r["table_index"])
        tb = parsed[(aid, fn)]["tables"][ti]
        selected.setdefault(aid, []).append((fn, ti, tb))
    return selected


def build_input(aid, selected_by_aid):
    html = (VAL_DIR / f"{aid}.html").read_text(encoding="utf-8",
                                               errors="ignore")
    parts = ["===== 公告正文 =====\n" + preprocess_text(html)]
    items = selected_by_aid.get(aid, [])
    if items:
        last_fn = None
        att_parts = []
        for fn, ti, tb in items:
            if fn != last_fn:
                att_parts.append(f"===== 第一层附件：{fn} =====")
                last_fn = fn
            att_parts.append(f"===== 报价明细表：表格{ti + 1} =====\n"
                             + render_table(tb))
        parts.append("\n".join(att_parts))
    return "\n\n".join(parts), ("html+v22tables" if items else "html_only")


def do_extract():
    rerun = "--rerun" in sys.argv
    selected_by_aid = load_selected()
    qwen_extractor.EXTRACT_PROMPT = V4_PROMPT
    from ai_extraction.extractors.qwen_extractor import QwenExtractor
    extractor = CountingExtractor(QwenExtractor())
    print(f"[V4] Prompt=V4(强实体边界) 模型: {qwen_extractor.QWEN_MODEL} "
          f"入选表公告: {len(selected_by_aid)}", flush=True)

    aids = read_validation_aids()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    records = []
    for i, aid in enumerate(aids, 1):
        out_path = RUN_DIR / f"{aid}.json"
        if out_path.exists() and not rerun:
            records.append(json.loads(out_path.read_text(encoding="utf-8")))
            print(f"[{i}/50] SKIP {aid}", flush=True)
            continue
        text, kind = build_input(aid, selected_by_aid)
        extractor.reset()
        tee = TeeLog(LOG_DIR / f"{aid}.log")
        t0 = time.time()
        status = "success"
        try:
            with contextlib.redirect_stdout(tee):
                rec = process_html(text, aid, extractor, file_id="",
                                   source_file=f"{aid}.html")
        except Exception as e:
            status = "failed"
            rec = {"announcement_id": aid, "status": "failed",
                   "error": f"{type(e).__name__}: {e}", "result": None}
        finally:
            tee.close()
        elapsed = round(time.time() - t0, 2)
        plan = build_plan(text, announcement_id=aid)
        planned = sum(1 for c in plan["chunks"] if c["kind"] == "product")
        rec.update({
            "status": status, "input_kind": kind,
            "input_chars": len(text),
            "products_count": len((rec.get("result") or {})
                                  .get("products", []) or []),
            "chunks_total": len(plan["chunks"]),
            "planned_product_chunks": planned,
            "api_calls_attempts": extractor.attempts,
            "api_calls_success": extractor.success,
            "json_errors": extractor.json_errors,
            "api_errors": extractor.api_errors,
            "split_retries": max(0, extractor.product_success - planned),
            "elapsed_seconds": elapsed,
        })
        out_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        records.append(rec)
        print(f"[{i}/50] RUN {aid} status={status} "
              f"products={rec.get('products_count')} "
              f"chunks={rec.get('chunks_total')} "
              f"api={rec.get('api_calls_success')}/{rec.get('api_calls_attempts')} "
              f"elapsed={elapsed}s", flush=True)

    summary = {
        "experiment": "V4: V2.2输入 + V4 Prompt(强实体边界) + qwen3.8-max",
        "prompt": "V4_PROMPT（情况A/B/C判断+去重+内部决策检查）",
        "model": qwen_extractor.QWEN_MODEL,
        "threshold": TABLE_SCORE_THRESHOLD,
        "total": len(records),
        "success": sum(1 for r in records if r.get("status") == "success"),
        "input_kind": {
            "html+v22tables": sum(1 for r in records
                                  if r.get("input_kind") == "html+v22tables"),
            "html_only": sum(1 for r in records
                             if r.get("input_kind") == "html_only")},
        "products_total": sum(r.get("products_count", 0) for r in records),
        "chunks_total": sum(r.get("chunks_total", 0) for r in records),
        "api_calls_attempts": sum(r.get("api_calls_attempts", 0)
                                  for r in records),
        "api_calls_success": sum(r.get("api_calls_success", 0)
                                 for r in records),
        "json_errors": sum(r.get("json_errors", 0) for r in records),
        "api_errors": sum(r.get("api_errors", 0) for r in records),
        "split_retries": sum(r.get("split_retries", 0) for r in records),
        "elapsed_total_s": round(sum(r.get("elapsed_seconds", 0)
                                     for r in records), 1),
    }
    (RUN_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(RUN_DIR / "summary.csv", "w", encoding="utf-8-sig",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["announcement_id", "status", "input_kind", "products",
                    "chunks", "api_calls", "json_errors", "split_retries",
                    "elapsed_seconds"])
        for r in records:
            w.writerow([r["announcement_id"], r.get("status"),
                        r.get("input_kind"), r.get("products_count"),
                        r.get("chunks_total"), r.get("api_calls_success"),
                        r.get("json_errors"), r.get("split_retries"),
                        r.get("elapsed_seconds")])
    print("\n===== V4 运行汇总 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def do_evaluate():
    from ai_extraction.evaluation.evaluate_attachment_run import macro, read_pred
    from ai_extraction.evaluation.evaluate_goldset import FIELDS
    from ai_extraction.evaluation.evaluate_validation_50 import evaluate, read_gold

    gold = read_gold()
    pred_v4, _ = read_pred(RUN_DIR)
    stats, counts, match_how = evaluate(gold, pred_v4)
    m = macro(stats)

    html_only = json.loads((BASE / "data" / "validation_run_final"
                            / "evaluation_50_result.json")
                           .read_text(encoding="utf-8"))
    v2 = json.loads((DATA / "run_v2_attachment_filter"
                     / "evaluation_v2_result.json")
                    .read_text(encoding="utf-8"))
    v22 = json.loads((DATA / "run_v2_2_attachment"
                      / "evaluation_v2_2_result.json")
                     .read_text(encoding="utf-8"))
    v3 = json.loads((DATA / "run_v3_prompt_v22"
                      / "evaluation_v3_result.json")
                     .read_text(encoding="utf-8"))

    pred_v22, _ = read_pred(DATA / "run_v2_2_attachment")
    pred_v3, _ = read_pred(DATA / "run_v3_prompt_v22")

    import re
    def norm(s):
        if not s: return ""
        return re.sub(r"[\s（）()\[\]【】、,，。:：·\-—_/\\]+", "", str(s))

    t508 = "t20260508_26523656"
    t508_pred_v22 = len(pred_v22.get(t508, []))
    t508_pred_v3 = len(pred_v3.get(t508, []))
    t508_pred_v4 = len(pred_v4.get(t508, []))

    t206 = "t20260206_26155241"
    gold_raw = json.loads(Path(
        r"E:\xwechat_files\wxid_tqvrrgep742c22_709a\msg\file\2026-09\validation_v0(1).json"
    ).read_text(encoding="utf-8"))
    gold_by = {r["announcement_id"]: r.get("products", []) for r in gold_raw}
    gnames = {norm(p.get("product_service_name")) for p in gold_by.get(t206, []) if p.get("product_service_name")}
    pnames_v3 = {norm(p.get("product_service_name")) for p in pred_v3.get(t206, []) if p.get("product_service_name")}
    pnames_v4 = {norm(p.get("product_service_name")) for p in pred_v4.get(t206, []) if p.get("product_service_name")}
    t206_v3_miss = len(gnames - pnames_v3)
    t206_v4_miss = len(gnames - pnames_v4)

    result = {
        "experiment": "V4: V2.2输入 + V4 Prompt + qwen3.8-max",
        "macro_f1": m,
        "macro_f1_html_only": html_only["macro_f1"],
        "macro_f1_v2": v2["macro_f1"],
        "macro_f1_v22": v22["macro_f1"],
        "macro_f1_v3": v3["macro_f1"],
        "gold_products": counts["gold_products"],
        "pred_products": counts["pred_products"],
        "matched": counts["matched"],
        "unmatched_gold": counts["unmatched_gold"],
        "unmatched_pred": counts["unmatched_pred"],
        "fields": {cn: stats[en] for en, cn, _ in FIELDS},
        "special": {
            "t20260206_gold_missed_v3": t206_v3_miss,
            "t20260206_gold_missed_v4": t206_v4_miss,
            "t20260508_pred_v22": t508_pred_v22,
            "t20260508_pred_v3": t508_pred_v3,
            "t20260508_pred_v4": t508_pred_v4,
        },
    }
    (RUN_DIR / "evaluation_v4_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== HTML-only vs V2 vs V2.2 vs V3 vs V4 =====\n")
    print(f"{'指标':<16}{'HTML-only':>10}{'V2':>10}{'V2.2':>10}{'V3':>10}{'V4':>10}")
    print(f"{'Macro-F1':<16}{html_only['macro_f1']:>10.4f}{v2['macro_f1']:>10.4f}{v22['macro_f1']:>10.4f}{v3['macro_f1']:>10.4f}{m:>10.4f}")
    print(f"{'Pred products':<16}{html_only['pred_products']:>10}{v2['pred_products']:>10}{v22['pred_products']:>10}{v3['pred_products']:>10}{counts['pred_products']:>10}")
    print(f"{'Matched':<16}{html_only['matched']:>10}{v2['matched']:>10}{v22['matched']:>10}{v3['matched']:>10}{counts['matched']:>10}")
    print(f"{'Unmatched Gold':<16}{html_only['unmatched_gold']:>10}{v2['unmatched_gold']:>10}{v22['unmatched_gold']:>10}{v3['unmatched_gold']:>10}{counts['unmatched_gold']:>10}")
    print(f"{'Unmatched Pred':<16}{html_only['unmatched_pred']:>10}{v2['unmatched_pred']:>10}{v22['unmatched_pred']:>10}{v3['unmatched_pred']:>10}{counts['unmatched_pred']:>10}")

    print("\n7字段 P/R/F1 五组对比：")
    print(f"{'字段':<12}{'HTML-F1':>8}{'V2-F1':>8}{'V2.2-F1':>9}{'V3-F1':>9}{'V4-F1':>9}{'Δv4v3':>9}{'V4-P':>8}{'V4-R':>8}")
    for en, cn, _ in FIELDS:
        s = stats[en]
        v3f = v3["fields"][cn]["F1"]
        print(f"{cn:<12}{html_only['fields'][cn]['F1']:>8.4f}{v2['fields'][cn]['F1']:>8.4f}{v22['fields'][cn]['F1']:>9.4f}{v3f:>9.4f}{s['F1']:>9.4f}{s['F1']-v3f:>+9.4f}{s['Precision']:>8.4f}{s['Recall']:>8.4f}")

    print(f"\nV4 vs HTML-only ΔMacro: {m - html_only['macro_f1']:+.4f}")
    print(f"V4 vs V2 ΔMacro: {m - v2['macro_f1']:+.4f}")
    print(f"V4 vs V2.2 ΔMacro: {m - v22['macro_f1']:+.4f}")
    print(f"V4 vs V3 ΔMacro: {m - v3['macro_f1']:+.4f}")

    print("\n===== 专项公告 =====")
    print(f"t20260206: V3漏{t206_v3_miss} → V4漏{t206_v4_miss}")
    print(f"t20260508: V2.2={t508_pred_v22} → V3={t508_pred_v3} → V4={t508_pred_v4}")


if __name__ == "__main__":
    if "--evaluate" in sys.argv:
        do_evaluate()
    else:
        do_extract()
