"""三表视图构建 + CSV 导出（对齐《输出标准样例表.xlsx》）。

数据来源：已入库的结构化抽取结果（extractions.raw_json），
按 总表 / 标的表 / 投标人表 三个视图重新组织，**不重新调用 AI 抽取**。
"""

from __future__ import annotations

import csv
import io
import re

from services.format_rules import (
    BIDDER_HEADERS,
    ITEM_HEADERS,
    LIST_SEP,
    SUMMARY_HEADERS,
    UNASSIGNED,
    VIEW_ALIASES,
    VIEW_ORDER,
    VIEWS,
    build_uuid_map,
    detect_bid_packages,
    detect_notice_id,
    detect_record_type,
    fmt_amount,
    fmt_price,
    fmt_qty,
    html_title,
    parse_package_awards,
)


def _text(value) -> str:
    """任意值 -> 单元格文本（列表用"、"连接，None -> 空串）。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return LIST_SEP.join(_text(v) for v in value if v is not None and str(v).strip())
    return str(value).strip()


def _names(values) -> list:
    """去重后的非空字符串列表（保持原顺序）。"""
    out = []
    for v in values or []:
        s = _text(v)
        if s and s not in out:
            out.append(s)
    return out


def resolve_packages(data: dict) -> list:
    """标段/合同包：结构化 packages > 解析阶段检测到的分包名 > "未分包"。

    每个元素形如 {"name": 标段名, "suppliers": [...]|None, "amount": 元|None}。
    """
    pkgs = data.get("packages")
    if isinstance(pkgs, list) and pkgs:
        out = []
        for p in pkgs:
            if isinstance(p, dict):
                name = _text(p.get("name") or p.get("标段") or p.get("package"))
                out.append({
                    "name": name or UNASSIGNED,
                    "suppliers": p.get("winning_suppliers") or p.get("suppliers"),
                    "amount": p.get("amount"),
                })
            else:
                name = _text(p)
                if name:
                    out.append({"name": name, "suppliers": None, "amount": None})
        if out:
            return out

    names = _names(data.get("bid_packages"))
    if len(names) == 1:
        return [{"name": names[0], "suppliers": None, "amount": None}]
    if len(names) > 1:
        # 有分包但无法归属到具体标段：保留全部名称，不编造金额
        return [{"name": LIST_SEP.join(names), "suppliers": None, "amount": None}]
    return [{"name": UNASSIGNED, "suppliers": None, "amount": None}]


def _same_org(a: str, b: str) -> bool:
    """单位名称宽松比对（去掉括号与空格后互相包含即算同一家）。"""
    def norm(x):
        return re.sub(r"[\s（）()【】\[\]]", "", _text(x))
    a, b = norm(a), norm(b)
    return bool(a) and bool(b) and (a == b or a in b or b in a)


def _validate_packages(parsed: list, winning_suppliers) -> bool:
    """只有解析结果与 AI 抽出的中标供应商对得上、且每个标段都有供应商+金额，才采信。"""
    if not (2 <= len(parsed) <= 20):
        return False
    for pkg in parsed:
        if not pkg.get("suppliers") or pkg.get("amount") is None:
            return False
    ai = _names(winning_suppliers)
    if ai:
        for pkg in parsed:
            if not any(_same_org(pkg["suppliers"][0], s) for s in ai):
                return False
    return True


def _assign_products(products: list, parsed: list) -> list:
    """把标的归属到标段：标的名称出现在某个标段的原文块里才算命中（不猜）。"""
    names = [pkg["name"] for pkg in parsed]
    hits = []
    pending = []
    for idx, prod in enumerate(products):
        pname = _text(prod.get("product_service_name"))
        matched = None
        if pname:
            cands = [pkg["name"] for pkg in parsed if pname in (pkg.get("_block_text") or "")]
            if len(cands) == 1:
                matched = cands[0]
        hits.append(matched)
        if not matched:
            pending.append(idx)
    if pending and len(pending) == len(parsed):
        for k, idx in enumerate(pending):
            hits[idx] = names[k]
    return [dict(prod, package=hits[i] or names[0]) for i, prod in enumerate(products)]


def apply_package_awards(data: dict, text: str) -> dict:
    """用公告原文补出"每个标段/合同包的中标供应商 + 金额"（规则解析，不调用 AI）。"""
    parsed = parse_package_awards(text or "")
    if not _validate_packages(parsed, data.get("winning_suppliers")):
        return data
    out = dict(data)
    out["packages"] = [
        {"name": pkg["name"], "suppliers": pkg["suppliers"], "amount": pkg["amount"]}
        for pkg in parsed
    ]
    out["bid_packages"] = [pkg["name"] for pkg in parsed]
    out["uuid_map"] = build_uuid_map(out.get("notice_id") or "", out["bid_packages"])
    products = [p for p in (out.get("products") or []) if isinstance(p, dict)]
    if products:
        out["products"] = _assign_products(products, parsed)
    return out


def build_views(data: dict) -> dict:
    """构建三表视图：{"summary": [[...], ...], "items": [...], "bidders": [...]}。

    每个视图第一行为表头，列名与样例表逐字一致。
    """
    notice_id = _text(data.get("notice_id")) or detect_notice_id(data.get("source_file"))
    project_name = _text(data.get("project_name"))
    purchaser = _text(data.get("purchaser"))
    suppliers = _names(data.get("winning_suppliers"))
    participants = _names(data.get("bid_participants"))
    products = [p for p in (data.get("products") or []) if isinstance(p, dict)]
    record_type = _text(data.get("record_type")) or detect_record_type(project_name=project_name)

    packages = resolve_packages(data)
    uuid_map = data.get("uuid_map") or build_uuid_map(notice_id, [p["name"] for p in packages])

    # ---- 总表：一行一个标段/合同包 ----
    summary = [list(SUMMARY_HEADERS)]
    for pkg in packages:
        name = pkg["name"]
        row_suppliers = _names(pkg.get("suppliers")) or suppliers
        amount = pkg.get("amount") if pkg.get("amount") is not None else data.get("project_amount")
        summary.append([
            uuid_map.get(name, notice_id),
            name,
            project_name,
            purchaser,
            LIST_SEP.join(row_suppliers),
            fmt_amount(amount),
        ])

    # ---- 标的表：一条标的一行（单价/总价单位万元，类别=中标/入围/需求） ----
    pkg_names = [p["name"] for p in packages]
    assigned = [_text(p.get("package")) for p in products]
    if any(a in pkg_names for a in assigned):
        # 有明确归属：未归属的兜底放到第一个标段，保证不丢行
        assigned = [a if a in pkg_names else pkg_names[0] for a in assigned]
    elif len(products) == len(pkg_names):
        assigned = list(pkg_names)          # 数量一致：按顺序 1:1
    else:
        assigned = [pkg_names[0]] * len(products)

    items = [list(ITEM_HEADERS)]
    for pkg in packages:
        name = pkg["name"]
        uuid = uuid_map.get(name, notice_id)
        for idx, prod in enumerate(products):
            if assigned[idx] != name:
                continue
            items.append([
                uuid,
                name,
                _text(prod.get("product_service_name")),
                _text(prod.get("category")),
                _text(prod.get("brand")),
                _text(prod.get("spec_model")),
                fmt_qty(prod.get("quantity")),
                fmt_price(prod.get("unit_price")),
                fmt_price(prod.get("total_price")),
                record_type,
            ])

    # ---- 投标人表：一行一个投标/响应主体 ----
    bidders = [list(BIDDER_HEADERS)]
    for pkg in packages:
        name = pkg["name"]
        uuid = uuid_map.get(name, notice_id)
        row_suppliers = _names(pkg.get("suppliers")) or suppliers
        amount = pkg.get("amount") if pkg.get("amount") is not None else data.get("project_amount")
        quote = fmt_amount(amount)
        for winner in row_suppliers:
            # 中标人即中标供应商，报价按中标金额回填；通过审查为"通过"
            bidders.append([uuid, name, winner, "中标人", "通过", quote])
        for other in participants:
            if other in row_suppliers:
                continue
            # 参与人：原文未披露报价/审查结果，留空
            bidders.append([uuid, name, other, "参与人", "", ""])

    return {"summary": summary, "items": items, "bidders": bidders}


def merge_views(views_list) -> dict:
    """把多条记录的三表视图合并为一份（表头只保留一次）。"""
    merged = {}
    for key in VIEW_ORDER:
        rows = []
        for views in views_list:
            part = views.get(key) or []
            if not part:
                continue
            rows.extend(part[1:] if rows else part)
        if rows:
            merged[key] = rows
    return merged


def rows_to_csv(rows) -> str:
    """二维数组 -> CSV 文本（CRLF 行尾，Excel 友好）。"""
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\r\n").writerows(rows)
    return buf.getvalue()


def views_to_csv(views: dict, view: str = "all") -> str:
    """视图 -> CSV 文本。view=all 时三个视图依次输出，各自带中文表名与小标题行。"""
    key = VIEW_ALIASES.get(_text(view) or "all")
    if key:
        return rows_to_csv(views.get(key) or [])
    parts = []
    for k in VIEW_ORDER:
        rows = views.get(k)
        if not rows:
            continue
        parts.append(VIEWS[k][0])
        parts.append(rows_to_csv(rows).rstrip("\r\n"))
        parts.append("")
    return "\r\n".join(parts)


def csv_bytes(text: str) -> bytes:
    """UTF-8 带 BOM，方便 Excel 直接打开不乱码。"""
    return ("\ufeff" + text).encode("utf-8")


def export_filename(notice_id: str = "", fallback: str = "", view: str = "all") -> str:
    base = _text(notice_id) or _text(fallback) or "extraction"
    key = VIEW_ALIASES.get(_text(view) or "all")
    if not key:
        return base + ".csv"
    return "%s_%s.csv" % (base, VIEWS[key][0])


def enrich_extraction(data: dict, *, filename: str = "", text: str = "",
                      html_raw: str = "") -> dict:
    """补齐导出所需的派生字段（纯规则，不调用 AI，不改变原抽取内容）。

    - notice_id     ：公告唯一标识（源文件名前缀）
    - record_type   ：中标 / 入围 / 需求
    - bid_packages  ：标段/合同包名称
    - uuid_map      ：标段 -> UUID 映射
    """
    out = dict(data or {})
    if not _text(out.get("notice_id")):
        out["notice_id"] = detect_notice_id(filename, out.get("source_file"))
    if not _text(out.get("record_type")):
        out["record_type"] = detect_record_type(
            title=html_title(html_raw),
            body=(text or "")[:2000],
            project_name=_text(out.get("project_name")),
        )
    if not out.get("bid_packages"):
        out["bid_packages"] = detect_bid_packages(text or "")
    if not out.get("uuid_map"):
        out["uuid_map"] = build_uuid_map(
            out["notice_id"], [p["name"] for p in resolve_packages(out)]
        )
    if not out.get("packages") and text:
        # 公告里写了"采购包N/合同包N -> 供应商 + 金额"时按标段拆开
        out = apply_package_awards(out, text)
    return out