"""V2.2 筛选审计：只跑筛选，不调 Qwen。

对比 V2.1（run_v2_1_attachment_filter/selection_audit.csv）与 V2.2 规则：
  - 入选/过滤表数变化
  - 撤回的表（V2.1 入选 → V2.2 过滤）
  - 新增入选的表（V2.1 过滤 → V2.2 入选）
  - 负面噪声表是否仍保持 0 放行
  - 特别检查 t20260508 / t20260206

输出: data/validation_attachment_test/run_v2_2_filter_audit/
"""
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from ai_extraction.preprocessing.v2_2_table_filter import (  # noqa: E402
    CONTINUATION_MIN_FEATURES, FORM_BONUS, IDENTITY_ONLY_ENABLED,
    KW_IDENTITY_V22, STRONG_IDENTITY, TABLE_SCORE_THRESHOLD,
    score_table_v22, selected_v22,
)
from ai_extraction.preprocessing.v2_1_table_filter import (  # noqa: E402
    score_table_v21, selected_v21,
)
from ai_extraction.preprocessing.v2_table_filter import (  # noqa: E402
    KW_IDENTITY, KW_NEGATIVE, _norm_cell, render_table, score_table,
)

BASE = Path(__file__).parent / "data" / "validation_attachment_test"
PARSED_DIR = BASE / "parsed"
RUN_V2 = BASE / "run_v2_attachment_filter"
RUN_V21 = BASE / "run_v2_1_attachment_filter"
RUN_DIR = BASE / "run_v2_2_filter_audit"
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

    # V2 pred names（用于 Gold 漏名命中检查）
    pred_names = {}
    v2_dir = BASE / "run_v2_attachment_filter"
    for jf in sorted(v2_dir.glob("t*.json")):
        r = json.loads(jf.read_text(encoding="utf-8"))
        pred_names[r["announcement_id"]] = {
            norm(p.get("product_service_name"))
            for p in (r.get("result") or {}).get("products", []) or []
            if p.get("product_service_name")}

    # V2.1 audit
    v21_audit = {(r["announcement_id"], r["attachment_name"],
                  r["table_index"]): r
                 for r in csv.DictReader(
                     open(RUN_V21 / "selection_audit.csv",
                          encoding="utf-8-sig"))}

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / "logs").mkdir(exist_ok=True)
    (RUN_DIR / "selected_tables").mkdir(exist_ok=True)

    rows = []
    selected_by_aid = {}
    v21_sel_set = set()
    v22_sel_set = set()
    withdrawn = []          # V2.1 入选 → V2.2 过滤
    new_added = []          # V2.1 过滤 → V2.2 入选
    identity_only_triggered = []  # V2.1 中被身份单独入选触发的表

    for (aid, fn), rec in parsed.items():
        if rec["parse_status"] != "success":
            continue
        prev_sel_v22 = False
        prev_depth_v22 = 0
        prev_tb_v22 = None
        prev_sel_v21 = False

        for tb in rec["tables"]:
            ti = tb["table_index"]

            # V2.2
            s22, matched22, neg22, detail22 = score_table_v22(tb)
            sel22 = selected_v22(tb, s22, prev_selected=prev_sel_v22,
                                 prev_depth=prev_depth_v22,
                                 prev_tb=prev_tb_v22)

            # V2.1 (re-score for comparison)
            s21, matched21, neg21, detail21 = score_table_v21(tb)
            sel21 = selected_v21(tb, s21, prev_selected=prev_sel_v21)

            key = (aid, fn, str(ti))
            v21_row = v21_audit.get(key)
            v21_sel_val = v21_row["selected_v21"] == "True" if v21_row else sel21

            # 检查 V2.1 中身份单独入选触发的表
            if v21_sel_val and s21 < TABLE_SCORE_THRESHOLD:
                identity_only_triggered.append({
                    "aid": aid, "fn": fn, "ti": ti,
                    "score_v21": s21, "matched": matched21,
                })

            # Gold 漏名命中
            miss = [p.get("product_service_name")
                    for p in gold_by.get(aid, [])
                    if norm(p.get("product_service_name"))
                    and norm(p.get("product_service_name"))
                    not in pred_names.get(aid, set())]
            blob = norm("\n".join(" ".join(str(c) for c in row if c)
                                  for row in tb.get("data") or []))
            hits = [m for m in miss if norm(m) and norm(m) in blob]

            reason = ""
            if not sel22:
                if neg22:
                    reason = "负面否决:" + "|".join(neg22)
                elif s22 < TABLE_SCORE_THRESHOLD:
                    reason = f"score{s22}<threshold{TABLE_SCORE_THRESHOLD}"
                else:
                    reason = "其他"

            rows.append({
                "announcement_id": aid, "attachment_name": fn,
                "table_index": ti, "score_v22": s22,
                "score_v21": s21,
                "matched_keywords": "|".join(matched22),
                "negative": "|".join(neg22),
                "gold_missed_hits": len(hits),
                "hit_examples": "|".join(hits[:3]),
                "selected_v22": sel22,
                "selected_v21": v21_sel_val,
                "form_row_hit": detail22["form_row_hit"],
                "is_continuation": sel22 and s22 < TABLE_SCORE_THRESHOLD,
                "excluded_reason": reason,
            })

            if sel22:
                selected_by_aid.setdefault(aid, []).append((fn, ti, tb))
                v22_sel_set.add(key)
                if not v21_sel_val:
                    new_added.append(rows[-1])
            if v21_sel_val:
                v21_sel_set.add(key)
                if not sel22:
                    withdrawn.append(rows[-1])

            prev_sel_v22 = sel22
            prev_tb_v22 = tb
            if sel22 and s22 >= TABLE_SCORE_THRESHOLD:
                prev_depth_v22 = 0
            elif sel22:
                prev_depth_v22 += 1
            prev_sel_v21 = sel21

    # selection_audit.csv
    with open(RUN_DIR / "selection_audit.csv", "w", encoding="utf-8-sig",
              newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # selected_tables/
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

    # 统计
    sel22_count = len(v22_sel_set)
    sel21_count = len(v21_sel_set)
    fil22 = len(rows) - sel22_count

    # 29 报价公告
    v2_aids = sorted({k[0] for k in v21_audit})
    retained22 = [a for a in v2_aids if a in selected_by_aid]

    # 负面噪声放行
    neg_leak_22 = sum(1 for r in rows if r["selected_v22"] and r["negative"])
    neg_leak_21 = sum(1 for r in rows if r["selected_v21"]
                      and (r.get("negative") or ""))

    # 强身份词触发统计
    strong_trigger_aids = Counter(r["aid"] for r in identity_only_triggered)

    # 专项: t20260508
    t508_rows = [r for r in rows if r["announcement_id"] == "t20260508_26523656"]
    t508_v22_sel = [r for r in t508_rows if r["selected_v22"]]
    t508_v21_sel = [r for r in t508_rows if r["selected_v21"]]
    t508_withdrawn = [r for r in t508_rows if r["selected_v21"] and not r["selected_v22"]]

    # 专项: t20260206
    t206_rows = [r for r in rows if r["announcement_id"] == "t20260206_26155241"]
    t206_v22_sel = [r for r in t206_rows if r["selected_v22"]]
    t206_v21_sel = [r for r in t206_rows if r["selected_v21"]]
    t206_cont = [r for r in t206_v22_sel if r["is_continuation"]]

    audit_json = {
        "threshold": TABLE_SCORE_THRESHOLD,
        "identity_only_enabled": IDENTITY_ONLY_ENABLED,
        "form_bonus": FORM_BONUS,
        "continuation_min_features": CONTINUATION_MIN_FEATURES,
        "identity_words_v22": KW_IDENTITY_V22,
        "negative_veto": True,
        "tables_total": len(rows),
        "v21_selected": sel21_count,
        "v22_selected": sel22_count,
        "v22_filtered": fil22,
        "withdrawn_from_v21": len(withdrawn),
        "new_added_vs_v21": len(new_added),
        "negative_noise_leak_v21": neg_leak_21,
        "negative_noise_leak_v22": neg_leak_22,
        "price_aids_retained_v22": len(retained22),
        "price_aids_total": len(v2_aids),
        "identity_only_triggered_count": len(identity_only_triggered),
        "identity_only_triggered_by_aid": dict(strong_trigger_aids),
        "special_t20260508": {
            "v21_selected": len(t508_v21_sel),
            "v22_selected": len(t508_v22_sel),
            "withdrawn": len(t508_withdrawn),
            "withdrawn_tables": [
                {"table": r["table_index"] + 1,
                 "score_v21": r["score_v21"],
                 "score_v22": r["score_v22"],
                 "matched": r["matched_keywords"][:40]}
                for r in t508_withdrawn
            ],
        },
        "special_t20260206": {
            "v21_selected": len(t206_v21_sel),
            "v22_selected": len(t206_v22_sel),
            "continuation_tables": [
                {"table": r["table_index"] + 1,
                 "score_v22": r["score_v22"],
                 "is_continuation": r["is_continuation"]}
                for r in t206_cont
            ],
        },
    }
    (RUN_DIR / "selection_audit.json").write_text(
        json.dumps(audit_json, ensure_ascii=False, indent=2),
        encoding="utf-8")

    # 10 选中 + 10 过滤样例
    sel_examples = [r for r in rows if r["selected_v22"]][:10]
    fil_examples = [r for r in rows if not r["selected_v22"]
                    and r["score_v22"] > 0][:10]

    # README
    readme = f"""# V2.2 筛选审计结果

## 核心改动

1. **撤回强身份词单独入选**：`IDENTITY_ONLY_ENABLED = False`
   - V2.1 中 16 张表因 score<4 但含身份词被单独入选
   - V2.2 中这些表必须通过主阈值（score≥4）或严格跨页续表入选

2. **跨页续表收紧**：
   - 上一张表必须是高置信入选（score≥4）
   - 当前表无独立表头
   - 列数差 ≤1
   - ≥{CONTINUATION_MIN_FEATURES} 个续表结构特征

3. **负面否决保持**：负面特征表一律否决
4. **threshold 保持 4**

## 筛选对比

| 指标 | V2.1 | V2.2 |
|------|------|------|
| 入选表 | {sel21_count} | {sel22_count} |
| 过滤表 | {len(rows)-sel21_count} | {fil22} |
| 负面噪声放行 | {neg_leak_21} | {neg_leak_22} |
| 29报价公告保留 | {len(v2_aids)} | {len(retained22)}/{len(v2_aids)} |

## 撤回表（V2.1入选→V2.2过滤）：{len(withdrawn)} 张

### 强身份词触发统计
{dict(strong_trigger_aids)}

## t20260508
- V2.1 入选: {len(t508_v21_sel)} 张
- V2.2 入选: {len(t508_v22_sel)} 张
- 撤回: {len(t508_withdrawn)} 张

## t20260206
- V2.1 入选: {len(t206_v21_sel)} 张
- V2.2 入选: {len(t206_v22_sel)} 张
- 续表: {len(t206_cont)} 张
"""
    (RUN_DIR / "README.md").write_text(readme, encoding="utf-8")

    # 终端输出
    print("===== V2.1 vs V2.2 筛选审计 =====")
    print(f"V2.1 入选表: {sel21_count}")
    print(f"V2.2 入选表: {sel22_count}")
    print(f"V2.1 过滤表: {len(rows)-sel21_count}")
    print(f"V2.2 过滤表: {fil22}")
    print(f"29报价公告: V2.1={len(v2_aids)} V2.2={len(retained22)}/{len(v2_aids)}")
    print(f"负面噪声放行: V2.1={neg_leak_21} V2.2={neg_leak_22}")
    print(f"撤回表: {len(withdrawn)} 张")
    print(f"新增表: {len(new_added)} 张")
    print(f"强身份词触发(V2.1): {len(identity_only_triggered)} 张, 分布: {dict(strong_trigger_aids)}")
    print(f"\nt20260508: V2.1={len(t508_v21_sel)} → V2.2={len(t508_v22_sel)} (撤回{len(t508_withdrawn)})")
    for r in t508_withdrawn:
        print(f"  撤回表{r['table_index']+1}: v21_score={r['score_v21']} v22_score={r['score_v22']} kw={r['matched_keywords'][:40]}")
    print(f"\nt20260206: V2.1={len(t206_v21_sel)} → V2.2={len(t206_v22_sel)} (续表{len(t206_cont)})")
    for r in t206_cont:
        print(f"  续表{r['table_index']+1}: score={r['score_v22']} is_cont={r['is_continuation']}")

    print("\n--- 选中样例（前10）---")
    for r in sel_examples:
        print(f"  {r['announcement_id']} 表{r['table_index']+1} score={r['score_v22']} kw={r['matched_keywords'][:40]}")
    print("\n--- 过滤样例（前10）---")
    for r in fil_examples:
        print(f"  {r['announcement_id']} 表{r['table_index']+1} score={r['score_v22']} reason={r['excluded_reason'][:30]}")


if __name__ == "__main__":
    main()
