"""输出格式规则：对齐《输出标准样例表.xlsx》。

样例表三个视图（蓝色字段 = 评估最小集，缺一不可；灰色字段不计分）：
- 总表    ：UUID / 标段/合同包 / 项目名称 / 采购单位 / 中标供应商 / 中标金额
- 标的表  ：UUID / 标段/合同包 / 产品服务名称 / 品目 / 品牌 / 规格型号 / 数量 / 单价 / 总价
            （+ 类别，灰色字段，用于区分 需求 / 中标 / 入围）
- 投标人表：UUID / 标段/合同包 / 名称 / 类别 / 通过审查 / 报价（万元）

统一口径：
1. 金额统一折算为"万元"：中标金额、报价（万元）保留两位小数、四舍五入；
   单价、总价最多 4 位小数（与样例表精度一致）。
2. UUID = 公告唯一标识（源文件 HTML/附件 文件名前缀，由任务给定，不得自行编造）；
   按"项目名称 + 标段名称"映射，分标段加 _01/_02… 后缀，未分包不加后缀。
3. 数量照录原文（整数不带小数点），原文未给出则留空。
4. 框架协议识别：标题/正文含 入围 / 框架协议 / 协议折扣 的记 record_type="入围"，
   与实际中标（"中标"）区分；结果公告未命中的记"需求"。
"""

from __future__ import annotations

import os
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from bs4 import BeautifulSoup

# ---------------- 列名（与样例表逐字一致，顺序一致） ----------------

SUMMARY_HEADERS = ["UUID", "标段/合同包", "项目名称", "采购单位", "中标供应商", "中标金额"]
ITEM_HEADERS = ["UUID", "标段/合同包", "产品服务名称", "品目", "品牌", "规格型号",
                "数量", "单价", "总价", "类别"]
BIDDER_HEADERS = ["UUID", "标段/合同包", "名称", "类别", "通过审查", "报价（万元）"]

# 视图键 -> (中文表名, 列名)
VIEWS = {
    "summary": ("总表", SUMMARY_HEADERS),
    "items": ("标的表", ITEM_HEADERS),
    "bidders": ("投标人表", BIDDER_HEADERS),
}
VIEW_ORDER = ["summary", "items", "bidders"]

# 视图别名（英文键 / 中文表名都可作为查询参数）
VIEW_ALIASES = {
    "summary": "summary", "总表": "summary",
    "items": "items", "标的表": "items",
    "bidders": "bidders", "投标人表": "bidders",
}

UNASSIGNED = "未分包"          # 未分包时的标段/合同包名称
LIST_SEP = "、"                # 多值分隔符（中标供应商等）

# ---------------- 单位换算 ----------------
# AI 抽取侧金额单位为"元"（见 ai_extraction/config.py 的 EXTRACT_PROMPT 规则 4）。
# 若上游改为直接输出万元，可通过环境变量 SOURCE_AMOUNT_UNIT=万元 覆盖。
SOURCE_AMOUNT_UNIT = os.getenv("SOURCE_AMOUNT_UNIT", "元")
_WAN = Decimal("10000")


def _decimal(value):
    """尽力把任意输入转成 Decimal；无法转换返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None


def to_wan(value):
    """金额折算为万元（Decimal）；原文未给出返回 None。"""
    d = _decimal(value)
    if d is None:
        return None
    return d if SOURCE_AMOUNT_UNIT == "万元" else d / _WAN


def fmt_amount(value) -> str:
    """中标金额 / 报价（万元）：两位小数，四舍五入；原文未给出返回空串。"""
    d = to_wan(value)
    if d is None:
        return ""
    return str(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def fmt_price(value) -> str:
    """单价 / 总价：万元，最多 4 位小数，去掉末尾多余的 0。"""
    d = to_wan(value)
    if d is None:
        return ""
    d = d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    out = format(d.normalize(), "f")
    return "0" if out in ("-0", "") else out


def fmt_qty(value) -> str:
    """数量：照录原文，整数不带小数点。"""
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    d = _decimal(raw)
    if d is None:
        return raw
    if d == d.to_integral_value():
        return str(int(d))
    return format(d.normalize(), "f")


# ---------------- 公告唯一标识 / UUID ----------------

def detect_notice_id(*filenames) -> str:
    """公告唯一标识 = 源文件（HTML/附件）文件名前缀，与任务给定保持一致。

    source_file 形如 "t20260202_26141082.html + t20260202_26141082.zip" 时取第一个文件。
    """
    for name in filenames:
        raw = str(name or "").strip()
        if not raw:
            continue
        first = re.split(r"[+＋]|[;；]|、", raw)[0].strip().strip('"').strip("'")
        stem = Path(first).stem.strip()
        if stem:
            return stem
    return ""


def build_uuid_map(notice_id: str, packages) -> dict:
    """UUID 映射：按"项目名称 + 标段名称"映射，分标段加后缀，未分包不加后缀。

    公告唯一标识为基底；出现多个标段时按标段首次出现顺序加 _01/_02… 后缀。
    """
    names = []
    for p in packages or []:
        name = str(p or "").strip()
        if name and name not in names:
            names.append(name)
    if len(names) <= 1:
        return {names[0] if names else UNASSIGNED: notice_id}
    return {n: "%s_%02d" % (notice_id, i) for i, n in enumerate(names, 1)}


# ---------------- 框架协议识别 ----------------

FRAMEWORK_KEYWORDS = ("入围", "框架协议", "协议折扣")
AWARD_KEYWORDS = ("中标公告", "中标结果", "中标公示", "中标（成交）", "中标(成交)",
                  "成交公告", "成交结果", "结果公告", "采购结果")


def detect_record_type(title: str = "", body: str = "", project_name: str = "") -> str:
    """框架协议识别：标题/正文含"入围/框架协议/协议折扣"记"入围"，与实际中标区分。"""
    blob = "\n".join(x for x in (title, project_name, body) if x)
    if any(k in blob for k in FRAMEWORK_KEYWORDS):
        return "入围"
    head = "\n".join(x for x in (title, project_name) if x)
    if not any(k in head for k in AWARD_KEYWORDS):
        head = (head + "\n" + (body or "")[:300]).strip()
    if any(k in head for k in AWARD_KEYWORDS):
        return "中标"
    return "需求"


# ---------------- 标段 / 合同包识别 ----------------

_NUM_CHARS = "一二三四五六七八九十两零〇0-9"
_PKG_PATTERNS = [
    (re.compile(r"第?\s*([%s]{1,3})\s*标段(?![名称号段])" % _NUM_CHARS), "{}标段"),
    (re.compile(r"合同包\s*([%s]{1,3})(?=\s*[：:、，,。；;（(]|\s|$)" % _NUM_CHARS), "合同包{}"),
    (re.compile(r"第\s*([%s]{1,3})\s*包(?![含装号])" % _NUM_CHARS), "第{}包"),
]


def detect_bid_packages(text: str, limit: int = 20) -> list:
    """从正文提取标段/合同包名称（按首次出现顺序去重，不做编造）。"""
    found = []
    for pattern, template in _PKG_PATTERNS:
        for m in pattern.finditer(text or ""):
            name = template.format(m.group(1))
            if name not in found:
                found.append(name)
        if len(found) >= limit:
            break
    return found[:limit]


# ---------------- 分标段中标结果解析（纯规则，不调用 AI） ----------------

# 形如：采购包1: / 合同包1(主链路): / 一标段： / 第1包：
_PKG_MARK = re.compile(
    r"[（(]?\s*(采购包|合同包|标段|包)\s*(\d{1,3}|[一二三四五六七八九十]{1,3})\s*[)）]?"
    r"\s*(?:[（(]([^）)\n]{1,40})[)）])?\s*[:：]"
)
_AMOUNT_YUAN = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*元")
_AWARD_HINTS = ("中标", "成交", "结果")
_ORG_HINTS = ("公司", "中心", "研究院", "学院", "大学", "学校", "医院", "集团",
              "合作社", "商行", "银行", "饭店", "酒店", "事务所", "院", "厂", "店", "部")
_ORG_SKIP = ("供应商名称", "供应商地址", "供应商", "中标（成交）金额", "评审总得分", "品目编号",
             "品目名称", "采购标的", "服务范围", "服务要求", "服务时间", "服务标准", "详见",
             "联系人", "联系电话", "代理服务费", "收取对象", "金额", "得分", "采购结果",
             "公告期限", "项目编号", "项目名称")


def _first_org(block: str) -> str:
    """取块内第一个"像单位名称"的行（不猜，取不到返回空串）。"""
    for raw in (block or "").splitlines():
        line = raw.strip()
        line = re.sub(r"^(服务类|货物类|工程类|服务|货物|工程)[（(]\s*", "", line)
        line = line.strip("（）() \t")
        if not (4 <= len(line) <= 60):
            continue
        if any(w in line for w in _ORG_SKIP):
            continue
        if any(h in line for h in _ORG_HINTS):
            return line
    return ""


def parse_package_awards(text: str, limit: int = 20) -> list:
    """按公告原文解析"每个标段/合同包 -> 中标供应商 + 中标金额(元)"。

    只认公告里明确写出的内容：块内出现"中标/成交/结果"才采信金额，
    取不到供应商或金额的块会被 _validate_packages 过滤掉，不会污染结果。
    返回：[{"name": 标段名, "suppliers": [...], "amount": 元, "_block_text": 原文块}]
    """
    if not text:
        return []
    marks = list(_PKG_MARK.finditer(text))
    if len(marks) < 2:
        return []

    order, merged = [], {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else min(len(text), m.end() + 800)
        block = text[m.end():end]
        num = m.group(2).strip()
        item = merged.get(num)
        if item is None:
            name = "%s%s" % (m.group(1), num)
            if m.group(3):
                name += "(%s)" % m.group(3).strip()
            item = {"name": name, "suppliers": [], "amount": None, "_block_text": ""}
            merged[num] = item
            order.append(num)
            if len(order) > limit:
                break
        item["_block_text"] += block + "\n"
        if not item["suppliers"]:
            org = _first_org(block)
            if org:
                item["suppliers"] = [org]
        if item["amount"] is None and any(h in block for h in _AWARD_HINTS):
            am = _AMOUNT_YUAN.search(block)
            if am:
                try:
                    item["amount"] = float(am.group(1).replace(",", ""))
                except ValueError:
                    pass
    return [merged[n] for n in order]


def html_title(html_raw: str) -> str:
    """取 HTML <title> / 首个一级标题，用于框架协议识别。"""
    if not html_raw:
        return ""
    try:
        soup = BeautifulSoup(html_raw, "lxml")
    except Exception:
        soup = BeautifulSoup(html_raw, "html.parser")
    if soup.title and soup.title.get_text(strip=True):
        return soup.title.get_text(strip=True)
    for tag in ("h1", "h2"):
        el = soup.find(tag)
        if el and el.get_text(strip=True):
            return el.get_text(strip=True)
    return ""