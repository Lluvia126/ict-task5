"""附件正文解析器：PDF / DOC / DOCX / XLS / XLSX → 统一结构化中间结果。

保真规则：
  - 不修改原始附件，不改写原文，不推断字段含义
  - 表格保留原始行列关系（data 为二维数组，空单元格保留位置）
  - 不做任何字段映射（品牌/型号/价格列原样保留）

parse_attachment(path, announcement_id) -> dict，键：
  announcement_id, source_file, attachment_filename, extension,
  parse_status("success"/"failed"), error, reason,
  pages_or_sheets, text, tables
  tables: [{"table_index", "rows", "columns", "data", "page"/"sheet"}]
"""
from pathlib import Path


def _cell(v):
    """单元格原值 → JSON 可序列化；None 保留为 None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    return str(v)


# ---------------------------------------------------------------- PDF

def parse_pdf(path, aid):
    import pdfplumber
    texts, tables = [], []
    with pdfplumber.open(str(path)) as pdf:
        n_pages = len(pdf.pages)
        for pi, page in enumerate(pdf.pages, 1):
            t = page.extract_text() or ""
            texts.append(f"第{pi}页\n{t}")
            for tb in page.extract_tables() or []:
                data = [[_cell(c) for c in row] for row in tb]
                tables.append({
                    "table_index": len(tables),
                    "page": pi,
                    "rows": len(data),
                    "columns": max((len(r) for r in data), default=0),
                    "data": data,
                })
    return {"pages_or_sheets": n_pages,
            "text": "\n\n".join(texts), "tables": tables}


# ---------------------------------------------------------------- DOCX

def _iter_docx_blocks(doc):
    """按文档原始顺序产出 ('p', Paragraph) / ('t', Table)。"""
    from docx.document import Document as _Doc
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag.endswith('}p'):
            yield 'p', Paragraph(child, doc)
        elif child.tag.endswith('}tbl'):
            yield 't', Table(child, doc)


def parse_docx(path, aid):
    import docx
    doc = docx.Document(str(path))
    paras, tables = [], []
    for kind, blk in _iter_docx_blocks(doc):
        if kind == 'p':
            paras.append(blk.text)
        else:
            data = [[_cell(c.text) for c in row.cells] for row in blk.rows]
            tables.append({
                "table_index": len(tables),
                "rows": len(data),
                "columns": max((len(r) for r in data), default=0),
                "data": data,
            })
    return {"pages_or_sheets": None,
            "text": "\n".join(paras), "tables": tables}


# ---------------------------------------------------------------- DOC (Word COM)

def parse_doc(path, aid):
    """老版 .doc 走本机 Word COM；无 Word 环境时抛异常由上层记录失败。"""
    import win32com.client
    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    try:
        doc = word.Documents.Open(str(path.resolve()), ReadOnly=True)
        try:
            text = doc.Content.Text or ""
            tables = []
            for ti in range(1, doc.Tables.Count + 1):
                tb = doc.Tables(ti)
                data = []
                for r in range(1, tb.Rows.Count + 1):
                    row = []
                    for c in range(1, tb.Columns.Count + 1):
                        try:
                            cell_txt = tb.Cell(r, c).Range.Text
                            # Word 单元格文本以 \r\x07 结尾
                            cell_txt = cell_txt.replace("\r\x07", "").replace("\x07", "")
                        except Exception:
                            cell_txt = None  # 合并单元格越界
                        row.append(_cell(cell_txt))
                    data.append(row)
                tables.append({
                    "table_index": len(tables),
                    "rows": len(data),
                    "columns": max((len(r) for r in data), default=0),
                    "data": data,
                })
            return {"pages_or_sheets": None, "text": text, "tables": tables}
        finally:
            doc.Close(False)
    finally:
        word.Quit()


# ---------------------------------------------------------------- XLS / XLSX

def parse_xlsx(path, aid):
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        tables = []
        for ws in wb.worksheets:
            data = [[_cell(c) for c in row] for row in ws.iter_rows(values_only=True)]
            tables.append({
                "table_index": len(tables),
                "sheet": ws.title,
                "rows": len(data),
                "columns": max((len(r) for r in data), default=0),
                "data": data,
            })
        return {"pages_or_sheets": wb.sheetnames, "text": "", "tables": tables}
    finally:
        wb.close()


def parse_xls(path, aid):
    import xlrd
    wb = xlrd.open_workbook(str(path))
    tables = []
    for ws in wb.sheets():
        data = [[_cell(ws.cell_value(r, c)) for c in range(ws.ncols)]
                for r in range(ws.nrows)]
        tables.append({
            "table_index": len(tables),
            "sheet": ws.name,
            "rows": len(data),
            "columns": ws.ncols,
            "data": data,
        })
    return {"pages_or_sheets": wb.sheet_names(), "text": "", "tables": tables}


# ---------------------------------------------------------------- 统一入口

PARSERS = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".doc": parse_doc,
    ".xlsx": parse_xlsx,
    ".xls": parse_xls,
}
PARSEABLE_EXTS = set(PARSERS)


def parse_attachment(path, announcement_id, source_file=""):
    path = Path(path)
    ext = path.suffix.lower()
    rec = {
        "announcement_id": announcement_id,
        "source_file": source_file,
        "attachment_filename": path.name,
        "extension": ext,
        "parse_status": "failed",
        "error": None,
        "reason": None,
        "pages_or_sheets": None,
        "text": "",
        "tables": [],
    }
    parser = PARSERS.get(ext)
    if not parser:
        rec["reason"] = f"不支持的扩展名: {ext}"
        return rec
    try:
        out = parser(path, announcement_id)
        rec.update(out)
        rec["parse_status"] = "success"
    except Exception as e:  # noqa: BLE001 - 失败必须记录原始错误
        rec["error"] = f"{type(e).__name__}: {e}"
        rec["reason"] = "解析异常"
    return rec
