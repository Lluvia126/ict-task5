r"""批量文件解析测试工具。

用法（建议从项目根目录执行）：
    .\.venv\Scripts\python.exe scripts\test_parsers.py
    .\.venv\Scripts\python.exe scripts\test_parsers.py test_data\official_samples
    .\.venv\Scripts\python.exe scripts\test_parsers.py some.pdf another.docx --no-save

脚本默认递归扫描 test_data，解析后把每个文件的 text/tables 写入
data/results/parser_test，同时输出一份 summary。测试脚本不依赖启动时的
当前工作目录，默认输入目录始终相对于项目根目录。
"""

from __future__ import annotations

import argparse
import csv
from contextlib import contextmanager
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Iterable

from bs4 import BeautifulSoup
from docx import Document
import openpyxl
import pdfplumber


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "test_data"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "results" / "parser_test"
UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR = PROJECT_ROOT / "data" / ".tmp"

MAX_ARCHIVE_DEPTH = 3


# ========== 通用工具 ==========


@contextmanager
def temporary_directory(prefix: str):
    """在项目 data/.tmp 下创建可访问的临时目录。"""
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
    """按常见中文/网页编码解码，避免部分旧文件因 GBK 报错。"""
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def read_text_file(path: str | Path) -> str:
    return decode_bytes(Path(path).read_bytes())


def save_upload(filename: str, content: bytes) -> dict[str, Any]:
    """保留原有上传辅助函数，路径固定为项目的 data/uploads。"""
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
    """优先根据文件内容识别格式，扩展名只作为兜底。"""
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


# ========== 各格式解析 ==========


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


def parse_pdf(path: str | Path) -> dict[str, Any]:
    text_parts: list[str] = []
    tables: list[list[list[str | None]]] = []
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
        warnings.append("未提取到文本或表格，文件可能是扫描件，需要额外 OCR")
    if warnings:
        result["warnings"] = warnings
    return result


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
    """优先使用 python-docx，异常或空结果时直接解析 document.xml。"""
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


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    """防止 ZIP 内路径穿越，不再把 extracted 目录永久留在测试数据旁。"""
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


def _find_soffice() -> str | None:
    executable = shutil.which("soffice")
    if executable:
        return executable
    candidates = (
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
    )
    return str(next((candidate for candidate in candidates if candidate.exists()), "")) or None


def parse_ole(path: str | Path, suffix: str) -> dict[str, Any]:
    """使用 LibreOffice 转换老式 .doc / .xls，并在临时目录中处理。"""
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
    """按真实内容格式分发解析器，并保证失败时也返回统一结构。"""
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
                "warnings": ["RTF 当前仅按纯文本读取，复杂格式未转换"],
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


# ========== 批量测试与报告 ==========


def iter_input_files(inputs: Iterable[Path], recursive: bool) -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()

    for input_path in inputs:
        input_path = input_path.expanduser().resolve()
        candidates = (
            input_path.rglob("*")
            if input_path.is_dir() and recursive
            else input_path.iterdir()
            if input_path.is_dir()
            else [input_path]
        )
        for candidate in candidates:
            if not candidate.is_file():
                continue
            if candidate.name.startswith(".") or candidate.name.startswith("~$"):
                continue
            key = str(candidate).casefold()
            if key not in seen:
                seen.add(key)
                files.append(candidate)

    return sorted(files, key=lambda item: str(item).casefold())


def _result_filename(index: int, relative_path: Path) -> str:
    raw_name = relative_path.as_posix()
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_name)
    digest = hashlib.sha1(raw_name.encode("utf-8")).hexdigest()[:8]
    return f"{index:03d}_{safe_name[:160]}_{digest}.json"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="批量解析测试：扫描文件并输出 text/tables 统计与 JSON 结果。"
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help=f"要测试的文件或目录；不传时使用 {DEFAULT_INPUT_DIR}",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"JSON 输出目录；默认 {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="只输出终端报告，不写 JSON 文件",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="目录只扫描第一层，不递归子目录",
    )
    parser.add_argument(
        "--show-text",
        type=int,
        default=120,
        metavar="N",
        help="每个文件在终端展示的文本预览字符数，默认 120；0 表示不展示",
    )
    parser.add_argument("--max-files", type=int, help="最多测试多少个文件")
    parser.add_argument("--fail-fast", action="store_true", help="遇到解析错误立刻退出")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，显式切到 UTF-8，防止中文/生僻字打印崩溃。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = build_argument_parser().parse_args(argv)
    inputs = args.paths or [DEFAULT_INPUT_DIR]
    missing = [path for path in inputs if not path.exists()]
    if missing:
        for path in missing:
            print(f"[错误] 路径不存在: {path}", file=sys.stderr)
        return 2

    files = iter_input_files(inputs, recursive=not args.no_recursive)
    if args.max_files is not None:
        files = files[: max(0, args.max_files)]
    if not files:
        print("没有找到可测试的文件。")
        return 1

    output_dir = args.output_dir.expanduser().resolve()
    if not args.no_save:
        output_dir.mkdir(parents=True, exist_ok=True)

    root = PROJECT_ROOT
    summary: list[dict[str, Any]] = []
    failed_count = 0

    print(f"发现 {len(files)} 个文件，开始批量解析...")
    for index, source_path in enumerate(files, start=1):
        started_at = time.perf_counter()
        result = parse_file(source_path, source_path.name)
        elapsed = time.perf_counter() - started_at
        error = result.get("error")
        status = "失败" if error else "成功"
        if error:
            failed_count += 1

        try:
            relative_path = source_path.relative_to(root)
        except ValueError:
            relative_path = Path(source_path.name)

        item: dict[str, Any] = {
            "index": index,
            "source_path": str(source_path),
            "filename": source_path.name,
            "format": result.get("format", detect_real_format(source_path)),
            "status": "error" if error else "ok",
            "elapsed_seconds": round(elapsed, 3),
            "text_length": len(result.get("text", "")),
            "table_count": len(result.get("tables", [])),
            "warnings": result.get("warnings", []),
            "error": error,
        }

        if not args.no_save:
            output_path = output_dir / _result_filename(index, relative_path)
            payload = dict(item)
            payload["result"] = result
            output_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            item["output_file"] = str(output_path)

        summary.append(item)

        preview = result.get("text", "").replace("\r", " ").replace("\n", " ")
        if args.show_text > 0:
            preview = preview[: args.show_text]
        else:
            preview = ""
        print(
            f"[{index:03d}/{len(files):03d}] {status} "
            f"format={item['format']} text={item['text_length']} "
            f"tables={item['table_count']} time={item['elapsed_seconds']:.3f}s "
            f"file={source_path}"
        )
        if preview:
            print(f"      preview: {preview}")
        if error:
            print(f"      error: {error}")
        if args.fail_fast and error:
            break

    if not args.no_save:
        summary_path = output_dir / "summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n结果目录: {output_dir}")
        print(f"汇总文件: {summary_path}")

    print(
        f"\n完成：共 {len(summary)} 个，成功 {len(summary) - failed_count} 个，"
        f"失败 {failed_count} 个。"
    )
    return 1 if failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
