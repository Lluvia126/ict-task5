import uuid
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup
import pdfplumber
from docx import Document
import openpyxl

UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def save_upload(filename: str, content: bytes) -> dict:
    file_id = uuid.uuid4().hex
    suffix = Path(filename).suffix.lower()
    saved_name = f"{file_id}{suffix}"
    saved_path = UPLOAD_DIR / saved_name
    saved_path.write_bytes(content)
    return {
        "file_id": file_id,
        "filename": filename,
        "saved_path": str(saved_path),
        "size": len(content),
    }


def parse_html(path: str) -> dict:
    html = Path(path).read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)

    tables = []
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)

    return {"text": text, "tables": tables}


def parse_pdf(path: str) -> dict:
    text_parts = []
    tables = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
            for tbl in page.extract_tables():
                if tbl:
                    tables.append(tbl)
    return {"text": "\n".join(text_parts), "tables": tables}


def parse_docx(path: str) -> dict:
    doc = Document(path)
    text_parts = [p.text for p in doc.paragraphs if p.text.strip()]
    tables = []
    for tbl in doc.tables:
        rows = []
        for row in tbl.rows:
            rows.append([cell.text.strip() for cell in row.cells])
        if rows:
            tables.append(rows)
    return {"text": "\n".join(text_parts), "tables": tables}


def parse_xlsx(path: str) -> dict:
    wb = openpyxl.load_workbook(path, data_only=True)
    text_parts = []
    tables = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            values = ["" if v is None else str(v) for v in row]
            if any(values):
                rows.append(values)
        if rows:
            tables.append(rows)
            text_parts.append(
                f"[Sheet: {ws.title}]\n" + "\n".join("\t".join(r) for r in rows)
            )
    return {"text": "\n\n".join(text_parts), "tables": tables}


def parse_zip(path: str) -> dict:
    extract_dir = Path(path).with_suffix("")
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "r") as zf:
        zf.extractall(extract_dir)

    all_text = []
    all_tables = []
    for f in extract_dir.rglob("*"):
        if f.is_file():
            try:
                result = parse_file(str(f), f.name)
                all_text.append(f"\n===== {f.name} =====\n{result['text']}")
                all_tables.extend(result.get("tables", []))
            except Exception as e:
                all_text.append(f"\n===== {f.name} 解析失败: {e} =====")
    return {"text": "\n".join(all_text), "tables": all_tables}


def parse_file(path: str, filename: str) -> dict:
    suffix = Path(filename).suffix.lower()
    if suffix in (".html", ".htm"):
        return parse_html(path)
    if suffix == ".pdf":
        return parse_pdf(path)
    if suffix == ".docx":
        return parse_docx(path)
    if suffix in (".xlsx", ".xls"):
        return parse_xlsx(path)
    if suffix == ".zip":
        return parse_zip(path)
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        text = ""
    return {"text": text, "tables": []}