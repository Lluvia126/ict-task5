"""V2.1 表格筛选器：在 V2 基础上恢复被误过滤的真实报价表。

改动基于 V2 漏抽归因（logs/attribution.json，37 张被过滤表命中 Gold 漏名）：
  - t20260206 表38/39：跨页表，表头（产品类别|产品名称|数量|单位）在第 37 张，
    第 38/39 张自身无表头 → 新增「数据行形态」兜底评分（数量列 + 单位列）。
  - t20260508 表8：表头=序号|服务名称|指标需求|重要程度 → 服务名称已命中身份，
    但缺价格/数量 → 新增身份类单独入选规则（threshold_identity_only）。
  - t20260512 表4 / t20260612 表1：包级汇总表（包号|包组名称|预算|限价）→
    新增身份词「包组名称」「招标内容」「采购包」「项目名称」（均为真实表头）。

新增词全部来自真实被误过滤表表头，未凭经验添加。
负面表（评分/审查/偏离/目录/技术参数）惩罚不变，threshold 主阈值保持 4。
"""
import re

from .v2_table_filter import (
    COMBO_BONUS, HEADER_SCAN_ROWS, KW_BRAND_SPEC, KW_IDENTITY, KW_NEGATIVE,
    KW_PRICE, KW_QTY, NEGATIVE_PENALTY, TABLE_SCORE_THRESHOLD, _hits,
    _norm_cell, render_table,
)

# ============================ V2.1 新增配置 ============================

# 身份类扩充词（全部来自真实被误过滤表表头）
KW_IDENTITY_V21 = [
    "包组名称",     # t20260512 表4（包级汇总）
    "招标内容",     # t20260612 表1（包级汇总）
    "采购包",       # t20260512（采购包预算金额）
    "项目名称",     # 多公告通用（V2 归因命中表出现）
    "服务内容",     # 服务类报价表真实表头
    "采购内容",     # 同上
]
# 数量/单位类扩充（跨页表数据行含"台/项/套/个/年"独立单元格）
QTY_UNIT_CELLS = {"台", "项", "套", "个", "年", "批", "件", "根", "米", "块"}

# 身份类单独入选：身份命中数 >= 该值 且 无负面特征 → 直接入选
# 依据：t20260508 表8（服务名称+指标需求）等无价格列的真实服务报价表
# V2.1 收紧：泛化词「名称」单独出现区分度过低（区分度0.02-0.1），
# 单独入选只认可「强身份词」（名称类必须带前缀或词长>2）
IDENTITY_ONLY_MIN_HITS = 1
IDENTITY_ONLY_ENABLED = True
STRONG_IDENTITY = [
    "产品名称", "货物名称", "服务名称", "商品名称", "标的名称", "采购标的",
    "品目名称", "设备名称", "品名", "包组名称", "招标内容", "采购包",
    "项目名称", "服务内容", "采购内容",
]

# 数据行形态兜底：非表头行中同时出现 数量列（纯数字1-9999）与 单位列 →
# 视为无表头跨页明细表，+FORM_BONUS
FORM_BONUS = 3
FORM_SCAN_ROWS = 8
_NUM_RE = re.compile(r"^\d{1,4}(\.\d+)?$")
# 身份/价格/数量词加权系数（V2.1 保持与 V2 一致的权重结构）
W_IDENTITY, W_PRICE, W_QTY, W_BRAND = 2, 2, 1, 1


def score_table_v21(tb):
    """V2.1 打分：V2 规则 + 新词 + 身份单独入选 + 跨页表数据行形态。"""
    data = tb.get("data") or []
    header_rows = data[:HEADER_SCAN_ROWS]
    ident, brand, qty, price, neg = set(), set(), set(), set(), set()
    ident_all = list(KW_IDENTITY) + KW_IDENTITY_V21

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

    # —— V2.1 新增：数据行形态（无表头跨页明细表，t20260206 表38/39 场景）
    form_hit = False
    if not ident:
        for row in data[:FORM_SCAN_ROWS]:
            cells = [str(c).strip() for c in row if c is not None
                     and str(c).strip() != ""]
            has_unit = any(c in QTY_UNIT_CELLS for c in cells)
            has_num = any(_NUM_RE.match(c) for c in cells)
            # 行内 ≥3 个有效单元格 且 同时出现数量与单位 → 明细行形态
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


def selected_v21(tb, score, prev_selected=False):
    """V2.1 入选判定：主阈值 或 身份类单独入选（无负面）或 FORM 兜底。

    FORM 兜底：本表无表头分（跨页续表），但同一附件存在紧邻的已入选表头表
    （prev_selected=上一张表已入选）且本表数据行呈明细形态 → 跨页续表入选。
    负面特征一律否决。
    """
    if _has_negative(tb):
        return False
    if score >= TABLE_SCORE_THRESHOLD:
        return True
    if not IDENTITY_ONLY_ENABLED:
        return False
    data = tb.get("data") or []
    ident = set()
    for row in data[:HEADER_SCAN_ROWS]:
        ident |= _hits(row, STRONG_IDENTITY)
    if len(ident) >= IDENTITY_ONLY_MIN_HITS:
        return True
    # 跨页续表：无表头 + 明细行形态 + 上一张表已入选
    if prev_selected and _form_hit(tb):
        return True
    return False


def _form_hit(tb):
    """数据行形态：同时出现纯数字数量列与单位单元格（≥3列有效）。"""
    for row in (tb.get("data") or [])[:FORM_SCAN_ROWS]:
        cells = [str(c).strip() for c in row if c is not None
                 and str(c).strip() != ""]
        has_unit = any(c in QTY_UNIT_CELLS for c in cells)
        has_num = any(_NUM_RE.match(c) for c in cells)
        if has_unit and has_num and len(cells) >= 3:
            return True
    return False


def _has_negative(tb):
    data = tb.get("data") or []
    blob_all = _norm_cell("\n".join(
        " ".join(str(c) for c in row if c) for row in data)) if data else ""
    return any(k in blob_all
               for kws in KW_NEGATIVE.values() for k in kws)
