"""V4.1: V2.2 输入 + V4.1 Prompt（强行级绑定+去重+价格隔离）+ qwen3.8-max。

V4 → V4.1 唯一变化：Prompt 升级（新增表格行级绑定、价格字段定义、
项目级/产品级隔离、续行恢复规则；强化去重；保留 V4 的 A/B/C 实体边界判断）

基于 V4 错误分析（972 实例）：
  TOP5: 字段漏值492 / 重复抽取85 / 过度抽取116 / 字段值错误74 / 漏抽31
  重点修复: 重复抽取(去重强化) + 字段值错误(行级绑定) + 项目级金额误入(隔离)
  保持: name/quantity Recall, A/B/C 实体边界

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
RUN_DIR = DATA / "run_v4_1_prompt_v22"
LOG_DIR = RUN_DIR / "run_logs"
CSV_PATH = Path(r"E:\项目数据与统计\验证集待人工复核清单.csv")

V4_1_PROMPT = """你是一个面向政府采购、招投标公告和采购文件的结构化信息抽取模型。

你的任务是：从输入的招投标文本中，识别并抽取"公告一实体"的项目级信息和采购产品/服务实体。

## 一、抽取目标定义

只抽取原文中明确出现的信息，不允许推测、补全或编造。
优先保证"实体边界正确"和"字段行级绑定准确"，不要因为追求召回而把参数、组成部件、技术指标、属性词、表格字段误当成独立实体，也不要把不属于当前产品行的价格、数量从其他行借过来。

## 二、实体边界

满足以下任一条件，并结合上下文确认其属于实际采购对象时，可以抽取：

1. 在"采购内容、采购标的、货物名称、服务名称、采购清单、报价清单"等位置作为独立采购对象出现。
2. 作为一个独立的货物、设备、软件或服务项目出现，并具有明确的采购意义。
3. 在正式采购明细表中作为独立的一行采购标的出现，并具有明确的名称、数量、单位、单价或总价等信息中的一项或多项。

以下内容默认不要单独抽取为产品实体：
- 产品的技术参数或配置项（CPU、内存、硬盘容量、处理器、接口、屏幕尺寸、分辨率、频率等）
- 某个设备的组成部件（电源模块、网卡、风扇、线缆、机架、配件等）
- 仅用于描述性能、规格、功能的词语
- 仅出现在"技术参数表、配置说明、实施方案、验收标准、服务要求"中的产品词
- "合计、小计、总计、税额、折扣、运费"等金额统计项
- 数量、金额、单位、税率等表格属性
- 品牌名称、型号名称、规格参数本身（它们是产品实体的属性，不是独立实体）

## 三、情况 A/B/C 判断

招投标文件中可能同时存在：服务项目 + 设备配置表、服务项目 + 设备报价明细、总体采购项目 + 大量设备参数、一个项目下的多个组成部分。

此时必须根据上下文判断：

### 情况 A：明确是独立采购标的
表格明确存在"货物名称/采购名称"，每一行代表一个实际采购对象，并且该对象具有独立采购数量、单价或总价。可以抽取。

### 情况 B：只是服务实施过程中使用的设备、材料或配置
如果这些设备只是为了完成某项服务而列出的实施配置、技术参数或内部组成，并未被明确作为独立采购标的，则不要把每个设备都抽取为产品实体。

### 情况 C：无法确定
当上下文无法判断某项到底是独立采购对象还是技术/实施组成项时：
- 不要仅因为它是一个具体设备名称就强行抽取
- 优先选择具有明确采购标的语义的上层实体
- 不进行猜测

特别避免把以下内容大量识别成产品：实施要求、技术要求、服务内容、配置说明、售后要求、项目说明。

## 四、表格行级绑定（重点）

对于产品名称、品目、品牌、规格型号、单价、数量、总价这七个字段，必须优先按照**同一行/同一条目**进行绑定。

规则：
1. unit_price、quantity、total_price 必须来自与 product_service_name 相同的表格行。
2. 禁止从上一行借单价，从下一行借单价，从其他产品行借数量或总价。
3. 禁止把整张表最后的合计金额分配给任何产品。
4. 禁止因为页面位置相邻就建立字段关联。
5. 只有在明确属于同一产品条目的情况下才能绑定字段。

如果某行的产品名称存在但该行没有单价/数量/总价，则对应字段填 null，不要从其他行借用。

## 五、价格字段定义

明确区分三个价格字段：

- unit_price = 单件/单项价格（一件产品的价格）
- total_price = 该条产品/服务对应的总价（该条目的行总价）
- quantity = 该条目的采购数量

规则：
1. 只有原文明确给出的、属于当前产品行的单价才能进入 unit_price。
2. 只有原文明确给出的、属于当前产品行的总价才能进入 total_price。
3. 不要用 unit_price × quantity 自行计算 total_price。即使可以算出数字，也不能把计算结果当作原文抽取结果。
4. 如果文本中只有项目总价，不要填到 total_price。
5. 如果文本中只有单价，不要自行计算总价。

以下金额不能直接作为 unit_price 或 total_price：
项目总金额、采购预算、中标总金额、最高限价、投标报价总额、税费、服务费、报价合计、包/标段合计、折扣后的项目总价、付款金额、保证金、其他非产品行金额。

不要因为文本附近出现"价格"就自动抽取。金额必须能明确绑定到具体产品/服务条目时才能进入价格字段。

## 六、项目级与产品级信息隔离

以下字段属于项目级信息：project_amount、项目预算、项目总金额、中标总金额、合同金额、采购预算、最高限价。

规则：
1. 项目级金额只能进入 project_amount 字段。
2. 不能因为项目级金额与产品表同时出现，就把它们复制到 unit_price 或 total_price。
3. project_amount 反映整个采购项目的总预算或总金额，不反映单个产品的价格。

## 七、表格续行恢复

允许合理的续行恢复，但必须有明确结构依据。

例如：
第一行：产品名称 | 规格型号 | 单价 | 数量
第二行：          型号补充 | 1000 | 2

如果解析结果能够明确证明第二行属于第一条产品（如第二行无独立产品名但有规格/价格补充），可以恢复到第一条产品。

但如果无法确认第二行属于哪一个产品：不要猜。宁可对应字段为 null，也不要跨行强行绑定。

## 八、去重规则（强化）

1. 同一个产品/服务在正文、采购清单、报价表、附件、页脚、汇总表中多次出现时，只保留一个实体。
2. 优先保留信息最完整、结构最明确的一条记录。
3. 名称完全相同或经过简单格式规范化后相同的实体，必须视为同一实体，只输出一条。
4. 不要因为品牌不同、格式不同或表格重复而重复生成相同实体。
5. 如果同一产品名称在不同表格中出现多次（如在不同包组/标段中重复列出），只保留字段最完整的一条。

## 九、字段抽取规则与缺失值处理

每个 products 实体包含：
- product_service_name：产品或服务名称（必须是明确的采购对象名称）
- category：品目（只有原文明确出现品目/分类信息时才填写，不要根据产品名称自行推断分类）
- brand：品牌（只有原文明确出现品牌时填写，不要根据型号猜测品牌，不要把公司名称、制造商名称、供应商名称、型号前缀当作品牌）
- spec_model：规格型号（只填写原文明确出现的规格、型号，不要自行组合或生成型号，不要把产品名称、数量、单位、参数描述、功能介绍拼进规格型号）
- unit_price：单价（仅填写原文明确给出的、属于当前行的单价）
- quantity：数量（仅填写原文明确给出的采购数量）
- total_price：总价（仅填写原文明确给出的该实体总价）

如果某字段在原文中没有明确出现：
- 字符串字段填写 null
- 数值字段填写 null
- 不允许根据上下文猜测

## 十、最终判断标准与 JSON 输出

在输出一个 products 实体之前，内部必须回答：
1. 这是否是一个真实的采购对象？
2. 它是否在当前上下文中被作为独立对象采购？
3. 它是否只是另一个产品/服务的组成部分、参数或属性？
4. 它是否已经在前文或其他表格中出现过（需要去重）？
5. unit_price / quantity / total_price 是否来自与产品名称相同的行？

只有在确认"属于独立采购对象"且"字段行级绑定正确"后，才输出该实体。

宁可不抽取无法确认的候选项，也不要把技术参数、组成部件和实施配置大量误抽为独立产品。
宁可字段为 null，也不要跨行强行绑定价格或数量。

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

所有字段必须来自原文。不要编造。不要因为"看起来像产品"就抽取。重点保证产品/服务实体的边界正确和字段行级绑定准确。

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
    qwen_extractor.EXTRACT_PROMPT = V4_1_PROMPT
    from ai_extraction.extractors.qwen_extractor import QwenExtractor
    extractor = CountingExtractor(QwenExtractor())
    print(f"[V4.1] Prompt=V4.1(行级绑定+去重+价格隔离) 模型: {qwen_extractor.QWEN_MODEL} "
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
        "experiment": "V4.1: V2.2输入 + V4.1 Prompt(行级绑定+去重+价格隔离) + qwen3.8-max",
        "prompt": "V4_1_PROMPT（行级绑定+去重强化+价格字段定义+项目级隔离+续行恢复+A/B/C）",
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
    print("\n===== V4.1 运行汇总 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def do_evaluate():
    from ai_extraction.evaluation.evaluate_attachment_run import macro, read_pred
    from ai_extraction.evaluation.evaluate_goldset import FIELDS
    from ai_extraction.evaluation.evaluate_validation_50 import evaluate, read_gold

    gold = read_gold()
    pred_v41, _ = read_pred(RUN_DIR)
    stats, counts, match_how = evaluate(gold, pred_v41)
    m = macro(stats)

    v4 = json.loads((DATA / "run_v4_prompt_v22"
                     / "evaluation_v4_result.json")
                    .read_text(encoding="utf-8"))
    html_only = json.loads((BASE / "data" / "validation_run_final"
                            / "evaluation_50_result.json")
                           .read_text(encoding="utf-8"))

    result = {
        "experiment": "V4.1: V2.2输入 + V4.1 Prompt + qwen3.8-max",
        "macro_f1": m,
        "macro_f1_html_only": html_only["macro_f1"],
        "macro_f1_v4": v4["macro_f1"],
        "gold_products": counts["gold_products"],
        "pred_products": counts["pred_products"],
        "matched": counts["matched"],
        "unmatched_gold": counts["unmatched_gold"],
        "unmatched_pred": counts["unmatched_pred"],
        "fields": {cn: stats[en] for en, cn, _ in FIELDS},
    }
    (RUN_DIR / "evaluation_v4_1_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== HTML-only vs V4 vs V4.1 =====\n")
    print(f"{'指标':<16}{'HTML-only':>10}{'V4':>10}{'V4.1':>10}{'Δv41v4':>10}")
    print(f"{'Macro-F1':<16}{html_only['macro_f1']:>10.4f}{v4['macro_f1']:>10.4f}{m:>10.4f}{m-v4['macro_f1']:>+10.4f}")
    print(f"{'Pred products':<16}{html_only['pred_products']:>10}{v4['pred_products']:>10}{counts['pred_products']:>10}{counts['pred_products']-v4['pred_products']:>+10}")
    print(f"{'Matched':<16}{html_only['matched']:>10}{v4['matched']:>10}{counts['matched']:>10}{counts['matched']-v4['matched']:>+10}")
    print(f"{'Unmatched Gold':<16}{html_only['unmatched_gold']:>10}{v4['unmatched_gold']:>10}{counts['unmatched_gold']:>10}{counts['unmatched_gold']-v4['unmatched_gold']:>+10}")
    print(f"{'Unmatched Pred':<16}{html_only['unmatched_pred']:>10}{v4['unmatched_pred']:>10}{counts['unmatched_pred']:>10}{counts['unmatched_pred']-v4['unmatched_pred']:>+10}")

    print("\n7字段 P/R/F1 V4→V4.1 对比：")
    print(f"{'字段':<12}{'V4-F1':>8}{'V4.1-F1':>9}{'ΔF1':>9}{'V4-P':>8}{'V4.1-P':>9}{'V4-R':>8}{'V4.1-R':>9}")
    for en, cn, _ in FIELDS:
        s = stats[en]
        v4f = v4["fields"][cn]["F1"]
        print(f"{cn:<12}{v4f:>8.4f}{s['F1']:>9.4f}{s['F1']-v4f:>+9.4f}"
              f"{v4['fields'][cn]['Precision']:>8.4f}{s['Precision']:>9.4f}"
              f"{v4['fields'][cn]['Recall']:>8.4f}{s['Recall']:>9.4f}")

    print(f"\nV4.1 vs V4 ΔMacro: {m - v4['macro_f1']:+.4f}")
    print(f"V4.1 vs HTML-only ΔMacro: {m - html_only['macro_f1']:+.4f}")


if __name__ == "__main__":
    if "--evaluate" in sys.argv:
        do_evaluate()
    else:
        do_extract()
