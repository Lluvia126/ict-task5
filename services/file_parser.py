"""统一文件解析模块。

支持格式：
- HTML / HTM
- PDF（含表格）
- DOCX（python-docx + XML 兜底）
- XLSX（openpyxl）
- CSV / TSV
- ZIP（递归 + 深度限制 + 安全解压）
- 老式 .doc / .xls（OLE 格式，需要 LibreOffice）

对外主入口：
    parse_file(path, filename) -> {"text": str, "tables": list, ...}
    save_upload(filename, content) -> {"file_id": str, ...}
"""

from __future__ import annotations

import csv
import io
import shutil
import subprocess
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from docx import Document
import openpyxl
import pdfplumber


# ========== 路径配置 ==========

UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

TEMP_DIR = Path("data/.tmp")
MAX_ARCHIVE_DEPTH = 3


# ========== 通用工具 ==========

@contextmanager
def temporary_directory(prefix: str):
    """在 data/.tmp 下创建临时目录，退出时自动清理。"""
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TEMP_DIR / f"{prefix}{uuid.uuid4().hex}"
    path.mkdir(parents=False, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        try:
            TEMP_DIR.rmdir()
        except OSError:
            pass


def decode_bytes(data: bytes) -> str:
    """按常见中文/网页编码解码，避免 GBK 文件报错。"""
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def read_text_file(path: str | Path) -> str:
    return decode_bytes(Path(path).read_bytes())


# ========== 上传保存 ==========

def save_upload(filename: str, content: bytes) -> dict[str, Any]:
    """保存上传文件到 data/uploads。"""
    file_id = uuid.uuid4().hex
    suffix = Path(filename).suffix.lower()
    saved_path = UPLOAD_DIR / f"{file_id}{suffix}"
    saved_path.write_bytes(content)
    return {
        "file_id": file_id,
        "filename": filename,
        "saved_path": str(saved_path),
        "size": len(content),
    }


# ========== 格式识别 ==========

def detect_real_format(path: str | Path, filename: str | None = None) -> str:
    """优先按文件内容识别格式，扩展名只作为兜底。"""
    path = Path(path)
    name = filename or path.name
    suffix = Path(name).suffix.lower()

    try:
        with path.open("rb") as file:
            head = file.read(8192)
    except OSError:
        return "unknown"

    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole"
    if head.startswith(b"{\\rtf"):
        return "rtf"

    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
            if "word/document.xml" in names:
                return "docx"
            if "xl/workbook.xml" in names:
                return "xlsx"
            if "ppt/presentation.xml" in names:
                return "pptx"
        except (OSError, zipfile.BadZipFile):
            pass
        return "zip"

    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if (
        stripped.startswith(b"<!doctype html")
        or stripped.startswith(b"<html")
        or stripped.startswith(b"<head")
        or stripped.startswith(b"<body")
    ):
        return "html"

    if suffix in (".html", ".htm"):
        return "html"
    if suffix in (".docx", ".xlsx"):
        return suffix.lstrip(".")
    if suffix == ".pdf":
        return "pdf"
    if suffix in (".txt", ".md", ".json", ".xml", ".csv", ".tsv"):
        return suffix.lstrip(".")
    return suffix.lstrip(".") or "unknown"


# ========== HTML ==========

def parse_html(path: str | Path) -> dict[str, Any]:
    html = read_text_file(path)
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    tables: list[list[list[str]]] = []
    for table in soup.find_all("table"):
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [cell.get_text(strip=True) for cell in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return {"text": text, "tables": tables}


# ========== PDF ==========

def parse_pdf(path: str | Path) -> dict[str, Any]:
    text_parts: list[str] = []
    tables: list[Any] = []
    warnings: list[str] = []

    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            try:
                text_parts.append(page.extract_text() or "")
            except Exception as exc:
                warnings.append(f"第 {page_number} 页文本提取失败: {exc}")
            try:
                for table in page.extract_tables():
                    if table:
                        tables.append(table)
            except Exception as exc:
                warnings.append(f"第 {page_number} 页表格提取失败: {exc}")

    result: dict[str, Any] = {"text": "\n".join(text_parts), "tables": tables}
    if not result["text"].strip() and not tables:
        warnings.append("未提取到文本或表格，文件可能是扫描件，需要 OCR")
    if warnings:
        result["warnings"] = warnings
    return result


# ========== DOCX ==========

def _extract_xml_text(element: Any, namespace: str) -> str:
    """读取 Word XML 段落，保留制表符和换行。"""
    parts: list[str] = []
    for node in element.iter():
        if node.tag == f"{{{namespace}}}t" and node.text:
            parts.append(node.text)
        elif node.tag == f"{{{namespace}}}tab":
            parts.append("\t")
        elif node.tag in (f"{{{namespace}}}br", f"{{{namespace}}}cr"):
            parts.append("\n")
    return "".join(parts).strip()


def _parse_docx_raw(path: str | Path) -> dict[str, Any]:
    """直接解 docx，读 word/document.xml。"""
    from lxml import etree

    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    try:
        with zipfile.ZipFile(path) as archive:
            xml_content = archive.read("word/document.xml")
        root = etree.fromstring(xml_content)
    except Exception as exc:
        return {"text": f"[DOCX解析失败: {exc}]", "tables": [], "error": str(exc)}

    body = root.find(f".//{{{namespace}}}body")
    if body is None:
        return {"text": "", "tables": []}

    text_parts: list[str] = []
    tables: list[list[list[str]]] = []
    for child in body:
        if child.tag == f"{{{namespace}}}p":
            line = _extract_xml_text(child, namespace)
            if line:
                text_parts.append(line)
        elif child.tag == f"{{{namespace}}}tbl":
            rows: list[list[str]] = []
            for row in child.findall(f".//{{{namespace}}}tr"):
                cells = [
                    _extract_xml_text(cell, namespace)
                    for cell in row.findall(f"./{{{namespace}}}tc")
                ]
                if any(cells):
                    rows.append(cells)
            if rows:
                tables.append(rows)

    return {"text": "\n".join(text_parts), "tables": tables}


def parse_docx(path: str | Path) -> dict[str, Any]:
    """优先用 python-docx，失败时回退到 XML 直读。"""
    try:
        document = Document(path)
        text_parts = [
            paragraph.text.strip()
            for paragraph in document.paragraphs
            if paragraph.text.strip()
        ]
        tables: list[list[list[str]]] = []
        for table in document.tables:
            rows = [
                [cell.text.strip() for cell in row.cells]
                for row in table.rows
            ]
            if any(any(cell for cell in row) for row in rows):
                tables.append(rows)
        if text_parts or tables:
            return {"text": "\n".join(text_parts), "tables": tables}
    except Exception:
        pass
    return _parse_docx_raw(path)


# ========== XLSX ==========

def parse_xlsx(path: str | Path) -> dict[str, Any]:
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        text_parts: list[str] = []
        tables: list[list[list[str]]] = []
        for worksheet in workbook.worksheets:
            rows: list[list[str]] = []
            for row in worksheet.iter_rows(values_only=True):
                values = ["" if value is None else str(value) for value in row]
                if any(values):
                    rows.append(values)
            if rows:
                tables.append(rows)
                text_parts.append(
                    f"[Sheet: {worksheet.title}]\n"
                    + "\n".join("\t".join(row) for row in rows)
                )
        return {"text": "\n\n".join(text_parts), "tables": tables}
    finally:
        workbook.close()


# ========== CSV / TSV ==========

def parse_csv(path: str | Path, delimiter: str | None = None) -> dict[str, Any]:
    text = read_text_file(path)
    if delimiter is None:
        delimiter = "\t" if Path(path).suffix.lower() == ".tsv" else ","
    rows = [
        [cell.strip() for cell in row]
        for row in csv.reader(io.StringIO(text), delimiter=delimiter)
        if any(cell.strip() for cell in row)
    ]
    rendered = "\n".join("\t".join(row) for row in rows)
    return {"text": rendered, "tables": [rows] if rows else []}


# ========== ZIP ==========

def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    """防止 ZIP 内路径穿越。"""
    destination = destination.resolve()
    for member in archive.infolist():
        member_path = (destination / member.filename).resolve()
        if member_path != destination and destination not in member_path.parents:
            raise ValueError(f"ZIP 包含不安全路径: {member.filename}")
        archive.extract(member, destination)


def parse_zip(path: str | Path, depth: int = 0) -> dict[str, Any]:
    if depth >= MAX_ARCHIVE_DEPTH:
        return {"text": "[ZIP嵌套层级过深，已停止解析]", "tables": []}

    all_text: list[str] = []
    all_tables: list[Any] = []
    warnings: list[str] = []

    with temporary_directory("procurement_zip_") as temporary_dir:
        extract_dir = Path(temporary_dir)
        try:
            with zipfile.ZipFile(path) as archive:
                _safe_extract(archive, extract_dir)
        except Exception as exc:
            return {"text": f"[ZIP解压失败: {exc}]", "tables": [], "error": str(exc)}

        files = sorted(
            file
            for file in extract_dir.rglob("*")
            if file.is_file()
            and not file.name.startswith(".")
            and "__MACOSX" not in file.parts
        )
        for file in files:
            relative_name = file.relative_to(extract_dir).as_posix()
            try:
                result = parse_file(file, relative_name, depth=depth + 1)
                all_text.append(
                    f"\n===== {relative_name} =====\n{result.get('text', '')}"
                )
                all_tables.extend(result.get("tables", []))
                warnings.extend(
                    f"{relative_name}: {warning}"
                    for warning in result.get("warnings", [])
                )
                if result.get("error"):
                    warnings.append(f"{relative_name}: {result['error']}")
            except Exception as exc:
                message = f"{relative_name} 解析失败: {exc}"
                all_text.append(f"\n===== {message} =====")
                warnings.append(message)

    result: dict[str, Any] = {"text": "\n".join(all_text), "tables": all_tables}
    if warnings:
        result["warnings"] = warnings
    return result


# ========== 老式 OLE（.doc / .xls） ==========

def _find_soffice() -> str | None:
    executable = shutil.which("soffice")
    if executable:
        return executable
    candidates = (
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
    )
    return str(next((c for c in candidates if c.exists()), "")) or None


def parse_ole(path: str | Path, suffix: str) -> dict[str, Any]:
    """用 LibreOffice 转换老式 .doc / .xls。"""
    soffice = _find_soffice()
    if not soffice:
        return {
            "text": f"[{suffix or 'OLE'} 是 OLE 老格式，需要安装 LibreOffice 才能解析]",
            "tables": [],
            "error": "未找到 LibreOffice(soffice)",
        }

    target_format = "xlsx" if suffix == ".xls" else "docx"
    with temporary_directory("procurement_ole_") as temporary_dir:
        output_dir = Path(temporary_dir)
        try:
            completed = subprocess.run(
                [
                    soffice,
                    "--headless",
                    "--convert-to",
                    target_format,
                    "--outdir",
                    str(output_dir),
                    str(Path(path).resolve()),
                ],
                check=True,
                timeout=120,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            return {
                "text": f"[LibreOffice 转换失败: {detail}]",
                "tables": [],
                "error": detail,
            }

        converted = output_dir / f"{Path(path).stem}.{target_format}"
        if not converted.exists():
            return {
                "text": f"[转换后文件未生成: {completed.stdout.strip()}]",
                "tables": [],
                "error": "转换后文件未生成",
            }
        return parse_docx(converted) if target_format == "docx" else parse_xlsx(converted)


# ========== 统一入口 ==========

def parse_file(
    path: str | Path,
    filename: str | None = None,
    *,
    depth: int = 0,
) -> dict[str, Any]:
    """按真实内容格式分发解析器，失败时也返回统一结构。"""
    path = Path(path)
    filename = filename or path.name
    suffix = Path(filename).suffix.lower()
    real_format = detect_real_format(path, filename)

    handlers = {
        "pdf": parse_pdf,
        "html": parse_html,
        "docx": parse_docx,
        "xlsx": parse_xlsx,
    }

    try:
        if real_format in handlers:
            result = handlers[real_format](path)
        elif real_format == "csv":
            result = parse_csv(path)
        elif real_format == "tsv":
            result = parse_csv(path, delimiter="\t")
        elif real_format == "zip":
            result = parse_zip(path, depth=depth)
        elif real_format == "ole":
            result = parse_ole(path, suffix)
        elif real_format == "pptx":
            result = {
                "text": "[PPTX暂未实现文本解析]",
                "tables": [],
                "error": "unsupported: pptx",
            }
        elif real_format == "rtf":
            result = {
                "text": read_text_file(path),
                "tables": [],
                "warnings": ["RTF 当前仅按纯文本读取"],
            }
        else:
            result = {"text": read_text_file(path), "tables": []}
    except Exception as exc:
        return {
            "text": f"[{real_format.upper()}解析失败: {exc}]",
            "tables": [],
            "error": str(exc),
            "format": real_format,
        }

    result.setdefault("text", "")
    result.setdefault("tables", [])
    result["format"] = real_format
    return result