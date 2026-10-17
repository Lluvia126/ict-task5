"""table_filter_lora.py — v2.2 表格筛选逻辑（从训练脚本原样复制，逻辑不改）。

来源（只读复制，未修改原件）：
  - ai_extraction/preprocessing/v2_2_table_filter.py
      score_table_v22 / selected_v22 / _is_continuation / _has_negative
      KW_IDENTITY_V22 / IDENTITY_ONLY_ENABLED / IDENTITY_ONLY_MIN_HITS / STRONG_IDENTITY
      FORM_BONUS / FORM_SCAN_ROWS / _NUM_RE
      CONTINUATION_FEATURES / CONTINUATION_MIN_FEATURES
      CONTINUATION_MAX_COL_DIFF / CONTINUATION_MAX_DEPTH
      W_IDENTITY / W_PRICE / W_QTY / W_BRAND
  - ai_extraction/preprocessing/v2_table_filter.py
      render_table / _norm_cell / _hits / score_table 依赖常量
      TABLE_SCORE_THRESHOLD / HEADER_SCAN_ROWS / NEGATIVE_PENALTY
      KW_IDENTITY / KW_BRAND_SPEC / KW_QTY / KW_PRICE / KW_NEGATIVE / COMBO_BONUS

为什么复制而不是 import：
  训练脚本属于受保护文件（不修改）。线上服务需要一份独立副本，
  以便与训练脚本解耦、可回退，且保证输入装配逐字节一致。
  本文件不 import v2_2_table_filter / v2_table_filter，避免线上行为随训练脚本改动而漂移。

注意：表格统一以 dict `tb` 传参，`tb = {"data": [[...], ...], ...}`。
"""
import re

# ============================ 常量（复制自 v2_table_filter.py） ============================

TABLE_SCORE_THRESHOLD = 4      # 表格入选最低分
HEADER_SCAN_ROWS = 3           # 表头扫描行数（前3行）
NEGATIVE_PENALTY = 3           # 每类负面特征扣分

# A 产品/服务身份类（+2）
KW_IDENTITY = [
    "产品名称", "货物名称", "服务名称", "商品名称", "标的名称", "采购标的",
    "品目名称", "设备名称", "名称", "品名",
]
# B 品牌/型号类（+1）
KW_BRAND_SPEC = [
    "品牌", "规格型号", "型号", "规格", "产地", "制造商", "生产厂商",
]
# C 数量类（+1）
KW_QTY = ["数量", "单位", "计量单位"]
# D 价格类（+2）
KW_PRICE = [
    "单价", "总价", "合价", "投标报价", "报价", "金额", "小计", "合计",
    "预算金额", "最高限价", "合同金额", "价格",
]
# 负面特征（每类 -NEGATIVE_PENALTY）
KW_NEGATIVE = {
    "目录/章节": ["目录", "章节", "第一章", "第二章"],
    "资格审查/评审": ["审查因素", "审查内容", "评审因素", "评审标准", "评审内容",
                      "资格要求", "资格审查", "评分标准", "重要程度", "分值"],
    "技术要求/参数": ["技术要求", "技术参数", "参数要求", "主要参数", "功能要求",
                      "技术规格要求", "配置要求"],
    "实施方案/服务方案": ["实施方案", "服务方案", "工作方案", "培训方案"],
    "合同条款": ["甲方", "乙方", "合同条款", "违约"],
    "格式/偏离表": ["偏离表", "偏离项", "格式要求", "偏离"],
}
# 报价明细组合（A+C+D 同时命中）
COMBO_BONUS = 2

# ============================ 常量（复制自 v2_2_table_filter.py） ============================

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


# ============================ 私有函数（复制自 v2_table_filter.py） ============================

def _norm_cell(s):
    """PDF 双字符渲染兼容：品品目目→品目、序序号号→序号。"""
    if s is None:
        return ""
    s = str(s)
    s = re.sub(r"([\u4e00-\u9fff])\1", r"\1", s)   # 相邻汉字重复去一次
    return s


def _hits(row_cells, kws):
    """一行单元格中命中的关键词集合。"""
    blob = _norm_cell(" ".join(str(c) for c in row_cells if c))
    return {k for k in kws if k in blob}


# ============================ V2.2 打分 / 入选（复制自 v2_2_table_filter.py） ============================

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


def render_table(tb):
    """表格 → 管道分隔文本，保持二维行列（与 V1 一致）。"""
    lines = []
    for row in tb.get("data") or []:
        cells = ["" if c is None else str(c).replace("\n", " ") for c in row]
        lines.append(" | ".join(cells))
    return "\n".join(lines)
