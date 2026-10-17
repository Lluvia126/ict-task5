"""V2 附件预处理实验：附件级候选筛选 + 表格级报价明细定位（确定性，无模型调用）。

两级筛选：
  L1 附件级：只保留 price_detail_candidates.csv 命中的候选附件（复用现有记录，
             不重新发明检测逻辑）；损坏/伪造附件天然不在其中（解析失败无表格）。
  L2 表格级：对候选附件的每张表（从 parsed JSON 二维结构读取）打分，
             只保留 score >= TABLE_SCORE_THRESHOLD 的报价明细表。

评分规则（可解释，阈值可配置）：
  A 产品/服务身份类命中: +2
  D 价格类命中:          +2
  C 数量类命中:          +1
  B 品牌/型号类命中:      +1
  同时 A+C+D 组合:        +2
  明显目录/审查/评审/资格/售后/技术要求/参数/实施方案: -3/类
  仅有名称+参数而无价格数量: 天然低分（A=2, B=1 → 3 < 阈值4）

词表基于 1241 张真实表格的表头词频统计（含 PDF 双字符渲染兼容）。
"""
import json
import re
from pathlib import Path

# ============================ 可配置参数 ============================

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

# ================================================================


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


def score_table(tb):
    """对单张表格打分，返回 (score, matched_keywords, negative_hits, detail)。"""
    data = tb.get("data") or []
    header_rows = data[:HEADER_SCAN_ROWS]
    ident, brand, qty, price, neg = set(), set(), set(), set(), set()

    # 表头区扫描
    for row in header_rows:
        ident |= _hits(row, KW_IDENTITY)
        brand |= _hits(row, KW_BRAND_SPEC)
        qty |= _hits(row, KW_QTY)
        price |= _hits(row, KW_PRICE)

    # 负面特征：整表扫描（目录/评审表往往正文很长）
    blob_all = _norm_cell("\n".join(
        " ".join(str(c) for c in row if c) for row in data)) if data else ""
    for cat, kws in KW_NEGATIVE.items():
        if any(k in blob_all for k in kws):
            neg.add(cat)

    # 全表兜底：表头前3行未命中但数据行有价格+数量（合并单元格表头常见）
    if not price and not qty:
        for row in data[HEADER_SCAN_ROWS:HEADER_SCAN_ROWS + 5]:
            price |= _hits(row, KW_PRICE)
            qty |= _hits(row, KW_QTY)

    score = 0
    score += 2 * len(ident)
    score += 2 * len(price)
    score += 1 * len(qty)
    score += 1 * len(brand)
    if ident and qty and price:
        score += COMBO_BONUS
    score -= NEGATIVE_PENALTY * len(neg)

    matched = sorted(ident | brand | qty | price)
    detail = {
        "identity": sorted(ident), "brand_spec": sorted(brand),
        "quantity": sorted(qty), "price": sorted(price),
        "negative": sorted(neg),
    }
    return score, matched, sorted(neg), detail


def render_table(tb):
    """表格 → 管道分隔文本，保持二维行列（与 V1 一致）。"""
    lines = []
    for row in tb.get("data") or []:
        cells = ["" if c is None else str(c).replace("\n", " ") for c in row]
        lines.append(" | ".join(cells))
    return "\n".join(lines)
