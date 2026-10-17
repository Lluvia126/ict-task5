"""V2.1 筛选审计：只跑筛选，不调 Qwen。

对比 V2（run_v2_attachment_filter/selection_audit.csv）与 V2.1 规则：
  - 入选/过滤表数
  - 新增入选表 & 是否覆盖 Gold 漏名
  - 典型噪声表是否仍被过滤（t20260508 技术要求、评分/审查/偏离表）
  - 特别检查 t20260206 / t20260508

输出: data/validation_attachment_test/run_v2_1_attachment_filter/
      selection_audit.csv/.json + selected_tables/ + v2_1_error_analysis.csv
      + v2_1_keyword_candidates.csv + README.md
"""
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from ai_extraction.preprocessing.v2_1_table_filter import (  # noqa: E402
    FORM_BONUS, IDENTITY_ONLY_MIN_HITS, KW_IDENTITY_V21, STRONG_IDENTITY,
    TABLE_SCORE_THRESHOLD, score_table_v21, selected_v21,
)
from ai_extraction.preprocessing.v2_table_filter import (  # noqa: E402
    KW_IDENTITY, KW_NEGATIVE, _norm_cell, render_table, score_table,
)

BASE = Path(__file__).parent / "data" / "validation_attachment_test"
PARSED_DIR = BASE / "parsed"
RUN_V2 = BASE / "run_v2_attachment_filter"
RUN_DIR = BASE / "run_v2_1_attachment_filter"
GOLD_JSON = Path(
    r"E:\xwechat_files\wxid_tqvrrgep742c22_709a\msg\file\2026-09\validation_v0(1).json")


def norm(s):
    if not s:
        return ""
    return re.sub(r"[\s（）()\[\]【】、,，。:：·\-—_/\\]+", "", str(s))


def load_parsed():
    out = {}
    for jf in sorted(PARSED_DIR.rglob("*.json")):
        rec = json.loads(jf.read_text(encoding="utf-8"))
        out[(rec["announcement_id"], rec["attachment_filename"])] = rec
    return out


def main():
    parsed = load_parsed()
    gold = json.loads(GOLD_JSON.read_text(encoding="utf-8"))
    gold_by = {r["announcement_id"]: r.get("products", []) for r in gold}

    # V2 pred names（评测口径的 Unmatched Gold）
    pred_names = {}
    for jf in sorted((RUN_V2 / "t*.json").parent.glob("t*.json")):
        r = json.loads(jf.read_text(encoding="utf-8"))
        pred_names[r["announcement_id"]] = {
            norm(p.get("product_service_name"))
            for p in (r.get("result") or {}).get("products", []) or []
            if p.get("product_service_name")}

    v2_audit = {(r["announcement_id"], r["attachment_name"],
                 r["table_index"]): r
                for r in csv.DictReader(
                    open(RUN_V2 / "selection_audit.csv",
                         encoding="utf-8-sig"))}

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / "logs").mkdir(exist_ok=True)
    (RUN_DIR / "selected_tables").mkdir(exist_ok=True)

    rows = []
    selected_by_aid = {}
    new_selected = []          # V2.1 新增入选（V2 被过滤）
    noise_recheck = []         # V2 典型噪声表复查
    for (aid, fn), rec in parsed.items():
        if rec["parse_status"] != "success":
            continue
        prev_sel = False       # 上一张表是否入选（跨页续表判定）
        for tb in rec["tables"]:
            ti = tb["table_index"]
            s21, matched, neg, detail = score_table_v21(tb)
            sel = selected_v21(tb, s21, prev_selected=prev_sel)
            key = (aid, fn, str(ti))
            v2row = v2_audit.get(key)
            v2_sel = v2row["selected"] == "True" if v2row else None

            # Gold 漏名命中（任何行）
            miss = [p.get("product_service_name")
                    for p in gold_by.get(aid, [])
                    if norm(p.get("product_service_name"))
                    and norm(p.get("product_service_name"))
                    not in pred_names.get(aid, set())]
            blob = norm("\n".join(" ".join(str(c) for c in row if c)
                                  for row in tb.get("data") or []))
            hits = [m for m in miss if norm(m) and norm(m) in blob]

            reason = ""
            if not sel:
                reason = "|".join(neg) or (
                    f"score{s21}<threshold{TABLE_SCORE_THRESHOLD}"
                    + ("" if detail["identity"] else "+无身份词"))
            rows.append({
                "announcement_id": aid, "attachment_name": fn,
                "table_index": ti, "score_v21": s21,
                "score_v2": v2row["score"] if v2row else "",
                "matched_keywords": "|".join(matched),
                "negative": "|".join(neg),
                "gold_missed_hits": len(hits),
                "hit_examples": "|".join(hits[:3]),
                "selected_v21": sel, "selected_v2": v2_sel,
                "form_row_hit": detail["form_row_hit"],
                "excluded_reason": reason,
            })
            if sel:
                selected_by_aid.setdefault(aid, []).append((fn, ti, tb))
                if v2_sel is False:
                    new_selected.append(rows[-1])
            prev_sel = sel
            # 典型噪声复查：V2 被过滤且带负面特征
            if v2_sel is False and neg:
                noise_recheck.append(rows[-1])

    # ---------------- selection_audit.csv ----------------
    with open(RUN_DIR / "selection_audit.csv", "w", encoding="utf-8-sig",
              newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    for aid, items in selected_by_aid.items():
        parts = []
        last_fn = None
        for fn, ti, tb in items:
            if fn != last_fn:
                parts.append(f"===== 第一层附件：{fn} =====")
                last_fn = fn
            parts.append(f"===== 报价明细表：表格{ti + 1} =====\n"
                         + render_table(tb))
        (RUN_DIR / "selected_tables" / f"{aid}.txt").write_text(
            "\n\n".join(parts), encoding="utf-8")

    # ---------------- 漏抽归因表 ----------------
    with open(RUN_DIR / "v2_1_error_analysis.csv", "w", encoding="utf-8-sig",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["announcement_id", "attachment_name", "table_index",
                    "gold_unmatched_count", "table_headers", "current_score",
                    "selected", "filter_reason", "missed_reason",
                    "suggested_keywords"])
        seen = set()
        for r in rows:
            if r["gold_missed_hits"] <= 0:
                continue
            k = (r["announcement_id"], r["attachment_name"],
                 r["table_index"])
            if k in seen:
                continue
            seen.add(k)
            rec = parsed[(r["announcement_id"], r["attachment_name"])]
            tb = rec["tables"][r["table_index"]]
            hdr = " | ".join(_norm_cell(c)[:16] for c in tb["data"][0])[:120]
            newly = "恢复(V2被误过滤)" if (r["selected_v21"]
                                          and r["selected_v2"] is False) else (
                "已入选" if r["selected_v21"] else "仍被过滤")
            w.writerow([
                r["announcement_id"], r["attachment_name"],
                r["table_index"] + 1, r["gold_missed_hits"], hdr,
                f"v2={r['score_v2']}/v21={r['score_v21']}",
                r["selected_v21"], r["excluded_reason"], newly,
                "|".join(x["keyword"] for x in KEYWORD_ROWS
                         if x["affected_tables"].startswith(
                             f"{r['announcement_id']}|{r['table_index']}"))
                or "见keyword_candidates",
            ])

    # ---------------- 新词统计表 ----------------
    with open(RUN_DIR / "v2_1_keyword_candidates.csv", "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["keyword", "frequency", "affected_tables",
                    "affected_gold_products", "semantic_type",
                    "current_score_effect", "recommendation"])
        w.writerows([[x["keyword"], x["frequency"], x["affected_tables"],
                      x["affected_gold_products"], x["semantic_type"],
                      x["current_score_effect"], x["recommendation"]]
                     for x in KEYWORD_ROWS])

    # ---------------- 统计 ----------------
    sel21 = [r for r in rows if r["selected_v21"]]
    fil21 = [r for r in rows if not r["selected_v21"]]
    lost = [r for r in rows if r["selected_v2"] == "True"
            and not r["selected_v21"]]
    v2_pc_aids = sorted({k[0] for k in v2_audit})
    retained21 = [a for a in v2_pc_aids if a in selected_by_aid]
    new_cov_hits = sum(r["gold_missed_hits"] for r in new_selected)
    noise_still_filtered = sum(1 for r in noise_recheck if not r["selected_v21"])

    # 特别检查
    def aid_rows(aid):
        return [r for r in rows if r["announcement_id"] == aid]
    t206 = [r for r in aid_rows("t20260206_26155241")
            if r["selected_v21"] and r["gold_missed_hits"] > 0]
    t508_tech = [r for r in aid_rows("t20260508_26523656")
                 if "技术" in (r["excluded_reason"] or "")
                 or r["score_v21"] < TABLE_SCORE_THRESHOLD]

    audit_json = {
        "threshold": TABLE_SCORE_THRESHOLD,
        "identity_only_min_hits": IDENTITY_ONLY_MIN_HITS,
        "form_bonus": FORM_BONUS,
        "new_identity_words": KW_IDENTITY_V21,
        "strong_identity_words": STRONG_IDENTITY,
        "negative_veto": True,
        "tables_total": len(rows),
        "v2_selected": sum(1 for r in rows if r["selected_v2"] is True),
        "v21_selected": len(sel21),
        "v21_filtered": len(fil21),
        "new_selected_tables": len(new_selected),
        "new_selected_gold_missed_hits_covered": new_cov_hits,
        "v2_selected_now_vetoed": len(lost),
        "vetoed_gold_missed_hits": sum(int(r["gold_missed_hits"])
                                       for r in lost),
        "noise_tables_recheck_filtered": noise_still_filtered,
        "price_aids_retained_v21": retained21,
        "price_aids_misfiltered_v21": [a for a in v2_pc_aids
                                       if a not in retained21],
        "special_t20260206_recovered": [
            {"att": r["attachment_name"], "table": r["table_index"] + 1,
             "hits": r["gold_missed_hits"]} for r in t206],
        "special_t20260508_tech_still_filtered": len(t508_tech),
    }
    (RUN_DIR / "selection_audit.json").write_text(
        json.dumps(audit_json, ensure_ascii=False, indent=2),
        encoding="utf-8")

    print("===== V2.1 筛选审计 =====")
    print(f"V2 入选表: {audit_json['v2_selected']} → V2.1 入选表: {len(sel21)}")
    print(f"V2 过滤表: {len(rows) - audit_json['v2_selected']} → "
          f"V2.1 过滤表: {len(fil21)}")
    print(f"新增入选: {len(new_selected)} 张, 覆盖Gold漏名 {new_cov_hits} 处")
    print(f"噪声复查: {len(noise_recheck)} 张带负面特征表, "
          f"{noise_still_filtered} 张仍被过滤")
    print(f"29报价公告保留: {len(retained21)}/29 "
          f"(V2.1 误过滤: {audit_json['price_aids_misfiltered_v21']})")
    print(f"t20260206 恢复: {audit_json['special_t20260206_recovered']}")
    print(f"t20260508 技术要求仍过滤: {audit_json['special_t20260508_tech_still_filtered']}")


if __name__ == "__main__":
    KEYWORD_ROWS = [
        {"keyword": "（跨页续表：数据行形态兜底）", "frequency": "3张表(38/39/40)",
         "affected_tables": "t20260206_26155241|37,38,39（表头在表37）",
         "affected_gold_products": 65,
         "semantic_type": "结构特征（非词表）", "current_score_effect": "V2得分0",
         "recommendation": "已采纳：FORM_BONUS=3+上一张表已入选时续表入选"},
        {"keyword": "服务名称", "frequency": 1,
         "affected_tables": "t20260508_26523656|7（表8）",
         "affected_gold_products": 3,
         "semantic_type": "强身份类（已在V2词表，缺价格/数量）",
         "current_score_effect": "score=1（负面-3压制）",
         "recommendation": "已采纳：强身份词单独入选（负面否决下）"},
        {"keyword": "包组名称", "frequency": 2,
         "affected_tables": "t20260512_26549108|3（表4，1包/2包）",
         "affected_gold_products": 2,
         "semantic_type": "身份类（包级汇总表）", "current_score_effect": "score=2<4",
         "recommendation": "已采纳：加入身份词表"},
        {"keyword": "招标内容", "frequency": 1,
         "affected_tables": "t20260612_26743324|0（表1）",
         "affected_gold_products": 2,
         "semantic_type": "身份类（包级汇总表）", "current_score_effect": "score=2<4",
         "recommendation": "已采纳：加入身份词表"},
        {"keyword": "采购包/项目名称/服务内容/采购内容", "frequency": "—",
         "affected_tables": "真实数据同族表达，防同类漏抽",
         "affected_gold_products": "—", "semantic_type": "身份类",
         "current_score_effect": "+2/词", "recommendation": "已采纳：加入身份词表"},
        {"keyword": "（拒绝）主要参数要/接口/SSD/材质/序号/名称(泛化)",
         "frequency": "27/6/4/4/233/—",
         "affected_tables": "t20260206 表42-80 技术参数表等",
         "affected_gold_products": 0,
         "semantic_type": "技术参数类/泛化词（区分度0.02-0.4）",
         "current_score_effect": "0或负面", "recommendation": "拒绝：导致大量技术参数表/目录表误入选"},
    ]
    main()
