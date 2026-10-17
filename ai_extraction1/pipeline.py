"""HTML 表格预处理正式接入后的 AI 抽取主流程。

目标流程：
  HTML
  → 原有清洗 (cleaner.preprocess_text)
  → HTML 表格结构预处理 (html_table_preprocessor)
  → 结构化中间文本 + 分块（结构边界，每块 ≤10 子项）
  → 原 V1 Prompt + Qwen（调用参数不变）
  → 原有后处理 (qwen_extractor 内 _clean_str/_to_number)
  → 结构位置优先 merge
  → result

两条路径：
  legacy     : 全文没有任何一行被预处理器判定为 split →
               与历史 V1 完全一致（cleaner 文本 + chunk_text 字符分块 +
               全字段去重 merge），保证普通公告零行为变化。
  structured : 存在 split 行 →
               含 split 的产品表从清洗文本中移除，改渲染为结构化子项块；
               其余内容（公告头、供应商表、服务表等）仍走原清洗文本；
               每个子项带内部结构位置 ID（t{表}-r{行}-s{子项}），
               merge 按位置去重：同位置可折叠，不同位置即使字段完全相同也保留。

位置 ID 只用于内部对齐/合并，不出现在最终 API 返回 JSON 中。
"""
import hashlib
import re
import time

from bs4 import BeautifulSoup

from .config import MAX_CHUNK_CHARS, empty_result
from .preprocessing.cleaner import preprocess_text
from .preprocessing.chunker import chunk_text
from .preprocessing.html_table_preprocessor import parse_announcement
from .extractors.qwen_extractor import _to_number

# 结构化产品块每块最多子项数（输出 ~6000 中文字符封顶，≤10 稳妥）
MAX_ITEMS_PER_CHUNK = 10
# 结构化产品块文本软上限
MAX_STRUCT_CHUNK_CHARS = 3600


# ================================================================ 计划构建

def _row_col(r, hs, name):
    """按 decide_product_row 的列索引映射取回本行该逻辑列原文。"""
    i = r.get("_ci", {}).get(name)
    if i is None or i >= len(hs):
        return None
    v = r["raw_columns"].get(hs[i])
    return v or None


def _build_units(parsed):
    """从 parsed 中含 split 的产品表构造有序子项单元。

    只处理「至少含一行 split」的产品表：
      split 行 → 每个 sub_item 一个 strict 单元（带 position_id）
      normal 行 → 1 个 strict 单元（机械上 1:1 已知）
      keep_raw 行 → 1 个 raw 单元（原文原样，不猜测对应关系）
    返回 [(table_index, row_index, unit)]。
    """
    units = []
    for t in parsed["tables"]:
        if t["table_type"] != "product":
            continue
        if not any(r["decision"] == "split" for r in t["rows"]):
            continue
        hs = t["header"]
        ti = t["table_index"]
        for r in t["rows"]:
            ri = r["row_index"]
            base = r.get("base", {})
            decision = r["decision"]
            if decision == "split":
                for it in r["sub_items"]:
                    units.append((ti, ri, {
                        "position_id": it["position_id"],
                        "kind": "split",
                        "strict": True,
                        "lot_no": base.get("品目号"),
                        "category": base.get("品目名称"),
                        "target": it.get("product_service_name")
                                  or base.get("采购标的"),
                        "quantity": base.get("数量（单位）"),
                        "product_service_name": it.get("product_service_name"),
                        "brand": it.get("brand"),
                        "spec_model": it.get("spec_model"),
                        "unit_price": it.get("unit_price"),
                        "total_price": it.get("total_price"),
                    }))
            elif decision == "normal":
                units.append((ti, ri, {
                    "position_id": f"t{ti}-r{ri}-s1",
                    "kind": "normal",
                    "strict": True,
                    "lot_no": base.get("品目号") or _row_col(r, hs, "lot_no"),
                    "category": base.get("品目名称") or _row_col(r, hs, "category"),
                    "target": base.get("采购标的") or _row_col(r, hs, "target")
                               or _row_col(r, hs, "name_fallback")
                               or _row_col(r, hs, "name"),
                    "quantity": base.get("数量（单位）") or _row_col(r, hs, "quantity"),
                    "product_service_name": _row_col(r, hs, "target")
                                            or _row_col(r, hs, "name_fallback")
                                            or _row_col(r, hs, "name"),
                    "brand": _row_col(r, hs, "brand"),
                    "spec_model": _row_col(r, hs, "spec"),
                    "unit_price": _row_col(r, hs, "unit_price"),
                    "total_price": _row_col(r, hs, "total_price"),
                }))
            elif decision == "keep_raw":
                units.append((ti, ri, {
                    "position_id": f"t{ti}-r{ri}-raw",
                    "kind": "raw",
                    "strict": False,
                    "lot_no": base.get("品目号") or _row_col(r, hs, "lot_no"),
                    "category": base.get("品目名称") or _row_col(r, hs, "category"),
                    "target": base.get("采购标的") or _row_col(r, hs, "target")
                               or _row_col(r, hs, "name_fallback")
                               or _row_col(r, hs, "name"),
                    "quantity": base.get("数量（单位）") or _row_col(r, hs, "quantity"),
                    "product_service_name": _row_col(r, hs, "target")
                                            or _row_col(r, hs, "name_fallback")
                                            or _row_col(r, hs, "name"),
                    "raw_brand": _row_col(r, hs, "brand"),
                    "raw_spec": _row_col(r, hs, "spec"),
                }))
    return units


def _pack_unit_groups(units, max_items):
    """按结构边界把单元打包为组，每组 ≤ max_items 个单元。

    规则：
      - 同一 split 行的 strict 单元按 max_items 切片（一个子项绝不跨组）；
      - normal/raw 行的单元按文档顺序缓冲，凑满 max_items 成组，
        遇到 split 行先 flush；
      - 组内单元顺序 = 原始 (表,行,子项) 顺序。
    """
    # 先按连续 (表,行) 归并成行级段
    runs = []
    for x in units:
        ti, ri, u = x
        if runs and runs[-1][0] == (ti, ri):
            runs[-1][1].append(x)
        else:
            runs.append([(ti, ri), [x]])

    groups = []
    pending = []          # normal/raw 行的缓冲单元
    pending_strict = True

    def flush():
        nonlocal pending, pending_strict
        if pending:
            groups.append({"units": pending, "strict": pending_strict})
            pending, pending_strict = [], True

    for (ti, ri), row_units in runs:
        if row_units[0][2]["kind"] == "split":
            flush()
            for k in range(0, len(row_units), max_items):
                groups.append({"units": row_units[k:k + max_items],
                               "strict": True})
        else:
            for x in row_units:
                pending.append(x)
                pending_strict = pending_strict and x[2]["strict"]
                if len(pending) >= max_items:
                    flush()
    flush()
    return groups


# ================================================================ 渲染

def _lot_header(u):
    return [
        f"【宗/包】{u.get('lot_no') or ''}",
        f"【品目名称】{u.get('category') or ''}",
        f"【采购标的】{u.get('target') or ''}",
        f"【数量（单位）】{u.get('quantity') or ''}",
    ]


def _lot_key(u):
    return (u.get("lot_no"), u.get("category"), u.get("target"), u.get("quantity"))


def render_structured_chunk(units):
    """把一组单元渲染为 chunk 文本。

    每个子项块带稳定的「位置：t..-r..-s..」标记：
      - 给模型区分不同位置上完全相同的条目（如多个「讯方/定制」）；
      - 位置标记仅出现在输入文本，模型不回传，无输出污染。
    宗/包头随每个新 lot 组重复携带，保证 chunk 上下文完整。
    """
    lines = []
    last_key = object()
    for idx, (_, _, u) in enumerate(units, start=1):
        key = _lot_key(u)
        if key != last_key:
            lines.extend(_lot_header(u))
            last_key = key
        if u["kind"] == "raw":
            lines.append(f"【子项{idx}】位置：{u['position_id']}（HTML结构无法确定"
                         f"品牌↔型号对应关系，按原文保留，请勿猜测配对）")
            if u.get("product_service_name"):
                lines.append(f"产品名称：{u['product_service_name']}")
            lines.append(f"品牌原文：{u.get('raw_brand') or ''}")
            lines.append(f"规格型号原文：{u.get('raw_spec') or ''}")
        else:
            lines.append(f"【子项{idx}】位置：{u['position_id']}")
            if u.get("product_service_name"):
                lines.append(f"产品名称：{u['product_service_name']}")
            lines.append(f"品牌：{u.get('brand') or ''}")
            lines.append(f"规格型号：{u.get('spec_model') or ''}")
            if u.get("unit_price"):
                lines.append(f"单价：{u['unit_price']}")
            if u.get("total_price"):
                lines.append(f"总价：{u['total_price']}")
    return "\n".join(lines).strip()


def build_plan(html, announcement_id=None, max_items=MAX_ITEMS_PER_CHUNK,
               max_chunk_chars=MAX_CHUNK_CHARS):
    """解析 HTML → 抽取计划。

    返回 plan：
      mode=legacy:     {chunks:[{kind:'context', text}], legacy_text}
      mode=structured: {chunks:[{kind:'context'|'products', text, units, strict}],
                        units_all, split_tables, stats}
    """
    parsed = parse_announcement(html, announcement_id=announcement_id)
    split_tables = [t["table_index"] for t in parsed["tables"]
                    if t["table_type"] == "product"
                    and any(r["decision"] == "split" for r in t["rows"])]

    if not split_tables:
        legacy_text = preprocess_text(html)
        return {
            "mode": "legacy",
            "announcement_id": announcement_id,
            "legacy_text": legacy_text,
            "chunks": [{"kind": "context", "text": c}
                       for c in chunk_text(legacy_text, max_chunk_chars)],
            "stats": parsed["stats"],
        }

    # 结构化模式：从 soup 中移除已结构化的产品表，其余内容沿用原清洗
    soup = BeautifulSoup(html, "html.parser")
    html_tables = soup.find_all("table")
    for ti in split_tables:
        html_tables[ti].decompose()
    context_text = preprocess_text(str(soup))

    units = _build_units(parsed)
    groups = _pack_unit_groups(units, max_items)

    chunks = [{"kind": "context", "text": c, "units": [], "strict": False}
              for c in chunk_text(context_text, max_chunk_chars)]
    overflow = []
    for g in groups:
        text = render_structured_chunk(g["units"])
        if len(text) > MAX_STRUCT_CHUNK_CHARS:
            if g["strict"] and len(g["units"]) > 1:
                # 沿子项边界继续二分，绝不切断单个子项
                mid = len(g["units"]) // 2
                for sub in (g["units"][:mid], g["units"][mid:]):
                    chunks.append({"kind": "products", "strict": True,
                                   "units": sub, "text": render_structured_chunk(sub)})
                overflow.append({"position": g["units"][0][2]["position_id"],
                                 "action": "split_at_unit_boundary",
                                 "length": len(text)})
                continue
            overflow.append({"position": g["units"][0][2]["position_id"],
                             "action": "single_unit_oversize",
                             "length": len(text)})
        chunks.append({"kind": "products", "text": text,
                       "units": g["units"], "strict": g["strict"]})

    n_strict = sum(1 for _, _, u in units if u["strict"])
    n_raw = len(units) - n_strict
    return {
        "mode": "structured",
        "announcement_id": announcement_id,
        "chunks": chunks,
        "units_all": units,
        "split_tables": split_tables,
        "overflow": overflow,
        "stats": {
            **parsed["stats"],
            "strict_units": n_strict,
            "raw_units": n_raw,
            "product_chunks": sum(1 for c in chunks if c["kind"] == "products"),
            "context_chunks": sum(1 for c in chunks if c["kind"] == "context"),
            "max_items_per_chunk": max(
                (len(c["units"]) for c in chunks if c["kind"] == "products"),
                default=0),
        },
    }


# ================================================================ 位置对齐

def _norm(s):
    return re.sub(r"\s+", "", str(s or "")).lower()


def _structural_product(u):
    """模型漏返回时，用预处理器机械已知字段补齐该位置（不编造原文没有的内容）。"""
    return {
        "product_service_name": u.get("product_service_name") or u.get("target"),
        "category": u.get("category"),
        "brand": u.get("brand"),
        "spec_model": u.get("spec_model"),
        "unit_price": _to_number(u.get("unit_price")),
        "quantity": _to_number(u.get("quantity")),
        "total_price": _to_number(u.get("total_price")),
    }


def align_products(units, products, chunk_tag="x"):
    """把一个 products chunk 的模型输出对齐到 chunk 内结构位置。

    返回 [(product_dict, position_id, source)]，source ∈
      model           : 模型返回且成功绑定到已知位置
      structural      : 该位置模型漏返回，用机械字段补齐
      raw             : 绑定到 keep_raw 单元（位置+内容定位置，天然去重）
      extra           : 模型多抽、无法归属（merge 时走全字段去重）
    对齐只依赖 (品牌,规格型号,名称)，不修改产品字段值。
    """
    strict = [(ti, ri, u) for ti, ri, u in units if u["strict"]]
    raw = [(ti, ri, u) for ti, ri, u in units if not u["strict"]]
    pool = list(products)  # 同一对象：bound 以 id() 为键，末尾按原列表查标记
    bound = {}             # id(pred) -> (pid, source)

    def take(preds, matcher):
        for ui, (_, _, u) in enumerate(preds):
            for j, p in enumerate(pool):
                if p is None or id(p) in bound:
                    continue
                if matcher(u, p):
                    bound[id(p)] = (u["position_id"], "model")
                    preds[ui] = None
                    pool[j] = None
                    break
        preds[:] = [x for x in preds if x is not None]

    # Pass 1：(品牌, 规格) 全等，含 None 键时要求至少一端非空
    def exact_match(u, p):
        bk, sk = _norm(u.get("brand")), _norm(u.get("spec_model"))
        if not bk and not sk:
            return False
        return (bk or None) == (_norm(p.get("brand")) or None) and \
               (sk or None) == (_norm(p.get("spec_model")) or None) and \
               (bk or sk)

    take(strict, exact_match)

    # Pass 2：品牌/规格/名称 单端相同
    def soft_match(u, p):
        ub, us, un = _norm(u.get("brand")), _norm(u.get("spec_model")), \
                     _norm(u.get("product_service_name") or u.get("target"))
        pb, ps, pn = _norm(p.get("brand")), _norm(p.get("spec_model")), \
                     _norm(p.get("product_service_name"))
        return ((ub and ub == pb) or (us and us == ps)
                or (un and pn and (un == pn or un in pn or pn in un)))

    take(strict, soft_match)

    # Pass 3：剩余模型产品归属 keep_raw 单元（内容落在原文串内即可）
    out = []
    for j, p in enumerate(list(pool)):
        if p is None or id(p) in bound:
            continue
        pb, ps, pn = _norm(p.get("brand")), _norm(p.get("spec_model")), \
                     _norm(p.get("product_service_name"))
        target_u = None
        for _, _, u in raw:
            rb = _norm(u.get("raw_brand"))
            rs = _norm(u.get("raw_spec"))
            rn = _norm(u.get("product_service_name"))
            if ((pb and rb and pb in rb) or (ps and rs and ps in rs)
                    or (pn and rn and (pn == rn or pn in rn))):
                target_u = u
                break
        if target_u is not None:
            h = hashlib.md5(
                "|".join(str(p.get(k)) for k in
                         ("product_service_name", "brand", "spec_model",
                          "unit_price", "quantity", "total_price")).encode(
                    "utf-8")).hexdigest()[:10]
            # 位置+内容：同 raw 位置的同内容折叠，不同 raw 位置天然分离
            pid = f"{target_u['position_id']}#{h}"
            bound[id(p)] = (pid, "raw")
            pool[j] = None

    # 组装：按结构位置顺序输出（模型绑定优先，漏返回机械补齐）
    model_by_pid = {}
    for p in products:
        b = bound.get(id(p))
        if b:
            model_by_pid.setdefault(b[0], p)
    for _, _, u in [x for x in units if x[2]["strict"]]:
        p = model_by_pid.get(u["position_id"])
        if p is not None:
            out.append((p, u["position_id"], "model"))
        else:
            out.append((_structural_product(u), u["position_id"], "structural"))
    # raw 绑定（内容 hash 位置，同内容折叠、异内容保留）
    for p in products:
        b = bound.get(id(p))
        if b and b[1] == "raw":
            out.append((p, b[0], "raw"))
    # 多抽：无结构位置，merge 时走全字段去重；pid 仅作块内序号
    extra_n = 0
    for p in products:
        if id(p) not in bound:
            extra_n += 1
            out.append((p, f"extra-{chunk_tag}-{extra_n}", "extra"))
    return out


# ================================================================ merge

def _product_tuple(p):
    return tuple(p.get(k) for k in (
        "product_service_name", "category", "brand", "spec_model",
        "unit_price", "quantity", "total_price"))


def _merge_product_fields(old, new):
    """同位置重复返回：字段级取第一个非 null。"""
    for k in ("product_service_name", "category", "brand", "spec_model",
              "unit_price", "quantity", "total_price"):
        if old.get(k) is None and new.get(k) is not None:
            old[k] = new[k]
    return old


def merge_chunk_results(chunk_payloads, announcement_id, file_id, source_file):
    """合并多个 chunk 结果（结构位置优先）。

    chunk_payloads: [{"result": <模型结果dict|None>,
                      "aligned": [(product, position_id, source)] | None}]
      aligned=None : 无结构位置 chunk（legacy/context），products 全字段去重；
      aligned 非空 : 直接使用对齐产物（已按结构位置排序、含机械补齐）。

    合并规则：
      - 项目级标量：第一个非 null（与历史一致）
      - winning_suppliers/bid_participants：有序并集去重（与历史一致）
      - products：
          有结构位置 → 按位置去重；不同位置即使 7 字段完全相同也全部保留；
          extra/无位置 → 按全字段元组去重（历史行为，保持普通公告不变）
    返回前剥离全部内部字段。
    """
    merged = empty_result(announcement_id, file_id, source_file)
    win, bid = [], []
    pos_products = {}        # position_id -> product
    pos_order = []
    legacy_products = []     # 无位置产品
    same_position_dedup = 0

    def merge_scalars(result):
        if result is None:
            return
        for f in ("project_name", "project_code", "purchaser", "project_amount"):
            if merged[f] is None and result.get(f) is not None:
                merged[f] = result[f]
        for s in result.get("winning_suppliers", []):
            if s not in win:
                win.append(s)
        for s in result.get("bid_participants", []):
            if s not in bid:
                bid.append(s)

    for payload in chunk_payloads:
        result = payload.get("result")
        aligned = payload.get("aligned")
        merge_scalars(result)

        if aligned is None:
            for p in (result or {}).get("products", []):
                if all(_product_tuple(p) != _product_tuple(q) for q in legacy_products):
                    legacy_products.append(dict(p))
            continue

        for p, pid, src in aligned:
            # 无结构位置的多抽产品：按全字段去重，避免跨 chunk 幻觉重复
            if src == "extra":
                if all(_product_tuple(p) != _product_tuple(q) for q in legacy_products):
                    legacy_products.append(dict(p))
                continue
            if pid in pos_products:
                pos_products[pid] = _merge_product_fields(pos_products[pid], p)
                same_position_dedup += 1  # 同一位置在多 chunk 重复返回
            else:
                pos_products[pid] = dict(p)
                pos_order.append(pid)

    merged["winning_suppliers"] = win
    merged["bid_participants"] = bid
    products = [pos_products[pid] for pid in pos_order] + legacy_products
    merged["products"] = products
    merged["_merge_stats"] = {
        "position_products": len(pos_order),
        "legacy_products": len(legacy_products),
        "same_position_dedup": same_position_dedup,
    }
    return merged


def legacy_merge(chunk_results, announcement_id, file_id, source_file):
    """历史 merge：products 按全字段去重（供 legacy 路径逐字保持行为）。"""
    merged = merge_chunk_results(
        [{"result": r, "aligned": None} for r in chunk_results],
        announcement_id, file_id, source_file)
    merged.pop("_merge_stats", None)
    return merged


# ================================================================ 主流程

def _extract_with_retry(extractor, text, ann, idx, source_file, retries=2):
    """单 chunk 调用：失败立即重试（截断位置不稳定，重试常成功）。"""
    last = None
    for attempt in range(retries + 1):
        try:
            return extractor.extract(text, announcement_id=f"{ann}_c{idx}",
                                     file_id="", source_file=source_file)
        except Exception as e:  # noqa: BLE001 - 保留原始报错由 extractor 打印
            last = e
    raise last


def process_html(html, announcement_id, extractor, file_id="", source_file="",
                 max_items=MAX_ITEMS_PER_CHUNK):
    """完整主流程：HTML → 结果记录。

    返回 record（兼容 run_baseline 记录结构）：
      announcement_id/source_file/mode/chunks_count/api_calls/
      elapsed_seconds/status/error/result/raw_responses/chunk_errors/
      plan_stats/alignment_stats
    """
    t0 = time.time()
    record = {
        "announcement_id": announcement_id,
        "source_file": source_file,
        "mode": None,
        "chunks_count": 0,
        "api_calls": 0,
        "elapsed_seconds": 0.0,
        "status": "success",
        "error": None,
        "result": None,
        "raw_responses": [],
        "chunk_errors": [],
        "plan_stats": None,
        "alignment_stats": None,
        "merge_stats": None,
        "position_trace": [],
    }
    try:
        plan = build_plan(html, announcement_id=announcement_id, max_items=max_items)
        record["mode"] = plan["mode"]
        record["plan_stats"] = plan.get("stats")
        tagged_chunks = []
        align_stats = {"model_bound": 0, "structural_backfill": 0,
                       "raw_bound": 0, "extra": 0, "chunk_failures": 0}

        if plan["mode"] == "legacy":
            # 与历史 V1 逐字一致：逐块单次调用，全字段去重
            results = []
            for idx, ch in enumerate(plan["chunks"]):
                try:
                    r = extractor.extract(
                        ch["text"], announcement_id=f"{announcement_id}_c{idx}",
                        file_id=file_id, source_file=source_file)
                    results.append(r)
                    record["raw_responses"].append(r)
                    record["api_calls"] += 1
                except Exception as e:  # noqa: BLE001
                    record["chunk_errors"].append(f"chunk[{idx}]: {e}")
            record["chunks_count"] = len(plan["chunks"])
            if results:
                record["result"] = legacy_merge(
                    results, announcement_id, file_id, source_file)
            else:
                record["status"] = "failed"
                record["error"] = ("所有 chunk 抽取均失败"
                                   if record["chunk_errors"] else "无有效 chunk")
            record["elapsed_seconds"] = round(time.time() - t0, 2)
            return record

        # structured：context 块无位置；products 块按位置对齐
        def run_product_chunk(units, text, idx, depth=0):
            """返回 {"result","aligned"} 或其列表；失败沿子项边界再分。"""
            try:
                r = _extract_with_retry(extractor, text, announcement_id,
                                        idx, source_file)
                tags = align_products(units, r.get("products", []),
                                      chunk_tag=str(idx))
                return {"result": r, "aligned": tags}
            except Exception as e:  # noqa: BLE001
                strict_units = [x for x in units if x[2]["strict"]]
                if len(units) > 1 and len(strict_units) == len(units) and depth < 5:
                    mid = len(units) // 2
                    outs = []
                    for k, sub in enumerate((units[:mid], units[mid:])):
                        part = run_product_chunk(
                            sub, render_structured_chunk(sub),
                            f"{idx}.{k}", depth + 1)
                        if part is not None:
                            outs.extend(part if isinstance(part, list) else [part])
                    return outs
                record["chunk_errors"].append(
                    f"chunk[{idx}] {type(e).__name__}: {e}")
                align_stats["chunk_failures"] += 1
                return None

        chunk_seq = 0
        for ch in plan["chunks"]:
            if ch["kind"] == "context":
                try:
                    r = _extract_with_retry(extractor, ch["text"], announcement_id,
                                            chunk_seq, source_file)
                    tagged_chunks.append({"result": r, "aligned": None})
                    record["raw_responses"].append(r)
                    record["api_calls"] += 1
                except Exception as e:  # noqa: BLE001
                    record["chunk_errors"].append(f"chunk[{chunk_seq}]: {e}")
                    align_stats["chunk_failures"] += 1
                chunk_seq += 1
                continue

            out = run_product_chunk(ch["units"], ch["text"], chunk_seq)
            chunk_seq += 1
            if out is None:
                continue
            outs = out if isinstance(out, list) else [out]
            for payload in outs:
                tagged_chunks.append(payload)
                r = payload["result"]
                record["raw_responses"].append(r)
                record["api_calls"] += 1
                for p, pid, src in payload["aligned"]:
                    record["position_trace"].append({
                        "position": pid, "source": src,
                        "brand": p.get("brand"),
                        "spec_model": p.get("spec_model"),
                        "product_service_name": p.get("product_service_name"),
                    })
                    if src == "model":
                        align_stats["model_bound"] += 1
                    elif src == "structural":
                        align_stats["structural_backfill"] += 1
                    elif src == "raw":
                        align_stats["raw_bound"] += 1
                    else:
                        align_stats["extra"] += 1

        record["chunks_count"] = chunk_seq
        record["alignment_stats"] = align_stats
        if tagged_chunks:
            merged = merge_chunk_results(
                tagged_chunks, announcement_id, file_id, source_file)
            record["merge_stats"] = merged.pop("_merge_stats", None)
            record["result"] = merged
            if record["chunk_errors"]:
                record["status"] = "partial_failed"
                record["error"] = "; ".join(str(x) for x in record["chunk_errors"])
        else:
            record["status"] = "failed"
            record["error"] = "所有 chunk 抽取均失败"
    except Exception as e:  # noqa: BLE001
        record["status"] = "failed"
        record["error"] = f"{type(e).__name__}: {e}"
    finally:
        record["elapsed_seconds"] = round(time.time() - t0, 2)
    return record
