from pydantic import BaseModel
from typing import List, Optional


class ProductItem(BaseModel):
    product_service_name: str = ""
    category: str = ""
    brand: str = ""
    spec_model: str = ""
    unit_price: Optional[float] = None
    quantity: Optional[float] = None
    total_price: Optional[float] = None


class ExtractionResult(BaseModel):
    announcement_id: str = ""
    file_id: str = ""
    source_file: str = ""
    project_name: str = ""
    project_code: str = ""
    purchaser: str = ""
    winning_suppliers: List[str] = []
    bid_participants: List[str] = []
    project_amount: Optional[float] = None
    products: List[ProductItem] = []


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