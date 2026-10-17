"""V4 错误分析：逐条对比 Gold 与 V4 预测，按 14 类错误分类。

输出：
  - error_analysis_v4.csv  (sample_id, error_type, field, gold, prediction, reason)
  - error_analysis_v4_summary.json (各类错误出现次数 + TOP5)

不调用 Qwen，不修改 Gold / V4 输出 / 评估脚本 / 业务代码。
匹配口径与 evaluate_validation_50.py 完全一致（match_products_v2 三级匹配）。
"""
import csv
import json
import re
from pathlib import Path

from ai_extraction.evaluation.evaluate_goldset import (
    FIELDS, is_empty, fields_match, normalize_field, normalize_text,
)
from ai_extraction.evaluation.evaluate_goldset_v2 import (
    canonical_name, match_products_v2,
)

BASE = Path(__file__).parent.parent
GOLD_JSON = Path(
    r"E:\xwechat_files\wxid_tqvrrgep742c22_709a\msg\file\2026-09\validation_v0(1).json")
V4_DIR = BASE / "data" / "validation_attachment_test" / "run_v4_prompt_v22"
OUT_CSV = V4_DIR / "error_analysis_v4.csv"
OUT_SUMMARY = V4_DIR / "error_analysis_v4_summary.json"

ENUM_PREFIX = re.compile(r"^\s*\d+(?:[-.、]\d+)*\s*")
SPACE_ALL = re.compile(r"\s+")

# 项目级金额关键词（出现这些词的金额不能作为产品价格）
PROJECT_AMOUNT_KEYWORDS = [
    "项目总金额", "采购预算", "中标总金额", "最高限价", "投标报价总额",
    "报价合计", "包合计", "标段合计", "合同金额", "项目预算",
    "项目金额", "采购金额", "总价合计", "合计", "小计", "总计",
    "税额", "税费", "服务费", "保证金", "付款金额", "折扣",
]

# 表头/页脚/备注关键词
HEADER_FOOTER_KEYWORDS = [
    "项目名称", "采购内容", "招标内容", "采购包", "包组名称",
    "服务名称", "货物名称", "产品名称", "品目名称", "标的名称",
    "规格型号", "品牌", "单价", "数量", "总价", "金额",
    "序号", "备注", "说明", "合计", "小计",
]

# 服务实施/配置/要求关键词（不应作为独立产品）
SERVICE_CONTENT_KEYWORDS = [
    "实施要求", "技术要求", "服务要求", "配置说明", "售后要求",
    "项目说明", "实施方案", "验收标准", "服务内容", "技术参数",
    "配置项", "性能要求", "功能要求",
]

# 公司/厂家后缀（不应作为品牌）
COMPANY_SUFFIXES = ["公司", "厂", "供应商", "制造商", "集团", "有限", "股份"]

# 规格型号关键词（如果出现在 brand 字段，可能是分类错误）
SPEC_KEYWORDS_IN_BRAND = ["型号", "规格", "系列", "型", "款"]

# 产品名称关键词（如果出现在 spec_model 字段，可能是混淆）
NAME_KEYWORDS_IN_SPEC = ["服务", "设备", "系统", "工程", "项目", "维保", "维修", "保养"]


def read_gold():
    data = json.loads(GOLD_JSON.read_text(encoding="utf-8"))
    return {r["announcement_id"]: r.get("products", []) for r in data}


def read_pred():
    out, status, meta = {}, {}, {}
    for jf in sorted(V4_DIR.glob("t*.json")):
        r = json.loads(jf.read_text(encoding="utf-8"))
        aid = r.get("announcement_id", jf.stem)
        out[aid] = (r.get("result") or {}).get("products", []) or []
        status[aid] = r.get("status", "unknown")
        meta[aid] = {
            "input_kind": r.get("input_kind", ""),
            "api_errors": r.get("api_errors", 0),
            "api_calls_attempts": r.get("api_calls_attempts", 0),
            "api_calls_success": r.get("api_calls_success", 0),
            "json_errors": r.get("json_errors", 0),
            "elapsed_seconds": r.get("elapsed_seconds", 0),
        }
    return out, status, meta


def classify_text_diff(ai_val, gold_val):
    a = normalize_field(ai_val, False)
    g = normalize_field(gold_val, False)
    if a == g:
        return None
    if SPACE_ALL.sub("", a) == SPACE_ALL.sub("", g):
        return "格式差异"
    if ENUM_PREFIX.sub("", a) == ENUM_PREFIX.sub("", g):
        return "格式差异"
    if a in g or g in a:
        return "格式差异"
    return "值错误"


def has_keyword(text, keywords):
    if not text:
        return False
    t = str(text)
    return any(k in t for k in keywords)


def detect_price_binding_error(gold_products, pred_products, matched, aid):
    """检测 unit_price/quantity/total_price 绑定错误。

    启发式：
      1. 同一 pred 的 unit_price × quantity 明显不等于 total_price，但与另一 pred 的 total_price 相等
      2. 多个 pred 共享相同的 unit_price 或 total_price（可能从其他行借值）
    """
    errors = []
    # 收集所有 pred 的价格
    pred_prices = []
    for pi, pp in enumerate(pred_products):
        up = normalize_field(pp.get("unit_price"), True)
        qty = normalize_field(pp.get("quantity"), True)
        tp = normalize_field(pp.get("total_price"), True)
        pred_prices.append((pi, up, qty, tp))

    # 检测跨行借值：多个 pred 共享相同 unit_price 或 total_price
    up_counts = {}
    tp_counts = {}
    for pi, up, qty, tp in pred_prices:
        if up:
            up_counts.setdefault(up, []).append(pi)
        if tp:
            tp_counts.setdefault(tp, []).append(pi)
    shared_up = {v: pis for v, pis in up_counts.items() if len(pis) > 1}
    shared_tp = {v: pis for v, pis in tp_counts.items() if len(pis) > 1}

    # 检测 unit_price 与其他行的 total_price 相同（行错位）
    for pi, up, qty, tp in pred_prices:
        if not up:
            continue
        # unit_price 是否等于某个其他行的 total_price
        for pj, upj, qtyj, tpj in pred_prices:
            if pi == pj:
                continue
            if up and tpj and up == tpj:
                errors.append({
                    "pi": pi, "field": "unit_price",
                    "val": up, "reason": f"unit_price({up})与产品{pj}的total_price相同,疑似行间借值",
                })
                break
    return errors


def detect_project_amount_misclass(pred_products, project_amount):
    """检测项目级金额被误识别成商品价格。"""
    errors = []
    if not project_amount:
        return errors
    pa = normalize_field(project_amount, True)
    if not pa:
        return errors
    for pi, pp in enumerate(pred_products):
        up = normalize_field(pp.get("unit_price"), True)
        tp = normalize_field(pp.get("total_price"), True)
        if up and up == pa:
            errors.append({
                "pi": pi, "field": "unit_price",
                "val": up, "reason": f"unit_price({up})=project_amount({pa}),项目级金额误入产品价格",
            })
        if tp and tp == pa:
            errors.append({
                "pi": pi, "field": "total_price",
                "val": tp, "reason": f"total_price({tp})=project_amount({pa}),项目级金额误入产品价格",
            })
    return errors


def analyze_sample(aid, gold_products, pred_products, status, meta, project_amount):
    """分析单个样本，返回错误列表。"""
    rows = []
    matched = match_products_v2(aid, gold_products, pred_products)
    matched_g = {m[0] for m in matched}
    matched_p = {m[1] for m in matched}

    # 1. API 异常导致的样本不完整 (type 14)
    if status != "success" or meta.get("api_errors", 0) > 0:
        rows.append({
            "sample_id": aid, "error_type": "API异常导致样本不完整",
            "field": "ALL", "gold": "", "prediction": "",
            "reason": f"status={status}, api_errors={meta.get('api_errors',0)}, "
                      f"attempts={meta.get('api_calls_attempts',0)}, "
                      f"success={meta.get('api_calls_success',0)}",
        })

    # 2. 漏抽 (type 1) - unmatched gold
    for gi, gp in enumerate(gold_products):
        if gi in matched_g:
            continue
        if all(is_empty(gp.get(en)) for en, _, _ in FIELDS):
            continue
        gname = gp.get("product_service_name") or "(空名)"
        rows.append({
            "sample_id": aid, "error_type": "漏抽",
            "field": "product_service_name",
            "gold": gname, "prediction": "",
            "reason": f"Gold产品未匹配到Pred(漏抽)",
        })

    # 3. 过度抽取 + 子类型分类 (type 2, 9, 10, 11, 12)
    for pi, pp in enumerate(pred_products):
        if pi in matched_p:
            continue
        pname = pp.get("product_service_name") or "(空名)"
        up = pp.get("unit_price") or ""
        tp = pp.get("total_price") or ""
        qty = pp.get("quantity") or ""
        brand = pp.get("brand") or ""
        spec = pp.get("spec_model") or ""

        # 优先判断更具体的子类型
        # type 10: 重复抽取 - 名称与某个 matched pred 相同
        is_dup = False
        pname_norm = normalize_text(pname)
        for mj in matched:
            mp_name = normalize_text(pred_products[mj[1]].get("product_service_name") or "")
            if pname_norm and mp_name and pname_norm == mp_name:
                is_dup = True
                break
        # 也检查与其他 unmatched pred 重复
        if not is_dup:
            for pj2, pp2 in enumerate(pred_products):
                if pj2 == pi or pj2 in matched_p:
                    continue
                if pname_norm and pname_norm == normalize_text(pp2.get("product_service_name") or ""):
                    is_dup = True
                    break

        if is_dup:
            rows.append({
                "sample_id": aid, "error_type": "重复抽取",
                "field": "product_service_name", "gold": "",
                "prediction": pname,
                "reason": f"Pred产品名称与其他Pred重复,应去重",
            })
            continue

        # type 9: 表头/页脚/备注误抽
        if has_keyword(pname, HEADER_FOOTER_KEYWORDS) and len(pname) < 12:
            # 短名称且含表头词
            rows.append({
                "sample_id": aid, "error_type": "表头页脚备注误抽",
                "field": "product_service_name", "gold": "",
                "prediction": pname,
                "reason": f"产品名'{pname}'疑似表头/页脚/备注词",
            })
            continue

        # type 12: 服务内容被误识别为独立产品
        if has_keyword(pname, SERVICE_CONTENT_KEYWORDS):
            rows.append({
                "sample_id": aid, "error_type": "服务内容误识别为产品",
                "field": "product_service_name", "gold": "",
                "prediction": pname,
                "reason": f"产品名'{pname}'含服务实施/配置/要求关键词,疑似非独立采购标的",
            })
            continue

        # type 11: 项目级金额误识别成商品价格
        pa = normalize_field(project_amount, True) if project_amount else ""
        if pa and ((up and normalize_field(up, True) == pa) or
                   (tp and normalize_field(tp, True) == pa)):
            rows.append({
                "sample_id": aid, "error_type": "项目级金额误入产品价格",
                "field": "unit_price" if up else "total_price",
                "gold": "", "prediction": f"{pname}|price={up or tp}",
                "reason": f"产品价格({up or tp})=project_amount({pa}),项目级金额误入",
            })
            continue

        # 默认: 过度抽取
        rows.append({
            "sample_id": aid, "error_type": "过度抽取",
            "field": "product_service_name", "gold": "",
            "prediction": pname,
            "reason": f"Pred产品未匹配到Gold(过度抽取),price={up or tp or '空'}",
        })

    # 4. 已匹配但字段错误 - 分类
    for gi, pi, how in matched:
        gp, pp = gold_products[gi], pred_products[pi]
        gname = gp.get("product_service_name") or ""
        pname = pp.get("product_service_name") or ""
        gname_norm = normalize_text(gname)
        pname_norm = normalize_text(pname)

        # 检查各字段错误
        field_errors = []
        for en, cn, is_num in FIELDS:
            gv, av = gp.get(en), pp.get(en)
            ge, ae = is_empty(gv), is_empty(av)
            if ge and ae:
                continue
            if not ge and not ae:
                if not fields_match(av, gv, is_num):
                    field_errors.append((en, cn, is_num, gv, av))
            elif not ge and ae:
                field_errors.append((en, cn, is_num, gv, av, "miss_in_ai"))
            elif ge and not ae:
                field_errors.append((en, cn, is_num, gv, av, "extra_in_ai"))

        if not field_errors:
            continue

        # 分类该匹配对的错误类型
        # 优先级: 行错位 > 价格绑定 > 项目级金额 > brand误识别 > spec/name混淆 > 字段分类 > 实体边界 > 格式差异

        # type 5: 表格行错位 - order fallback 且多个字段错误
        if how == "order" and len(field_errors) >= 3:
            fields_str = ",".join(f[1] for f in field_errors)
            rows.append({
                "sample_id": aid, "error_type": "表格行错位",
                "field": fields_str,
                "gold": gname, "prediction": pname,
                "reason": f"order兜底匹配且{len(field_errors)}字段错误,疑似行错位",
            })
            continue

        # type 6: unit_price/quantity/total_price 绑定错误
        price_fields_err = [f for f in field_errors if f[0] in ("unit_price", "quantity", "total_price")]
        if len(price_fields_err) >= 2 and how == "order":
            fields_str = ",".join(f[1] for f in price_fields_err)
            rows.append({
                "sample_id": aid, "error_type": "价格字段绑定错误",
                "field": fields_str,
                "gold": gname, "prediction": pname,
                "reason": f"价格字段(unit/qty/total)多字段错误,疑似跨行绑定",
            })
            continue

        # type 11: 项目级金额误入产品价格（匹配对内）
        pa = normalize_field(project_amount, True) if project_amount else ""
        if pa:
            for f in field_errors:
                en, cn, is_num, gv, av = f[0], f[1], f[2], f[3], f[4]
                if en in ("unit_price", "total_price"):
                    av_norm = normalize_field(av, True)
                    if av_norm == pa:
                        rows.append({
                            "sample_id": aid, "error_type": "项目级金额误入产品价格",
                            "field": cn, "gold": str(gv), "prediction": str(av),
                            "reason": f"产品价格({av})=project_amount({pa})",
                        })
                        break
            else:
                pass  # 没命中项目金额，继续往下分类

        # 逐字段分类剩余错误
        for f in field_errors:
            en, cn, is_num = f[0], f[1], f[2]
            gv = f[3] if len(f) > 3 else ""
            av = f[4] if len(f) > 4 else ""
            extra = f[5] if len(f) > 5 else None

            # 跳过已在上层处理的
            if extra is None and len(f) == 5:
                gv, av = f[3], f[4]

            # type 8: brand 误识别
            if en == "brand" and av and has_keyword(av, COMPANY_SUFFIXES):
                rows.append({
                    "sample_id": aid, "error_type": "品牌误识别",
                    "field": cn, "gold": str(gv), "prediction": str(av),
                    "reason": f"brand'{av}'含公司/厂家后缀,疑似误识别",
                })
                continue

            # type 4: 字段分类错误 - brand 含规格关键词 或 spec 含品牌
            if en == "brand" and has_keyword(av, SPEC_KEYWORDS_IN_BRAND) and not has_keyword(av, COMPANY_SUFFIXES):
                rows.append({
                    "sample_id": aid, "error_type": "字段分类错误",
                    "field": cn, "gold": str(gv), "prediction": str(av),
                    "reason": f"brand'{av}'含规格型号关键词,疑似分类错误",
                })
                continue

            # type 7: spec_model 与 product name 混淆
            if en == "spec_model" and av and has_keyword(av, NAME_KEYWORDS_IN_SPEC):
                rows.append({
                    "sample_id": aid, "error_type": "规格型号与产品名混淆",
                    "field": cn, "gold": str(gv), "prediction": str(av),
                    "reason": f"spec_model'{av}'含产品名关键词,疑似混淆",
                })
                continue
            if en == "product_service_name" and av:
                # 检查是否 product name 实际是 spec
                if has_keyword(av, SPEC_KEYWORDS_IN_BRAND) and not has_keyword(av, NAME_KEYWORDS_IN_SPEC):
                    rows.append({
                        "sample_id": aid, "error_type": "规格型号与产品名混淆",
                        "field": cn, "gold": str(gv), "prediction": str(av),
                        "reason": f"product_name'{av}'含规格关键词,疑似混淆",
                    })
                    continue

            # type 3: 实体边界错误 - 产品名值错误（非格式差异）
            if en == "product_service_name" and av and gv:
                diff = classify_text_diff(av, gv)
                if diff == "值错误":
                    rows.append({
                        "sample_id": aid, "error_type": "实体边界错误",
                        "field": cn, "gold": str(gv), "prediction": str(av),
                        "reason": f"产品名值错误(gold='{gv}' vs pred='{av}'),疑似实体边界判断差异",
                    })
                    continue
                elif diff == "格式差异":
                    rows.append({
                        "sample_id": aid, "error_type": "格式差异",
                        "field": cn, "gold": str(gv), "prediction": str(av),
                        "reason": f"产品名格式差异(gold='{gv}' vs pred='{av}')",
                    })
                    continue

            # type 6: 价格字段值错误（匹配对内的价格错误）
            if en in ("unit_price", "total_price", "quantity") and av and gv:
                # 检查是否 unit_price 与 total_price 互换
                if en == "unit_price":
                    tp_pred = normalize_field(pp.get("total_price"), True)
                    gv_norm = normalize_field(gv, True)
                    if tp_pred and gv_norm and tp_pred == gv_norm:
                        rows.append({
                            "sample_id": aid, "error_type": "价格字段绑定错误",
                            "field": cn, "gold": str(gv), "prediction": str(av),
                            "reason": f"unit_price({av})与total_price({tp_pred})疑似互换",
                        })
                        continue
                rows.append({
                    "sample_id": aid, "error_type": "价格字段绑定错误",
                    "field": cn, "gold": str(gv), "prediction": str(av),
                    "reason": f"{cn}值错误(gold='{gv}' vs pred='{av}')",
                })
                continue

            # 通用字段错误分类
            if extra == "miss_in_ai":
                # Gold有值但Pred空 = 字段级Recall问题
                rows.append({
                    "sample_id": aid, "error_type": "字段漏值",
                    "field": cn, "gold": str(gv), "prediction": "",
                    "reason": f"{cn}Gold有值Pred漏值(gold='{gv}')",
                })
            elif extra == "extra_in_ai":
                rows.append({
                    "sample_id": aid, "error_type": "过度抽取",
                    "field": cn, "gold": "", "prediction": str(av),
                    "reason": f"{cn}Gold空但Pred多值(pred='{av}')",
                })
            else:
                diff = classify_text_diff(av, gv) if not is_num else "值错误"
                etype = "格式差异" if diff == "格式差异" else "字段值错误"
                rows.append({
                    "sample_id": aid, "error_type": etype,
                    "field": cn, "gold": str(gv), "prediction": str(av),
                    "reason": f"{cn}值错误(gold='{gv}' vs pred='{av}')",
                })

    # 5. 检测价格绑定错误（跨产品）
    price_bind_errors = detect_price_binding_error(gold_products, pred_products, matched, aid)
    for e in price_bind_errors:
        pi = e["pi"]
        if pi in matched_p:
            pp = pred_products[pi]
            rows.append({
                "sample_id": aid, "error_type": "价格字段绑定错误",
                "field": e["field"],
                "gold": "", "prediction": str(pp.get("product_service_name") or ""),
                "reason": e["reason"],
            })

    # 6. 检测项目级金额误入（所有 pred，含 matched）
    pa_errors = detect_project_amount_misclass(pred_products, project_amount)
    for e in pa_errors:
        pi = e["pi"]
        if pi in matched_p:
            rows.append({
                "sample_id": aid, "error_type": "项目级金额误入产品价格",
                "field": e["field"],
                "gold": "", "prediction": str(pred_products[pi].get("product_service_name") or ""),
                "reason": e["reason"],
            })

    # 7. 检测重复抽取（matched 内重复名称）
    name_to_pi = {}
    for pi, pp in enumerate(pred_products):
        nm = normalize_text(pp.get("product_service_name") or "")
        if not nm:
            continue
        if nm in name_to_pi:
            rows.append({
                "sample_id": aid, "error_type": "重复抽取",
                "field": "product_service_name",
                "gold": "", "prediction": str(pp.get("product_service_name") or ""),
                "reason": f"Pred内产品名重复(name='{nm}')",
            })
        else:
            name_to_pi[nm] = pi

    return rows


def main():
    gold_by_aid = read_gold()
    pred_by_aid, pred_status, pred_meta = read_pred()

    all_rows = []
    sample_errors = {}

    for aid in sorted(gold_by_aid):
        gold_products = gold_by_aid[aid]
        pred_products = pred_by_aid.get(aid, [])
        status = pred_status.get(aid, "unknown")
        meta = pred_meta.get(aid, {})

        # 获取 project_amount（从 pred 的 result 中）
        project_amount = None
        v4_file = V4_DIR / f"{aid}.json"
        if v4_file.exists():
            r = json.loads(v4_file.read_text(encoding="utf-8"))
            project_amount = (r.get("result") or {}).get("project_amount")

        rows = analyze_sample(aid, gold_products, pred_products, status, meta, project_amount)
        all_rows.extend(rows)
        sample_errors[aid] = len(rows)

    # 写 CSV
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sample_id", "error_type", "field", "gold", "prediction", "reason"])
        for r in all_rows:
            w.writerow([r["sample_id"], r["error_type"], r["field"],
                        r["gold"], r["prediction"], r["reason"]])

    # 统计摘要
    type_counts = {}
    for r in all_rows:
        t = r["error_type"]
        type_counts[t] = type_counts.get(t, 0) + 1

    # 按次数排序
    sorted_types = sorted(type_counts.items(), key=lambda x: -x[1])
    top5 = sorted_types[:5]

    # 按样本统计
    sample_summary = {}
    for aid, n in sample_errors.items():
        sample_summary[aid] = n

    summary = {
        "total_error_instances": len(all_rows),
        "total_samples": len(gold_by_aid),
        "samples_with_errors": sum(1 for n in sample_errors.values() if n > 0),
        "error_type_counts": dict(sorted_types),
        "top5_errors": [{"error_type": t, "count": c} for t, c in top5],
        "per_sample_error_count": sample_summary,
    }
    with open(OUT_SUMMARY, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # 控制台报告
    print("===== V4 错误分析摘要 =====\n")
    print(f"总错误实例数: {len(all_rows)}")
    print(f"样本数: {len(gold_by_aid)}")
    print(f"有错误样本数: {sum(1 for n in sample_errors.values() if n > 0)}\n")

    print("各类错误出现次数（降序）:")
    for t, c in sorted_types:
        print(f"  {t:<24} {c}")

    print(f"\nTOP 5 错误类型:")
    for t, c in top5:
        print(f"  {t}: {c}")

    print(f"\nCSV: {OUT_CSV}")
    print(f"Summary: {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
