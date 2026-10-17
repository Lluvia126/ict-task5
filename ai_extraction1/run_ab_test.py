"""A/B Qwen 实验：原始清洗文本 (A) vs 表格结构预处理文本 (B)。

样本：structure_regression_summary.json 中 split_rows>0 的 11 个公告。
两组条件完全一致：
  - 模型 qwen-vl-max、V1 Prompt、temperature=0、chunk_text(4000)
  - 合并逻辑复用 run_baseline._merge_chunk_results
仅输入不同：
  A: cleaner.preprocess_text(html)        —— 当前 V1 口径
  B: html_table_preprocessor.preprocess_html_tables(html) 结构化文本

不修改 qwen_extractor.py / config.py / V1 / V1.1 / V2 / Gold。
单文件失败不影响整体；不做全量处理。
"""
import csv
import json
import re
import time
from collections import Counter
from pathlib import Path

from ai_extraction.preprocessing.cleaner import preprocess_text
from ai_extraction.preprocessing.chunker import chunk_text
from ai_extraction.preprocessing.html_table_preprocessor import (
    parse_announcement, preprocess_html_tables,
)
from ai_extraction.extractors.qwen_extractor import QwenExtractor
from ai_extraction.run_baseline import _merge_chunk_results
from ai_extraction.evaluation.evaluate_goldset_v2 import (
    read_gold_full, evaluate_version,
)

BASE = Path(__file__).parent
VAL_DIR = BASE / "data" / "validation"
REGRESSION_JSON = BASE / "data" / "table_test" / "structure_regression_summary.json"
AB_DIR = BASE / "data" / "ab_test"
A_DIR = AB_DIR / "v1_raw"
B_DIR = AB_DIR / "v1_table_preprocessed"

LIST_PUNCT = re.compile(r"[、；;\n]")
UNCLOSED_PAREN = re.compile(r"[（(][^)）]*$")


# ---------------------------------------------------------------- 队列

def read_cohort():
    s = json.loads(REGRESSION_JSON.read_text(encoding="utf-8"))
    return [r["announcement_id"]
            for r in s["parallel_files"] if r["split_rows"] > 0]


# ---------------------------------------------------------------- 单组运行

def _extract_one(chunk, ann, tag, source_file, extractor):
    return extractor.extract(chunk, announcement_id=tag, file_id="",
                             source_file=source_file)


def _extract_adaptive(chunk, ann, idx, source_file, extractor,
                      depth=0, retry=1):
    """抽取一个 chunk；失败时：
      1. 先立即重试 retry 次（截断位置不稳定，重试常成功）；
      2. 截断时沿 宗/子项 结构边界继续拆，直到单个子项。
    对 A/B 对称生效的实验护栏，不改核心代码。"""
    try:
        return [_extract_one(chunk, ann, f"{ann}_c{idx}", source_file,
                             extractor)]
    except RuntimeError as e:
        msg = str(e)
        # 输出被切断时，JSON 错误形态不固定
        truncated = any(sig in msg for sig in (
            "Unterminated", "Expecting property name",
            "Expecting ',' delimiter", "Expecting value"))
        if retry > 0:  # 先重试（截断/瞬时均适用）
            return _extract_adaptive(chunk, ann, idx, source_file, extractor,
                                     depth, retry - 1)
        if depth < 5:
            parts = _split_chunk_structural(chunk)
            if parts:
                out, errs = [], []
                for j, part in enumerate(parts):
                    try:
                        out.extend(_extract_adaptive(
                            part, ann, f"{idx}.{j}", source_file, extractor,
                            depth + 1))
                    except Exception as e2:
                        errs.append(e2)
                if out:  # 保住成功的部分
                    if errs:
                        print(f"[adaptive] {ann} 部分子块仍失败，"
                              f"保留 {len(out)} 个成功结果")
                    return out
                raise errs[0]
        raise


def _split_chunk_structural(chunk):
    """沿结构边界把 chunk 拆成 2 份，尽量不切断条目。返回 [p1, p2] 或 None。"""
    # 1) 宗/包 边界
    mks = [m.start() for m in re.finditer(r"【宗/包】", chunk)]
    if len(mks) >= 2:
        cut = mks[len(mks) // 2]
        return [chunk[:cut].strip(), chunk[cut:].strip()]
    # 2) 子项边界（结构化小块，宗头随每部分携带）
    hpos = chunk.find("【子项")
    if hpos >= 0:
        header = chunk[:hpos]
        starts = [hpos + m.start() for m in re.finditer(r"【子项", chunk[hpos:])]
        if len(starts) >= 2:
            cut = starts[len(starts) // 2]
            p1 = chunk[:cut].strip()
            p2 = (header + chunk[cut:]).strip()
            return [p1, p2]
    # 3) 纯文本（A 组原始输入）：就近换行对半
    cut = len(chunk) // 2
    nl = chunk.rfind("\n", cut - 800, cut + 800)
    if nl > 0:
        cut = nl
    if 0 < cut < len(chunk):
        return [chunk[:cut].strip(), chunk[cut:].strip()]
    return None


def structured_chunks(text, items_per_call=10, max_chars=3600):
    """B 组专用：按 宗/子项 结构边界切块，不在条目中间断开。

    每块 ≤ items_per_call 个子项、≤ max_chars 字符；
    宗头部（宗号/品目/标的/数量）随每块携带，保留上下文。
    """
    lots = re.split(r"(?=【宗/包】)", text)
    chunks = []
    for lot in lots:
        lot = lot.strip()
        if not lot:
            continue
        mk = re.search(r"【子项", lot)
        header, body = (lot[:mk.start()], lot[mk.start():]) if mk else (lot, "")
        blocks = re.findall(r"【子项.*?(?=【子项|$)", body, flags=re.S)
        if not blocks:  # 无 子项 标记（不应出现），整体一块
            chunks.append(lot)
            continue
        cur = ""
        n = 0
        for blk in blocks:
            blk = blk.strip()
            piece = (header + "\n" + blk) if not cur else blk
            if (n >= items_per_call or len(cur) + len(blk) + 1 > max_chars) \
                    and cur:
                chunks.append(cur.strip())
                cur, n = "", 0
                piece = header + "\n" + blk
            cur = (cur + "\n" + piece) if cur else piece
            n += 1
        if cur:
            chunks.append(cur.strip())
    return chunks


def run_arm(ann, source_file, text, extractor, chunker=chunk_text):
    """对一个公告的一种输入跑完整 V1 流程，返回 record。"""
    rec = {"announcement_id": ann, "source_file": source_file,
           "chunks_count": 0, "api_calls": 0, "elapsed_seconds": 0.0,
           "status": "success", "error": None, "result": None}
    t0 = time.time()
    try:
        if not text.strip():
            rec["status"] = "failed"
            rec["error"] = "输入文本为空"
            return rec
        chunks = chunker(text)
        rec["chunks_count"] = len(chunks)
        chunk_results = []
        for idx, chunk in enumerate(chunks):
            try:
                rs = _extract_adaptive(chunk, ann, idx, source_file, extractor)
                chunk_results.extend(rs)
                rec["api_calls"] += len(rs)
            except Exception as e:
                rec["status"] = "partial" if chunk_results else "failed"
                rec["error"] = (rec["error"] + "; " if rec["error"] else "") + \
                               f"chunk[{idx}]: {type(e).__name__}: {e}"
        if chunk_results:
            rec["result"] = _merge_chunk_results(
                chunk_results, ann, "", source_file)
        else:
            rec["status"] = "failed"
    except Exception as e:
        rec["status"] = "failed"
        rec["error"] = f"{type(e).__name__}: {e}"
    finally:
        rec["elapsed_seconds"] = round(time.time() - t0, 2)
    return rec


def save_record(rec, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{rec['announcement_id']}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 结构质量分析

def expected_structure(html, ann):
    """预处理器给出的结构基准（机械可核验）。

    返回 (known_pairs, brands, uncertain_rows)：
      known_pairs: split 子项 + normal 行（品牌,型号）已知的 1:1 对
      uncertain_rows: keep_raw 行的原始 (品牌串, 规格串)，该区不判错
    """
    parsed = parse_announcement(html, announcement_id=ann)
    pairs, brands, uncertain = [], set(), []
    for t in parsed["tables"]:
        for r in t["rows"]:
            d = r.get("decision")
            if d == "split":
                for it in r.get("sub_items", []):
                    pairs.append((it.get("brand"), it.get("spec_model")))
                    if it.get("brand"):
                        brands.add(it["brand"])
            elif d == "normal":
                rc = r.get("raw_columns", {})
                b, sp = rc.get("品牌"), rc.get("规格型号")
                pairs.append((b or None, sp or None))
                if b:
                    brands.add(b)
            elif d == "keep_raw":
                rc = r.get("raw_columns", {})
                uncertain.append((rc.get("品牌", ""), rc.get("规格型号", "")))
    return pairs, brands, uncertain


def analyze_arm(expected_pairs, brands, uncertain_rows, products):
    """以预处理器结构为基准，做机械级问题计数（全部为"疑似"，供人工复核）。"""
    exp_counter = Counter(expected_pairs)
    pred_counter = Counter((p.get("brand"), p.get("spec_model"))
                           for p in products)

    issues = {
        "brand_string_in_name": 0,     # 品牌串进产品名称
        "unmerged_brand_cell": 0,      # 品牌单元格仍是列表 → 未拆分/合并
        "unmerged_spec_cell": 0,       # 规格单元格仍是列表
        "brand_spec_misaligned": 0,    # 已知品牌挂到非其型号 → 疑似错位
        "spec_truncated": 0,           # 型号疑似截断（未闭合括号等）
        "wrong_merge": 0,              # 一条产品含多个品牌/型号
        "wrong_split": 0,              # 已知区产品数超出期望 → 疑似误拆
        "missing_products": 0,         # 已知区少于期望 → 疑似漏拆/漏抽
        "duplicate_lost": 0,           # 期望重复对未保留
        "uncertain_zone": 0,           # 落在 keep_raw 区，不判错
        "n_products": len(products),
        "n_expected": len(expected_pairs),
        "examples": [],
    }

    def in_uncertain(p):
        brand = p.get("brand") or ""
        spec = p.get("spec_model") or ""
        for rb, rs in uncertain_rows:
            if (brand and brand in (rb or "")) or (spec and spec in (rs or "")):
                return True
        return False

    def flag(kind, p, detail):
        issues[kind] += 1
        if len(issues["examples"]) < 15:
            issues["examples"].append(
                {"issue": kind, "detail": detail,
                 "name": p.get("product_service_name"),
                 "brand": p.get("brand"), "spec": p.get("spec_model")})

    known_zone = 0
    for p in products:
        name = p.get("product_service_name") or ""
        brand = p.get("brand") or ""
        spec = p.get("spec_model") or ""
        if in_uncertain(p):
            issues["uncertain_zone"] += 1
            continue
        known_zone += 1
        # 1) 品牌串入名称
        hit = next((b for b in brands if b and b in name), None)
        if hit and len(name) > len(hit):
            flag("brand_string_in_name", p, f"名称含品牌「{hit}」")
        # 2) 列表未拆开
        if brand and LIST_PUNCT.search(brand):
            flag("unmerged_brand_cell", p, "品牌单元格仍含列表分隔符")
        if spec and LIST_PUNCT.search(spec):
            flag("unmerged_spec_cell", p, "规格单元格仍含列表分隔符")
        # 3) 错位：品牌已知，但 (品牌,型号) 不在期望多重集合中
        key = (p.get("brand"), p.get("spec_model"))
        if brand and spec and brand in brands and key not in exp_counter:
            flag("brand_spec_misaligned", p,
                 f"已知品牌 {brand} 挂到非对齐型号 {spec[:30]}")
        # 4) 截断
        if spec and UNCLOSED_PAREN.search(spec):
            flag("spec_truncated", p, "规格以未闭合括号结尾")
        # 5) 合并：一条记录里出现多个品牌/型号段
        if (brand and len([x for x in re.split(r"[、；;]", brand) if x.strip()]) > 1) \
                or (spec and len([x for x in re.split(r"[、；;]", spec) if x.strip()]) > 1):
            flag("wrong_merge", p, "单条产品含多个品牌/型号段")

    # 多重性比较（重复保留）
    for key, m in exp_counter.items():
        got = pred_counter.get(key, 0)
        if got < m:
            issues["duplicate_lost"] += m - got
    # 已知区数量比较（误拆/漏拆）
    if known_zone > len(expected_pairs):
        issues["wrong_split"] = known_zone - len(expected_pairs)
    elif known_zone < len(expected_pairs):
        issues["missing_products"] = len(expected_pairs) - known_zone
    return issues


# ---------------------------------------------------------------- Gold

def gold_evaluation(a_records, b_records):
    gold = read_gold_full()
    cohort = {r["announcement_id"] for r in a_records}
    gold_subset = [g for g in gold if g["announcement_id"] in cohort]

    def pred_dict(records):
        return {r["announcement_id"]:
                (r["result"]["products"] if r["result"] else [])
                for r in records}

    a_stats, a_counts = evaluate_version(gold_subset, pred_dict(a_records))
    b_stats, b_counts = evaluate_version(gold_subset, pred_dict(b_records))
    return gold_subset, a_stats, a_counts, b_stats, b_counts


def macro(stats):
    return round(sum(s["F1"] for s in stats.values()) / len(stats), 4)


# ---------------------------------------------------- 宗级列表对齐评估

def _tokens(s):
    return [x.strip() for x in re.split(r"[、；;\n]+", str(s or ""))
            if x.strip()]


def _norm(s):
    return re.sub(r"\s+", "", str(s or "")).lower()


def _multiset_match(gold_items, pred_items):
    """token 多重集匹配：精确→子串包含。返回 TP。"""
    g = list(gold_items)
    p = list(pred_items)
    tp = 0
    # Pass 1 精确（规范化）
    for j, pv in list(enumerate(p)):
        pn = _norm(pv)
        if not pn:
            continue
        for i, gv in enumerate(g):
            if gv is not None and pn == _norm(gv):
                tp += 1
                g[i] = None
                p[j] = None
                break
    # Pass 2 子串包含（单向）
    for j, pv in list(enumerate(p)):
        pn = _norm(pv)
        if not pn:
            continue
        for i, gv in enumerate(g):
            gn = _norm(gv)
            if gn and (pn in gn or gn in pn) and \
                    min(len(pn), len(gn)) / max(len(pn), len(gn)) > 0.5:
                tp += 1
                g[i] = None
                p[j] = None
                break
    return tp, len(pred_items), len(gold_items)


def lot_aware_eval_file(gold_products, pred_products):
    """对一个公告做"宗级列表对齐"评估。

    Gold 中以 list 形式给出品牌/型号的记录视为一"宗"；
    pred 无论是宗级单条（字段内含列表）还是拆分子项，
    统一在 token 级比较，不把结构差异强行计错。
    """
    lots = []
    for gp in gold_products:
        gb, gs = _tokens(gp.get("brand")), _tokens(gp.get("spec_model"))
        if gb or gs:
            lots.append({"gold": gp, "gb": gb, "gs": gs,
                         "pb": [], "ps": []})

    pool = list(pred_products)
    # 1) 宗级记录：名称对齐 + 字段含分隔符
    for lot in lots:
        gname = _norm(lot["gold"].get("product_service_name"))
        for i, pp in enumerate(pool):
            if not pp or not gname:
                continue
            pn = _norm(pp.get("product_service_name"))
            if not pn:
                continue
            if (pn == gname or pn.startswith(gname)) and (
                    LIST_PUNCT.search(str(pp.get("brand") or ""))
                    or LIST_PUNCT.search(str(pp.get("spec_model") or ""))):
                lot["pb"].extend(_tokens(pp.get("brand")))
                lot["ps"].extend(_tokens(pp.get("spec_model")))
                lot["lot_record"] = pp
                pool[i] = None
                break

    # 2) 拆分子项：按 (品牌,型号) 对精确归属
    pair_map = {}
    for li, lot in enumerate(lots):
        for b, s in zip(lot["gb"], lot["gs"]):
            pair_map.setdefault((_norm(b), _norm(s)), []).append(li)
    rem = []
    for pp in pool:
        if pp is None:
            continue
        key = (_norm(pp.get("brand")), _norm(pp.get("spec_model")))
        if key in pair_map and pair_map[key]:
            li = pair_map[key].pop(0)
            lots[li]["pb"].append(pp.get("brand"))
            lots[li]["ps"].append(pp.get("spec_model"))
        else:
            rem.append(pp)

    # 3) 剩余：型号/品牌单端归属，否则全局 FP
    for pp in rem:
        b, s = pp.get("brand"), pp.get("spec_model")
        target = None
        if s:
            sn = _norm(s)
            for lot in lots:
                if any(sn and (sn == _norm(g) or sn in _norm(g))
                       for g in lot["gs"]):
                    target = lot
                    break
        if target is None and b:
            bn = _norm(b)
            for lot in lots:
                if bn in [_norm(g) for g in lot["gb"]]:
                    target = lot
                    break
        if target is not None:
            target["pb"].append(b)
            target["ps"].append(s)

    # 4) 汇总
    from ai_extraction.evaluation.evaluate_goldset import FIELDS
    stats = {en: {"TP": 0, "n_pred": 0, "n_gold": 0}
             for en, _, _ in FIELDS}
    for lot in lots:
        for en, pred_items, gold_items in (
                ("brand", lot["pb"], lot["gb"]),
                ("spec_model", lot["ps"], lot["gs"])):
            tp, np_, ng = _multiset_match(gold_items, pred_items)
            stats[en]["TP"] += tp
            stats[en]["n_pred"] += np_
            stats[en]["n_gold"] += ng
        gp = lot["gold"]
        lr = lot.get("lot_record")

        # product name / category / quantity：宗级记录或子项覆盖
        for en in ("product_service_name", "category", "quantity"):
            gv = gp.get(en)
            if gv is None or str(gv).strip() == "":
                continue
            stats[en]["n_gold"] += 1
            ok = False
            if lr is not None:
                pn = _norm(lr.get(en))
                gn = _norm(gv)
                ok = bool(pn) and (pn == gn or pn.startswith(gn)
                                   or gn.startswith(pn))
            if not ok:
                # 子项中查找（pair 归属过的）
                for pp in pred_products:
                    if _norm(pp.get(en)) == _norm(gv):
                        ok = True
                        break
            if ok:
                stats[en]["TP"] += 1
                stats[en]["n_pred"] += 1
        # 单价/总价 gold 为空：pred 非空 → FP（token 计数体现在 n_pred）
        for en in ("unit_price", "total_price"):
            vals = [pp.get(en) for pp in pred_products
                    if pp.get(en) is not None]
            stats[en]["n_pred"] += len(vals)

    for en, _, is_num in FIELDS:
        s = stats[en]
        tp, np_, ng = s["TP"], s["n_pred"], s["n_gold"]
        fp, fn = max(0, np_ - tp), max(0, ng - tp)
        s["FP"], s["FN"] = fp, fn
        s["Precision"] = round(tp / np_, 4) if np_ else 0.0
        s["Recall"] = round(tp / ng, 4) if ng else 0.0
        pr = s["Precision"] + s["Recall"]
        s["F1"] = round(2 * s["Precision"] * s["Recall"] / pr, 4) if pr else 0.0
    return stats


def lot_aware_evaluation(a_records, b_records):
    out = {}
    for r_a, r_b in zip(a_records, b_records):
        ann = r_a["announcement_id"]
        # 是否含宗级列表 Gold
        from ai_extraction.evaluation.evaluate_goldset_v2 import read_gold_full
        golds = [g for g in read_gold_full()
                 if g["announcement_id"] == ann]
        if not any(_tokens(g.get("brand")) or _tokens(g.get("spec_model"))
                   for g in golds):
            continue
        pa = r_a["result"]["products"] if r_a["result"] else []
        pb = r_b["result"]["products"] if r_b["result"] else []
        out[ann] = {"A": lot_aware_eval_file(golds, pa),
                    "B": lot_aware_eval_file(golds, pb)}
    return out


# ---------------------------------------------------------------- main

def main():
    cohort = read_cohort()
    print(f"A/B 样本：{len(cohort)} 个公告")
    A_DIR.mkdir(parents=True, exist_ok=True)
    B_DIR.mkdir(parents=True, exist_ok=True)
    extractor = QwenExtractor()

    a_records, b_records, per_file = [], [], []
    structures = {}
    total_t0 = time.time()

    for i, ann in enumerate(cohort, 1):
        fp = VAL_DIR / f"{ann}.html"
        html = fp.read_text(encoding="utf-8", errors="ignore")
        print(f"\n[{i}/{len(cohort)}] {ann}")

        exp_pairs, brands, uncertain_rows = expected_structure(html, ann)
        structures[ann] = exp_pairs

        text_a = preprocess_text(html)
        text_b, _lots = preprocess_html_tables(html)

        # 断点续跑：已成功的记录直接复用
        fa_, fb_ = A_DIR / f"{ann}.json", B_DIR / f"{ann}.json"
        if fa_.exists():
            rec_a = json.loads(fa_.read_text(encoding="utf-8"))
            if rec_a.get("status") != "success":
                rec_a = run_arm(ann, fp.name, text_a, extractor)
                save_record(rec_a, A_DIR)
        else:
            rec_a = run_arm(ann, fp.name, text_a, extractor)
            save_record(rec_a, A_DIR)
        if fb_.exists():
            rec_b = json.loads(fb_.read_text(encoding="utf-8"))
            if rec_b.get("status") != "success":
                rec_b = run_arm(ann, fp.name, text_b, extractor,
                                 chunker=structured_chunks)
                save_record(rec_b, B_DIR)
        else:
            rec_b = run_arm(ann, fp.name, text_b, extractor,
                            chunker=structured_chunks)
            save_record(rec_b, B_DIR)
        a_records.append(rec_a)
        b_records.append(rec_b)

        pa = rec_a["result"]["products"] if rec_a["result"] else []
        pb = rec_b["result"]["products"] if rec_b["result"] else []
        iss_a = analyze_arm(exp_pairs, brands, uncertain_rows, pa)
        iss_b = analyze_arm(exp_pairs, brands, uncertain_rows, pb)
        per_file.append({
            "announcement_id": ann,
            "A_products": len(pa), "B_products": len(pb),
            "expected_products": len(exp_pairs),
            "A_status": rec_a["status"], "B_status": rec_b["status"],
            "A_elapsed_seconds": rec_a["elapsed_seconds"],
            "B_elapsed_seconds": rec_b["elapsed_seconds"],
            "A_chunks": rec_a["chunks_count"], "B_chunks": rec_b["chunks_count"],
            "A_issues": iss_a, "B_issues": iss_b,
        })
        print(f"  A: {rec_a['status']} {rec_a['elapsed_seconds']}s "
              f"products={len(pa)}")
        print(f"  B: {rec_b['status']} {rec_b['elapsed_seconds']}s "
              f"products={len(pb)} (期望 {len(exp_pairs)})")

    total_elapsed = round(time.time() - total_t0, 2)

    # Gold 评估
    gold_subset, a_stats, a_counts, b_stats, b_counts = gold_evaluation(
        a_records, b_records)
    gold_aids = sorted({g["announcement_id"] for g in gold_subset})

    # 宗级列表对齐评估（结构差异不强行计错）
    lot_aware = lot_aware_evaluation(a_records, b_records)

    def field_block(stats):
        from ai_extraction.evaluation.evaluate_goldset import FIELDS
        return {cn: stats[en] for en, cn, _ in FIELDS}

    # 聚合结构问题
    def sum_issues(arm_key, kind):
        return sum(p[f"{arm_key}_issues"][kind] for p in per_file)

    aggregate = {
        "n_files": len(cohort),
        "A_total_products": sum(p["A_products"] for p in per_file),
        "B_total_products": sum(p["B_products"] for p in per_file),
        "expected_total_products": sum(p["expected_products"] for p in per_file),
        "A_success": sum(1 for p in per_file if p["A_status"] == "success"),
        "B_success": sum(1 for p in per_file if p["B_status"] == "success"),
        "A_avg_elapsed": round(
            sum(p["A_elapsed_seconds"] for p in per_file) / len(per_file), 2),
        "B_avg_elapsed": round(
            sum(p["B_elapsed_seconds"] for p in per_file) / len(per_file), 2),
        "A_total_chunks": sum(p["A_chunks"] for p in per_file),
        "B_total_chunks": sum(p["B_chunks"] for p in per_file),
        "A": {k: sum_issues("A", k) for k in (
            "brand_string_in_name", "unmerged_brand_cell",
            "unmerged_spec_cell", "brand_spec_misaligned",
            "spec_truncated", "wrong_merge", "wrong_split",
            "missing_products", "duplicate_lost", "uncertain_zone")},
        "B": {k: sum_issues("B", k) for k in (
            "brand_string_in_name", "unmerged_brand_cell",
            "unmerged_spec_cell", "brand_spec_misaligned",
            "spec_truncated", "wrong_merge", "wrong_split",
            "missing_products", "duplicate_lost", "uncertain_zone")},
    }

    summary = {
        "note": "11个真实并行列表公告上的A/B实验，非官方比赛最终得分",
        "model": "qwen-vl-max (temperature=0, V1 Prompt, chunk=4000)",
        "total_elapsed_seconds": total_elapsed,
        "aggregate": aggregate,
        "per_file": per_file,
        "gold_evaluation": {
            "gold_announcements": gold_aids,
            "gold_records": len(gold_subset),
            "A": {"macro_f1": macro(a_stats), "fields": field_block(a_stats),
                  "matched": a_counts["matched"],
                  "true_miss": a_counts["true_miss"],
                  "true_extra": a_counts["true_extra"],
                  "total_pred": a_counts["total_pred"]},
            "B": {"macro_f1": macro(b_stats), "fields": field_block(b_stats),
                  "matched": b_counts["matched"],
                  "true_miss": b_counts["true_miss"],
                  "true_extra": b_counts["true_extra"],
                  "total_pred": b_counts["total_pred"]},
        },
        "gold_lot_aligned_evaluation": {
            aid: {"A": {"macro_f1": macro(la["A"]),
                         "fields": field_block(la["A"])},
                   "B": {"macro_f1": macro(la["B"]),
                         "fields": field_block(la["B"])}}
            for aid, la in lot_aware.items()},
    }
    AB_DIR.mkdir(parents=True, exist_ok=True)
    (AB_DIR / "ab_test_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # CSV
    with open(AB_DIR / "ab_test_summary.csv", "w", encoding="utf-8-sig",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["announcement_id", "A_products", "B_products",
                    "expected_products", "A_status", "B_status",
                    "A_elapsed_seconds", "B_elapsed_seconds",
                    "A_chunks", "B_chunks"])
        for p in per_file:
            w.writerow([p["announcement_id"], p["A_products"], p["B_products"],
                        p["expected_products"], p["A_status"], p["B_status"],
                        p["A_elapsed_seconds"], p["B_elapsed_seconds"],
                        p["A_chunks"], p["B_chunks"]])

    # ---------------- 打印结论
    g = summary["gold_evaluation"]
    print("\n" + "=" * 70)
    print("===== A/B 实验 =====")
    print(f"A 原始文本：products={aggregate['A_total_products']} "
          f"Macro-F1={g['A']['macro_f1']} (Gold {len(gold_subset)}条)")
    print(f"B 表格预处理：products={aggregate['B_total_products']} "
          f"Macro-F1={g['B']['macro_f1']}")
    for cn in ("品牌", "规格型号", "产品/服务名称", "品目", "单价", "数量", "总价"):
        fa = g["A"]["fields"][cn]["F1"]
        fb = g["B"]["fields"][cn]["F1"]
        print(f"  {cn} F1：{fa} → {fb}")
    print("\n结构问题计数  A → B：")
    for k, label in (
            ("brand_string_in_name", "品牌串入名称"),
            ("brand_spec_misaligned", "品牌↔型号疑似错位"),
            ("spec_truncated", "型号疑似截断"),
            ("wrong_merge", "疑似错误合并"),
            ("wrong_split", "疑似错误拆分"),
            ("duplicate_lost", "重复项丢失"),
            ("unmerged_brand_cell", "品牌单元格未拆开"),
            ("unmerged_spec_cell", "规格单元格未拆开")):
        print(f"  {label}: {aggregate['A'][k]} → {aggregate['B'][k]}")
    print(f"\n平均耗时：A={aggregate['A_avg_elapsed']}s "
          f"B={aggregate['B_avg_elapsed']}s")

    # 宗级列表对齐口径（对结构差异公平的 Gold 评估）
    if lot_aware:
        print("\n" + "-" * 70)
        print("Gold 宗级列表·对齐评估（结构差异不强行计错）")
        from ai_extraction.evaluation.evaluate_goldset import FIELDS
        en_for = {cn: en for en, cn, _ in FIELDS}
        for aid, la in lot_aware.items():
            print(f"  [{aid}]")
            print(f"    Macro-F1：A={macro(la['A'])} → B={macro(la['B'])}")
            for cn in ("品牌", "规格型号", "产品/服务名称", "品目", "数量"):
                fa = la["A"][en_for[cn]]
                fb = la["B"][en_for[cn]]
                print(f"    {cn}: A P={fa['Precision']}/R={fa['Recall']}"
                      f"/F1={fa['F1']}  →  B P={fb['Precision']}"
                      f"/R={fb['Recall']}/F1={fb['F1']}")
    print(f"\n输出: {AB_DIR}")


if __name__ == "__main__":
    main()
