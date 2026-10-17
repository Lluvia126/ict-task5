"""HTML 表格结构预处理器（通用 POC 版）。

目标：在不调用大模型的前提下，从 HTML <table> 恢复二维结构，并安全判断
产品表中的某一行是：
  - normal  : 普通一行一产品，保持原始行结构，不做额外处理
  - split   : 单元格内存在"并行列表"，且结构证据足够明确 → 按位置拆成子项
  - keep_raw: 无法从 HTML 结构证明对应关系 → 原样保留，标记无法判断，不猜

拆分的必要条件（全部满足才 split）：
  1. 品牌、规格型号单元格都不含外部指引/未完语（详见、附件、格式文件、
     项目清单、按招标文件要求、结尾"等"等）；
  2. 规格单元格不是描述性长文本（"规格：…/型号：…"标签、句号、
     分号与顿号混用等）；
  3. 两列按同一分隔符（换行 / ；/ ; / 、）切出的段数都 >= 2 且段数相等；
  4. 每个切段长度合理、不含句子标点。
段数不等、分隔符不一致、任一条目不满足 sanity 时一律 keep_raw。

天津式标的表（表头：类型/名称/品牌/规格型号/数量/单价）额外要求
名称列表与品牌、规格列表等长；整行多列并行，任一列表无法解析即拒绝。

输出统一中间格式（见 parse_announcement），并对每行记录决策证据。
本模块为独立预处理，不修改正式抽取流程。
"""
import re
from pathlib import Path
from bs4 import BeautifulSoup

# ---------------------------------------------------------------- 常量

# 外部指引 / 列表不完整的信号：出现即不能以本 HTML 为准拆分
HEDGE_MARKERS = (
    "详见", "见附件", "另附", "格式文件", "项目清单", "招标文件要求",
    "具体详见", "投标文件", "报价表", "清单报价",
)

# 列表候选分隔符（按优先级：换行最强）
DELIMITERS = ("\n", "；", ";", "、")

# 切段 sanity 上限（防止把描述性句子切成"产品"）
BRAND_PART_MAX = 25
SPEC_PART_MAX = 60
NAME_PART_MAX = 40

# 描述性文本信号
PROSE_LABELS = ("规格：", "规格:", "型号：", "型号:", "产地：", "产地:", "品牌：", "品牌:")
SENTENCE_PUNCT = "。！？"

# 产品表表头匹配
SPEC_HEADERS = ("规格型号", "规格/型号", "型号规格", "规格(型号)", "规格（型号）")
SERVICE_WORDS = ("服务范围", "服务要求", "服务时间", "服务标准")


# ---------------------------------------------------------------- 基础解析

def cell_text(cell):
    """取单元格文本，<br> 转换行。"""
    for br in cell.find_all("br"):
        br.replace_with("\n")
    return cell.get_text("", strip=True)


def build_grid(table):
    """把 <table> 恢复为二维网格。

    返回 (grid, ncols, has_rowspan, has_colspan)：
      grid[r][c] = 单元格 dict:
        {text, origin: (r0, c0), rowspan, colspan, is_th}
      rowspan/colspan 覆盖的位置引用源单元格 dict（不复制内容）。
    """
    trs = table.find_all("tr")
    grid = []
    pending = {}  # (r, c) -> 源单元格 dict（rowspan/colspan 填充）
    ncols = 0
    has_rs = has_cs = False

    for r, tr in enumerate(trs):
        row = []
        c = 0
        for cell in tr.find_all(["th", "td"]):
            while (r, c) in pending:
                row.append(pending[(r, c)])
                c += 1
            rs = _as_int(cell.get("rowspan"), 1)
            cs = _as_int(cell.get("colspan"), 1)
            data = {
                "text": cell_text(cell),
                "origin": (r, c),
                "rowspan": rs,
                "colspan": cs,
                "is_th": cell.name == "th",
            }
            for rr in range(r, r + rs):
                for cc in range(c, c + cs):
                    if rr == r and cc == c:
                        continue
                    pending[(rr, cc)] = data
            for _ in range(cs):
                row.append(data)
            has_rs |= rs > 1
            has_cs |= cs > 1
            c += cs
        # 行尾：把本行 pending 的单元格按其精确列号放入（短行可能只到某列，
        # 中间留空，例如嵌套表压平后 rowspan=2 跨到下一条短记录行）
        pending_cols = [cc for (rr, cc) in pending if rr == r]
        if pending_cols:
            last_cc = max(pending_cols)
            while c <= last_cc:
                row.append(pending.get((r, c)))  # 空位为 None
                c += 1
        grid.append(row)
        ncols = max(ncols, len(row))
    return grid, ncols, has_rs, has_cs


def _as_int(v, default):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- 表头 / 表类型

def _header_texts(grid):
    if not grid:
        return []
    return [d["text"] if d else "" for d in grid[0]]


def _find_col(hs, *needles):
    """返回首个表头包含任一 needle 的列索引（优先全等匹配）。"""
    for i, h in enumerate(hs):
        if any(h.strip() == n for n in needles):
            return i
    for i, h in enumerate(hs):
        if any(n in h for n in needles):
            return i
    return None


def _exact_col(hs, *needles):
    """表头文本全等匹配的列索引（用于严格识别，防止大文本单元格子串误判）。"""
    for i, h in enumerate(hs):
        if h.strip() in needles:
            return i
    return None


def classify_table(hs):
    """根据表头判定表类型：product / service / other。

    严格条件：
      - 列数 < 3 → other（公告大文本表、信息表）；
      - 任一表头单元格文本 > 30 字 → other（公告全文塞在一个 td 的情形）；
      - 产品表关键列必须全等匹配且品牌/规格分列；
      - 服务表以全等的服务范围/要求/时间/标准列计数。
    """
    if len(hs) < 3:
        return "other"
    if any(len(h.strip()) > 30 for h in hs):
        return "other"

    ci_brand = _exact_col(hs, "品牌")
    ci_spec = _exact_col(hs, *SPEC_HEADERS)
    ci_target = _exact_col(hs, "采购标的")
    ci_name = _exact_col(hs, "名称")
    ci_type = _exact_col(hs, "类型")
    # 兜底名称列（子串）：大文本单元格已由 ≤30 字规则排除，
    # 可安全识别"标的名称/标项名称/产品名称"等表头变体。
    ci_name_any = _find_col(hs, "名称") if ci_name is None else ci_name

    if ci_brand is not None and ci_spec is not None and ci_brand != ci_spec:
        other_cols_ok = (
            (ci_target is not None and ci_target not in (ci_brand, ci_spec))
            or (ci_name_any is not None and ci_name_any not in (ci_brand, ci_spec))
            or ci_type is not None
        )
        if other_cols_ok:
            return "product"

    n_service = sum(1 for h in hs if h.strip() in SERVICE_WORDS)
    if n_service >= 2:
        return "service"
    ci_lot = _exact_col(hs, "品目号")
    if (ci_lot is not None or ci_target is not None) and ci_brand is None:
        # 品目号 + 采购标的 + 服务范围/要求 一类（无品牌列）
        return "service"
    return "other"


def _product_subtype(hs):
    """product 表的两种版面：guangdong（品目号/采购标的）/ tianjin（类型/名称）。"""
    if _exact_col(hs, "品目号") is not None and _exact_col(hs, "采购标的") is not None:
        return "guangdong"
    if _exact_col(hs, "类型") is not None and _exact_col(hs, "名称") is not None:
        return "tianjin"
    return "generic"


# ---------------------------------------------------------------- 列表解析（证据驱动）

def _has_hedge(s):
    s = (s or "").strip()
    if not s:
        return None
    for m in HEDGE_MARKERS:
        if m in s:
            return m
    if s.endswith("等"):  # 列表未尽（A、B等）
        return "结尾\"等\""
    return None


def _looks_like_prose(s):
    s = (s or "").strip()
    if not s:
        return False
    if any(s.startswith(lab) for lab in PROSE_LABELS):
        return True
    if any(p in s for p in SENTENCE_PUNCT):
        return True
    # 分号（含中文）与顿号同时出现 → 典型规格描述而非并行列表
    if ("、" in s) and (";" in s or "；" in s):
        return True
    if any(lab in s for lab in ("型号：", "型号:", "产地：", "产地:")):
        return True
    return False


def parse_list(s, part_max):
    """把单元格解析为并行列表。

    返回 (delimiter, parts)：
      - 单值：(None, [value])
      - 多值：(实际分隔符, [parts])
      - 无法安全解析：(None, None)
    """
    s = (s or "").strip()
    if not s:
        return None, []
    best_d, best_parts = None, [s]
    for d in DELIMITERS:
        parts = [p.strip() for p in s.split(d) if p.strip()]
        if len(parts) > len(best_parts):
            best_d, best_parts = d, parts
    if best_d is None:
        return None, best_parts
    for p in best_parts:
        if len(p) > part_max:
            return None, None
        if any(ch in p for ch in SENTENCE_PUNCT):
            return None, None
    return best_d, best_parts


# ---------------------------------------------------------------- 行决策

def _row_values(hs, grid_row):
    def get(ci):
        if ci is None or ci >= len(grid_row) or grid_row[ci] is None:
            return ""
        return grid_row[ci]["text"]
    return get


def _name_fallback_col(hs):
    """产品名兜底列：按 标的名称>产品名称>项目名称>标项名称 优先级；
    排除 供应商名称。"""
    for k in ("标的名称", "产品名称", "项目名称", "标项名称"):
        for i, h in enumerate(hs):
            if k in h:
                return i
    for i, h in enumerate(hs):
        if "名称" in h and "供应商" not in h:
            return i
    return None


def decide_product_row(hs, grid_row, subtype):
    """对 product 表一行做决策，返回 row record（见模块注释）。"""
    get = _row_values(hs, grid_row)
    ci = {
        "lot_no": _find_col(hs, "品目号"),
        "category": _find_col(hs, "品目名称"),
        "target": _find_col(hs, "采购标的"),
        "name": _find_col(hs, "名称"),
        "type": _find_col(hs, "类型"),
        "brand": _find_col(hs, "品牌"),
        "spec": _find_col(hs, *SPEC_HEADERS),
        "quantity": _find_col(hs, "数量（单位）", "数量(单位)", "数量"),
        "unit_price": _find_col(hs, "单价(元)", "单价（元）", "单价(万元)", "单价"),
        "total_price": _find_col(hs, "总价(元)", "总价（元）", "总价"),
    }
    # generic 版面（如 序号/标的名称/品牌/规格型号）：用兜底名称列承载产品名
    ci["name_fallback"] = None if ci["target"] is not None else _name_fallback_col(hs)
    brand_raw = get(ci["brand"])
    spec_raw = get(ci["spec"])
    base = {
        "品目号": get(ci["lot_no"]) or None,
        "品目名称": get(ci["category"]) or None,
        "采购标的": get(ci["target"]) or None,
        "数量（单位）": get(ci["quantity"]) or None,
    }
    raw_columns = {h: (grid_row[i]["text"] if i < len(grid_row)
                       and grid_row[i] is not None else "")
                   for i, h in enumerate(hs)}

    rec = {
        "decision": None,
        "reason": None,
        "brand_count": None,
        "spec_count": None,
        "name_count": None,
        "aligned": False,
        "uncertain": False,
        "sub_items": [],
        "base": base,
        "raw_columns": raw_columns,
        "_ci": ci,
    }

    # 1) 外部指引 / 列表未尽 → 原样保留
    for raw, label in ((brand_raw, "品牌"), (spec_raw, "规格型号")):
        h = _has_hedge(raw)
        if h:
            rec.update(decision="keep_raw", uncertain=True,
                       reason=f"{label}单元格含外部指引/未尽信号「{h}」，HTML 内信息不完整，拒绝猜测")
            return rec

    # 2) 规格为描述性文本：按普通一行一产品处理（品牌必须单值）
    if _looks_like_prose(spec_raw):
        db, brands = parse_list(brand_raw, BRAND_PART_MAX)
        if db is None and brands is None:
            rec.update(decision="keep_raw", uncertain=True,
                       reason="规格为描述性文本且品牌列表无法安全解析")
            return rec
        if len(brands) == 1:
            rec.update(decision="normal",
                       reason="规格单元格为描述文本（含标签/句号/混用标点），按一行一产品保留，不拆分",
                       brand_count=1, spec_count=1)
            return rec
        rec.update(decision="keep_raw", uncertain=True,
                   reason=f"规格为描述文本但品牌含 {len(brands)} 个值，无法证明对应关系")
        return rec

    # 3) 解析两个列表
    db, brands = parse_list(brand_raw, BRAND_PART_MAX)
    ds, specs = parse_list(spec_raw, SPEC_PART_MAX)
    if brands is None or specs is None:
        rec.update(decision="keep_raw", uncertain=True,
                   reason="品牌或规格列表含超长/句子式条目，无法安全解析")
        return rec
    rec["brand_count"] = len(brands)
    rec["spec_count"] = len(specs)

    # 天津式：名称列也必须是可对齐的并行列表
    names = None
    if subtype == "tianjin":
        name_raw = get(ci["name"])
        h = _has_hedge(name_raw)
        if h:
            rec.update(decision="keep_raw", uncertain=True,
                       reason=f"名称单元格含外部指引/未尽信号「{h}」",
                       name_count=None)
            return rec
        dn, names = parse_list(name_raw, NAME_PART_MAX)
        if names is None:
            rec.update(decision="keep_raw", uncertain=True,
                       reason="名称列表无法安全解析", name_count=None)
            return rec
        rec["name_count"] = len(names)
        if not (db == dn == ds and len(names) == len(brands) == len(specs) and len(names) >= 2):
            rec.update(decision="keep_raw", uncertain=True,
                       reason="名称/品牌/规格列表分隔符或段数不一致，拒绝整行配对")
            return rec
    else:
        # 广东式：两列等长、同一分隔符
        if len(brands) == 1 and len(specs) == 1:
            rec.update(decision="normal", reason="一行一产品（品牌、规格均为单值）")
            return rec
        if db != ds:
            rec.update(decision="keep_raw", uncertain=True,
                       reason=f"品牌分隔符「{db}」与规格分隔符「{ds}」不一致，拒绝猜测位置")
            return rec
        if len(brands) != len(specs):
            rec.update(decision="keep_raw", uncertain=True,
                       reason=f"品牌列表 {len(brands)} 段 != 规格列表 {len(specs)} 段，无法一一对应")
            return rec

    # 4) 证据充分：按位置拆成子项
    rec["decision"] = "split"
    rec["aligned"] = True
    rec["reason"] = (f"品牌 {len(brands)} 段与规格 {len(specs)} 段等长"
                     f"（分隔符「{_delim_name(db)}」），按原始位置建立 brand[i]↔spec[i]")
    for i, (b, sp) in enumerate(zip(brands, specs)):
        if names:
            pname = _clean_name_mark(names[i])
        else:
            # generic 版面（标项名称/标的名称）以名称列承载产品名
            pname = (get(ci["target"]) or get(ci["name_fallback"])
                     or get(ci["name"]))
        item = {
            "product_service_name": pname or None,
            "brand": b or None,
            "spec_model": sp or None,
            # 分包整包行的数量/价格是宗级，不分摊到子项
            "quantity": None if (names or len(brands) > 1) else get(ci["quantity"]) or None,
            "unit_price": None if (names or len(brands) > 1) else get(ci["unit_price"]) or None,
            "total_price": None if (names or len(brands) > 1) else get(ci["total_price"]) or None,
        }
        rec["sub_items"].append(item)
    return rec


def _delim_name(d):
    return {"\n": "换行", "；": "；", ";": ";", "、": "、", None: "单值"}.get(d, str(d))


def _clean_name_mark(s):
    """去掉天津式名称条目前的 ▲/■/● 等符号，不改其余内容。"""
    return re.sub(r"^[▲■●★\s]+", "", s or "").strip()


def decide_normal_row(hs, grid_row):
    """service/other 表的行：不做拆分，仅保留原始列。"""
    return {
        "decision": "passthrough",
        "reason": "非产品表，保持原始行结构",
        "brand_count": None,
        "spec_count": None,
        "name_count": None,
        "aligned": False,
        "uncertain": False,
        "sub_items": [],
        "raw_columns": {h: (grid_row[i]["text"] if i < len(grid_row)
                            and grid_row[i] is not None else "")
                        for i, h in enumerate(hs)},
    }


# ---------------------------------------------------------------- 公告级解析

def parse_announcement(html, announcement_id=None):
    """解析单个公告，返回统一中间格式 dict。

    结构：
      announcement_id
      tables: [
        {table_index, table_type, subtype, header, n_rows, n_cols,
         has_rowspan, has_colspan,
         rows: [ {row_index, decision, reason, brand_count, spec_count,
                  name_count, aligned, uncertain, sub_items[],
                  base{}, raw_columns{}} ]}
      ]
      stats: 见 _compute_stats
    """
    soup = BeautifulSoup(html, "html.parser")
    out = {"announcement_id": announcement_id, "tables": []}

    for ti, table in enumerate(soup.find_all("table")):
        grid, ncols, has_rs, has_cs = build_grid(table)
        hs = _header_texts(grid)
        ttype = classify_table(hs)
        subtype = _product_subtype(hs) if ttype == "product" else None
        trec = {
            "table_index": ti,
            "table_type": ttype,
            "subtype": subtype,
            "header": hs,
            "n_rows": max(0, len(grid) - 1),
            "n_cols": ncols,
            "has_rowspan": has_rs,
            "has_colspan": has_cs,
            "rows": [],
        }
        for ri, grid_row in enumerate(grid[1:], start=1):
            if ttype == "product":
                rrec = decide_product_row(hs, grid_row, subtype)
            else:
                rrec = decide_normal_row(hs, grid_row)
            rrec["row_index"] = ri
            # 内部结构位置标识：announcement_id * table_index * row_index *
            # subitem_index。仅用于内部 merge 同位判定，不进入最终 API JSON。
            for si, it in enumerate(rrec.get("sub_items", []), start=1):
                it["position_id"] = f"t{ti}-r{ri}-s{si}"
            trec["rows"].append(rrec)
        out["tables"].append(trec)

    out["stats"] = compute_stats(out)
    return out


def iter_sub_items(parsed):
    """按表/行顺序产出 (table_index, row_index, sub_item)。

    sub_item 含内部字段 position_id（t{table}-r{row}-s{sub}）。
    """
    for t in parsed["tables"]:
        if t["table_type"] != "product":
            continue
        for r in t["rows"]:
            for it in r["sub_items"]:
                yield t["table_index"], r["row_index"], it


def compute_stats(parsed):
    """公告级统计：决策计数、对齐子项、重复项保留数等。"""
    stats = {
        "n_tables": len(parsed["tables"]),
        "n_product_tables": 0,
        "n_service_tables": 0,
        "normal_rows": 0,
        "split_rows": 0,
        "keep_raw_rows": 0,
        "sub_items_total": 0,
        "aligned_pairs": 0,
        "uncertain_rows": 0,
        "duplicates_preserved": 0,
    }
    seen_pairs = {}
    for t in parsed["tables"]:
        if t["table_type"] == "product":
            stats["n_product_tables"] += 1
        elif t["table_type"] == "service":
            stats["n_service_tables"] += 1
        for r in t["rows"]:
            d = r["decision"]
            if d == "normal":
                stats["normal_rows"] += 1
            elif d == "split":
                stats["split_rows"] += 1
            elif d == "keep_raw":
                stats["keep_raw_rows"] += 1
            if r.get("uncertain"):
                stats["uncertain_rows"] += 1
            items = r.get("sub_items", [])
            stats["sub_items_total"] += len(items)
            if d == "split":
                stats["aligned_pairs"] += len(items)
            for it in items:
                key = (it.get("brand") or "", it.get("spec_model") or "")
                if key in seen_pairs:
                    stats["duplicates_preserved"] += 1  # 同位重复，仍保留
                seen_pairs.setdefault(key, 0)
                seen_pairs[key] += 1
    return stats


# ---------------------------------------------------------------- 文本渲染

def render_report(parsed):
    """把中间格式渲染为人工检查用纯文本。"""
    st = parsed["stats"]
    L = [
        f"# 公告：{parsed['announcement_id'] or ''}",
        f"# 表总数={st['n_tables']} 产品表={st['n_product_tables']} "
        f"服务表={st['n_service_tables']}",
        f"# 行决策：normal={st['normal_rows']} split={st['split_rows']} "
        f"keep_raw={st['keep_raw_rows']} | 子项={st['sub_items_total']} "
        f"对齐={st['aligned_pairs']} 重复保留={st['duplicates_preserved']}",
        "",
    ]
    for t in parsed["tables"]:
        if t["table_type"] == "other":
            continue
        sub = f"/{t['subtype']}" if t["subtype"] else ""
        L.append(f"[table {t['table_index']}] type={t['table_type']}{sub} "
                 f"rows={t['n_rows']} cols={t['n_cols']} "
                 f"rowspan={t['has_rowspan']} colspan={t['has_colspan']}")
        L.append("  表头：" + " | ".join(t["header"]))
        for r in t["rows"]:
            tag = r["decision"].upper()
            L.append(f"  row {r['row_index']}: {tag} — {r['reason']}")
            if r["decision"] == "split":
                L.append(f"    拆分前 品牌原文：{r['raw_columns'].get('品牌', '')}")
                L.append(f"    拆分前 规格原文：{r['raw_columns'].get('规格型号', '')}")
                base = r.get("base", {})
                for i, it in enumerate(r["sub_items"], 1):
                    L.append(
                        f"    [{i}] 品名={it['product_service_name'] or ''} "
                        f"品牌={it['brand'] or ''} 规格={it['spec_model'] or ''} "
                        f"(品目号={base.get('品目号') or ''})")
            elif r["decision"] == "normal":
                rc = r["raw_columns"]
                L.append(f"    保留：采购标的={rc.get('采购标的', '')} "
                         f"品牌={rc.get('品牌', '')} 规格={rc.get('规格型号', '')[:80]}")
            elif r["decision"] == "keep_raw":
                rc = r["raw_columns"]
                L.append(f"    原样保留 品牌：{rc.get('品牌', '')}")
                L.append(f"    原样保留 规格：{rc.get('规格型号', '')}")
            elif r["decision"] == "passthrough":
                rc = r["raw_columns"]
                L.append("    原始行：" + " | ".join(
                    f"{h}={(rc.get(h) or '')[:50]}" for h in t["header"]))
        L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- 向后兼容接口

def extract_lots(html):
    """旧接口（run_table_poc.py 使用）：返回拍平的 lot 列表。

    lot: {lot_no, category, target, quantity, aligned,
          sub_items: [{brand, spec_model}], brand_raw, spec_raw}
    """
    parsed = parse_announcement(html)
    lots = []
    for t in parsed["tables"]:
        if t["table_type"] != "product":
            continue
        for r in t["rows"]:
            base = r.get("base", {})
            rc = r["raw_columns"]
            lot = {
                "lot_no": base.get("品目号"),
                "category": base.get("品目名称"),
                "target": base.get("采购标的"),
                "quantity": base.get("数量（单位）"),
                "aligned": r["decision"] == "split",
                "sub_items": [{"brand": it["brand"], "spec_model": it["spec_model"]}
                              for it in r["sub_items"]],
                "brand_raw": rc.get("品牌"),
                "spec_raw": rc.get("规格型号"),
            }
            if r["decision"] == "normal":
                lot["aligned"] = True
                lot["sub_items"] = [{"brand": rc.get("品牌") or None,
                                     "spec_model": rc.get("规格型号") or None}]
            lots.append(lot)
    return lots


def render_preprocessed_text(lots):
    """旧接口：lot 列表 -> 【宗/包】格式文本（与 V1 POC 一致）。"""
    lines = []
    for lot in lots:
        lines.append(f"【宗/包】{lot['lot_no'] or ''}")
        lines.append(f"【品目名称】{lot['category'] or ''}")
        lines.append(f"【采购标的】{lot['target'] or ''}")
        lines.append(f"【数量（单位）】{lot['quantity'] or ''}")
        if lot["aligned"]:
            for i, it in enumerate(lot["sub_items"], 1):
                lines.append(f"【子项{i}】(所属采购标的：{lot['target'] or ''})")
                lines.append(f"品牌：{it['brand'] or ''}")
                lines.append(f"规格型号：{it['spec_model'] or ''}")
        else:
            lines.append("【子项】品牌串与规格串无法从HTML结构确定对应关系：")
            lines.append(f"品牌原文：{lot['brand_raw'] or ''}")
            lines.append(f"规格型号原文：{lot['spec_raw'] or ''}")
        lines.append("")
    return "\n".join(lines)


def preprocess_html_tables(html):
    """便捷入口：HTML -> 结构化文本。"""
    lots = extract_lots(html)
    return render_preprocessed_text(lots), lots


def preprocess_file(path):
    """文件级便捷入口。"""
    path = Path(path)
    parsed = parse_announcement(
        path.read_text(encoding="utf-8", errors="ignore"),
        announcement_id=path.stem,
    )
    return parsed, render_report(parsed)
