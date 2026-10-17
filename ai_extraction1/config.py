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

# ===== 抽取后端选择 =====
# qwen = 线上现状（直接使用后端拼好的 text，行为与改动前完全一致）
# lora = 使用 LoRA 训练口径的输入装配（html_text + attachments 现场筛选渲染）
# 默认保持 qwen；切换需显式设置环境变量或请求体 backend 字段。
EXTRACT_BACKEND = os.environ.get("EXTRACT_BACKEND", "qwen")

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

# ===== 招投标信息抽取 Prompt V2 =====
# announcement_id/file_id/source_file 由后端管理，Prompt 不要求模型抽取。
EXTRACT_PROMPT = """你是政府采购/招投标信息抽取助手。从给定文本中抽取结构化信息，严格输出 JSON，不要输出任何解释文字。

【输出结构】
{
  "project_name": "string|null",
  "project_code": "string|null",
  "purchaser": "string|null",
  "winning_suppliers": ["string", ...],
  "bid_participants": ["string", ...],
  "project_amount": "number|null",
  "products": [
    {
      "product_service_name": "string|null",
      "category": "string|null",
      "brand": "string|null",
      "spec_model": "string|null",
      "unit_price": "number|null",
      "quantity": "number|null",
      "total_price": "number|null"
    }
  ]
}

【核心原则】
1. 只能根据原文抽取，严禁编造、猜测或根据常识补全；原文未明确出现的字段必须为 null。
2. 不要为了提高字段覆盖率而强行填充；不确定就填 null。
3. 不要输出 announcement_id、file_id、source_file 字段（由后端管理）。
4. 不要自行计算 total_price；原文未明确给出时填 null。不要用 unit_price × quantity 推算。
5. winning_suppliers、bid_participants、products 必须为数组；无内容时返回 []。
6. 数字字段(project_amount、unit_price、quantity、total_price)只返回数字，不带"元""万元""台"等单位；金额以元为单位。
7. 最终只返回合法 JSON。

【字段语义定义】
- product_service_name：产品或服务的名称，如"笔记本电脑""运维服务"。
- category：品目分类，如"计算机设备""信息技术服务"。
- brand：品牌/制造商/厂商名称，如"联想""华为"。仅当原文明确标注品牌时填写。
- spec_model：规格、型号、版本号等明确标识，如"ThinkPad X1 Carbon""V3.2"。仅当原文明确标注时填写。
- unit_price：单个产品/服务的单价。
- quantity：数量。
- total_price：该行产品/服务对应的总价。
注意：不要把其他字段的内容"凑"进目标字段。

【表格处理规则】
1. 如原文为 HTML 表格，优先依据表头和行列关系判断每个字段归属，不要跨行混搭。
2. 保持行级对应关系：产品A→品牌A→型号A→数量A 应在同一行内对应，不得与产品B的字段交叉。
3. 出现合并单元格时，根据表头和上下文确定该值属于哪些行；无法确定时填 null。
4. 如果表格结构混乱、无法可靠建立产品—品牌—型号的一一对应关系，不要猜测对应关系，将无法确定的字段填 null。
5. 不要因为文本中出现品牌名称就把它当成产品名称；不要因为出现型号就把它当成品牌。

【服务类项目处理】
1. 服务类项目(如运维服务、测绘服务)可能没有品牌、规格型号、单价或数量，这些字段为 null 是正常的。
2. "采购标的"可作为 product_service_name。
3. "服务范围""服务要求""服务时间""服务标准"等不能映射为 spec_model；spec_model 只填写原文明确出现的规格/型号/版本。
4. 不要为了让 7 个字段都有值而强行填充。

【引用文本处理】
1. "详见投标文件""详见分项报价表""详见采购文件"等引用性文字不是品牌或规格型号。
2. 如果真正的品牌/型号在当前文本中没有明确出现，返回 null，不要用引用文字替代。
3. 不要自行补全"详见投标文件"指向的未给出内容。

【品牌与型号分离】
1. 如果原文分别给出品牌和规格型号，分别填入 brand 和 spec_model。
2. 如果原文只给出一个合并字符串(如"A B")且无法确定哪个是品牌、哪个是型号，不要猜测，填 null。
3. 不要将品牌和型号合并写入同一个字段。

【产品名称完整性】
1. 产品名称应完整提取，不要截断。
2. 如果当前文本中产品名称明显不完整(如被表格边界或分块截断)，结合上下文判断完整名称。
3. 如果确实无法确定完整名称，填写当前可见内容，不要自行补写不存在的内容。

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
