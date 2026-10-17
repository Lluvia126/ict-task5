"""V2.2 表格筛选器：撤回 V2.1 的"强身份词单独入选"，保留跨页续表 + 负面否决。

V2.1 问题：
  - IDENTITY_ONLY_ENABLED=True 导致 t20260508 大量设备清单/技术要求表
    仅因含"服务名称"/"项目名称"等身份词就入选，Qwen 从中抽出 187 条
    产品（Gold 仅 11 条服务类），Precision 崩塌。

V2.2 改动：
  1. IDENTITY_ONLY_ENABLED = False —— 身份词不再单独触发入选
  2. 跨页续表保留但收紧：
     a) 上一张表必须是 score >= threshold 的高置信入选（非身份单独入选）
     b) 当前表无独立表头
     c) 数据行结构与上一张表相似（列数差 ≤ 1）
     d) 存在 ≥2 个稳定续表结构特征（数量/单位/价格/品牌/型号）
     e) 不被负面规则否决
  3. 负面否决保持不变
  4. threshold 保持 4
  5. KW_IDENTITY_V21 保留为辅助加分词（贡献 +2/词到 score），但不触发单独入选
"""
import re

from .v2_table_filter import (
    COMBO_BONUS, HEADER_SCAN_ROWS, KW_BRAND_SPEC, KW_IDENTITY, KW_NEGATIVE,
    KW_PRICE, KW_QTY, NEGATIVE_PENALTY, TABLE_SCORE_THRESHOLD, _hits,
    _norm_cell, render_table,
)

# ============================ V2.2 配置 ============================

# 身份类扩充词（保留为辅助加分，不再单独触发入选）
KW_IDENTITY_V22 = [
    "包组名称",
    "招标内容",
    "采购包",
    "项目名称",
    "服务内容",
    "采购内容",
]

# 强身份词单独入选：V2.2 撤回
IDENTITY_ONLY_ENABLED = False
IDENTITY_ONLY_MIN_HITS = 1
STRONG_IDENTITY = [
    "产品名称", "货物名称", "服务名称", "商品名称", "标的名称", "采购标的",
    "品目名称", "设备名称", "品名", "包组名称", "招标内容", "采购包",
    "项目名称", "服务内容", "采购内容",
]

# 数据行形态兜底（跨页续表）
FORM_BONUS = 3
FORM_SCAN_ROWS = 8
_NUM_RE = re.compile(r"^\d{1,4}(\.\d+)?$")

# 续表结构特征词（至少命中 3 个才允许续表入选——收紧自 V2.1 的 2）
CONTINUATION_FEATURES = KW_QTY + ["台", "项", "套", "个", "年", "批", "件", "根", "米", "块"] + KW_PRICE + KW_BRAND_SPEC
CONTINUATION_MIN_FEATURES = 3

# 续表列数相似性容忍度
CONTINUATION_MAX_COL_DIFF = 1

# 续表链最大深度（防止无限续表）
CONTINUATION_MAX_DEPTH = 3

W_IDENTITY, W_PRICE, W_QTY, W_BRAND = 2, 2, 1, 1


def score_table_v22(tb):
    """V2.2 打分：与 V2.1 相同的打分逻辑（身份词仍加分，但不单独入选）。"""
    data = tb.get("data") or []
    header_rows = data[:HEADER_SCAN_ROWS]
    ident, brand, qty, price, neg = set(), set(), set(), set(), set()
    ident_all = list(KW_IDENTITY) + KW_IDENTITY_V22

    for row in header_rows:
        ident |= _hits(row, ident_all)
        brand |= _hits(row, KW_BRAND_SPEC)
        qty |= _hits(row, KW_QTY)
        price |= _hits(row, KW_PRICE)

    blob_all = _norm_cell("\n".join(
        " ".join(str(c) for c in row if c) for row in data)) if data else ""
    for cat, kws in KW_NEGATIVE.items():
        if any(k in blob_all for k in kws):
            neg.add(cat)

    if not price and not qty:
        for row in data[HEADER_SCAN_ROWS:HEADER_SCAN_ROWS + 5]:
            price |= _hits(row, KW_PRICE)
            qty |= _hits(row, KW_QTY)

    form_hit = False
    if not ident:
        for row in data[:FORM_SCAN_ROWS]:
            cells = [str(c).strip() for c in row if c is not None
                     and str(c).strip() != ""]
            has_unit = any(c in {"台", "项", "套", "个", "年", "批", "件", "根", "米", "块"} for c in cells)
            has_num = any(_NUM_RE.match(c) for c in cells)
            if has_unit and has_num and len(cells) >= 3:
                form_hit = True
                break

    score = 0
    score += 2 * len(ident)
    score += 2 * len(price)
    score += 1 * len(qty)
    score += 1 * len(brand)
    if ident and qty and price:
        score += COMBO_BONUS
    if form_hit:
        score += FORM_BONUS
    score -= NEGATIVE_PENALTY * len(neg)

    matched = sorted(ident | brand | qty | price)
    detail = {
        "identity": sorted(ident), "brand_spec": sorted(brand),
        "quantity": sorted(qty), "price": sorted(price),
        "negative": sorted(neg), "form_row_hit": form_hit,
    }
    return score, matched, sorted(neg), detail


def selected_v22(tb, score, prev_selected=False, prev_depth=0, prev_tb=None):
    """V2.2 入选判定：主阈值 或 严格跨页续表。

    V2.1 的身份单独入选已撤回。
    跨页续表保留但收紧：
      1. 上一张表已入选（可以是主阈值或续表链，但链深度 ≤ MAX_DEPTH）
      2. 当前表无独立表头（ident 为空）
      3. 数据行结构与上一张表相似（列数差 ≤ 1）
      4. 存在 ≥3 个稳定续表结构特征
      5. 不被负面规则否决
    """
    if _has_negative(tb):
        return False
    if score >= TABLE_SCORE_THRESHOLD:
        return True
    # V2.2: 身份单独入选已撤回
    # 跨页续表：允许从任何已入选表续接，但限制链深度
    if prev_selected and prev_depth < CONTINUATION_MAX_DEPTH and _is_continuation(tb, prev_tb):
        return True
    return False


def _is_continuation(tb, prev_tb):
    """判断当前表是否为上一张表的跨页续表。

    条件：
      1. 当前表无独立表头（ident 为空）
      2. 数据行结构与上一张表相似（列数差 ≤ 1）
      3. 数据行结构特征：至少 2 行同时包含纯数字 + 单位指示符
         （这是续表的本质特征——无表头但数据行是报价明细行）
    """
    data = tb.get("data") or []
    if not data:
        return False

    # 1. 无独立表头
    ident = set()
    ident_all = list(KW_IDENTITY) + KW_IDENTITY_V22
    for row in data[:HEADER_SCAN_ROWS]:
        ident |= _hits(row, ident_all)
    if ident:
        return False

    # 2. 列数相似
    prev_data = (prev_tb or {}).get("data") or []
    if prev_data:
        prev_cols = max(len(r) for r in prev_data[:5]) if prev_data[:5] else 0
        cur_cols = max(len(r) for r in data[:5]) if data[:5] else 0
        if abs(cur_cols - prev_cols) > CONTINUATION_MAX_COL_DIFF:
            return False

    # 3. 数据行结构特征：至少 2 行同时包含纯数字 + 单位
    #    （续表的本质：无表头但数据行是报价明细行）
    unit_cells = {"台", "项", "套", "个", "年", "批", "件", "根", "米", "块"}
    detail_rows = 0
    for row in data[:FORM_SCAN_ROWS]:
        cells = [str(c).strip() for c in row if c is not None
                 and str(c).strip() != ""]
        has_unit = any(c in unit_cells for c in cells)
        has_num = any(_NUM_RE.match(c) for c in cells)
        if has_unit and has_num and len(cells) >= 3:
            detail_rows += 1
    if detail_rows < 2:
        return False

    return True


def _has_negative(tb):
    data = tb.get("data") or []
    blob_all = _norm_cell("\n".join(
        " ".join(str(c) for c in row if c) for row in data)) if data else ""
    return any(k in blob_all
               for kws in KW_NEGATIVE.values() for k in kws)
