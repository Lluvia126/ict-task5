from pydantic import BaseModel
from typing import List, Optional


class UploadResponse(BaseModel):
    file_id: str
    filename: str
    saved_path: str
    size: int
    status: str = "uploaded"


class ParsedDocument(BaseModel):
    file_id: str
    filename: str
    file_type: str
    text: str
    tables: List[list] = []
    metadata: dict = {}


class ProductItem(BaseModel):
    product_service_name: Optional[str] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    spec_model: Optional[str] = None
    unit_price: Optional[float] = None
    quantity: Optional[float] = None
    total_price: Optional[float] = None


class ExtractionResult(BaseModel):
    announcement_id: str = ""
    file_id: str = ""
    source_file: str = ""
    project_name: Optional[str] = None
    project_code: Optional[str] = None
    purchaser: Optional[str] = None
    winning_suppliers: Optional[List[str]] = None
    bid_participants: Optional[List[str]] = None
    project_amount: Optional[float] = None
    products: Optional[List[ProductItem]] = None
    # 样例表所需的派生字段（规则生成，非 AI 抽取）
    notice_id: Optional[str] = None
    record_type: Optional[str] = None
    bid_packages: Optional[List[str]] = None
    uuid_map: Optional[dict] = None
    # 图谱同步标记（Neo4j 不可用时为 False 并带错误信息）
    graph_synced: Optional[bool] = None
    graph_sync_error: Optional[str] = None


class PairUploadResponse(BaseModel):
    pair_id: str
    html_file_id: str
    html_filename: str
    zip_file_id: str = ""
    zip_filename: str = ""
    status: str = "uploaded"


class ParsedPair(BaseModel):
    pair_id: str
    html_raw: str = ""
    html_text: str = ""
    html_tables: List[list] = []
    attachment_text: str = ""
    merged_text: str = ""
    tables: List[list] = []
    attachments: List[dict] = []
    html_file_id: str = ""
    zip_file_id: str = ""
    metadata: dict = {}