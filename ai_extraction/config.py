import os

# 可选：从 .env 文件加载（pip install python-dotenv）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ===== API 配置（从环境变量读取，不要硬编码真实 Key）=====
QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "")
QWEN_BASE_URL = os.environ.get(
    "QWEN_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen-vl-max")

# ===== 抽取字段定义 =====
# 项目级标量字段
PROJECT_FIELDS = ["project_name", "project_code", "purchaser", "project_amount"]
# 项目级列表字段
PROJECT_LIST_FIELDS = ["winning_suppliers", "bid_participants"]
# 产品级字段
PRODUCT_FIELDS = [
    "product_service_name", "category", "brand", "spec_model",
    "unit_price", "quantity", "total_price",
]

# ===== 分块参数（单次输入最大字符数，给 prompt 留余量）=====
MAX_CHUNK_CHARS = 4000

# ===== 数据目录 =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
PROCESSED_DIR = os.path.join(DATA_DIR, "processed")
VALIDATION_DIR = os.path.join(DATA_DIR, "validation")

# ===== 招投标信息抽取 Prompt =====
# announcement_id/file_id/source_file 由后端管理，Prompt 不要求模型抽取。
EXTRACT_PROMPT = """你是政府采购/招投标信息抽取助手。只根据原文抽取，不得编造；无法确定的字段填 null。
严格输出 JSON，不要输出任何解释文字。

输出结构：
{
  "project_name": "项目名称(string|null)",
  "project_code": "项目编号(string|null)",
  "purchaser": "采购单位(string|null)",
  "winning_suppliers": ["中标供应商(list[string]，无则 [])"],
  "bid_participants": ["投标参与方(list[string]，无则 [])"],
  "project_amount": "项目金额(number|null，单位元)",
  "products": [
    {
      "product_service_name": "产品服务名称(string|null)",
      "category": "品目(string|null)",
      "brand": "品牌(string|null)",
      "spec_model": "规格型号(string|null)",
      "unit_price": "单价(number|null)",
      "quantity": "数量(number|null)",
      "total_price": "总价(number|null)"
    }
  ]
}

规则：
1. 只能根据原文抽取，不允许编造。
2. 无法确定的字段使用 null。
3. winning_suppliers、bid_participants、products 必须为数组；无内容时返回 []。
4. 数字字段(project_amount、unit_price、quantity、total_price)只返回数字，不带"元""万元""台"等单位；金额以元为单位。
5. 产品字段必须保持同一产品的字段对应关系，按原文产品逐个提取。
6. 原文如为表格，按表格行对应提取每个产品。
7. 不要自行计算产品总价；原文未明确给出 total_price 时填 null。
8. 不要输出 announcement_id、file_id、source_file 字段。
9. 最终只返回合法 JSON。

文本：
{text}
"""


def empty_result(announcement_id, file_id="", source_file=""):
    """返回带 null/空数组的完整结果骨架（announcement_id 等由后端原样回填）。"""
    return {
        "announcement_id": announcement_id,
        "file_id": file_id,
        "source_file": source_file,
        "project_name": None,
        "project_code": None,
        "purchaser": None,
        "winning_suppliers": [],
        "bid_participants": [],
        "project_amount": None,
        "products": [],
    }


def ensure_dirs():
    for d in [DATA_DIR, RAW_DIR, PROCESSED_DIR, VALIDATION_DIR]:
        os.makedirs(d, exist_ok=True)
