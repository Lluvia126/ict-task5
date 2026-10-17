"""HTML 表格结构分析与预处理 POC（仅针对 t20260811_27115458.html）。

不调用 Qwen，不做产品语义推断，不猜品牌/型号，不做模糊匹配。
只恢复 HTML 已经表达的结构：

  1. 通用二维网格展开：处理 rowspan / colspan，继承单元格显式标记来源。
  2. 产品表（表头含 品目号/采购标的/品牌/规格型号）额外检测：
     单元格内"分隔符串联的并行列表"，按位置恢复 品牌[i]<->型号[i]。
     —— HTML 未把每一对包成独立行，这属于"等长列表的位置对应（结构推断）"，
        仅当两列表长度相等时才输出，并明确标注，不做猜测。

输出：ai_extraction/data/table_test/t20260811_27115458_structured.txt
"""
import re
from pathlib import Path
from bs4 import BeautifulSoup

SRC = Path(__file__).parent.parent / "data" / "validation" / "t20260811_27115458.html"
OUT_DIR = Path(__file__).parent.parent / "data" / "table_test"
OUT = OUT_DIR / "t20260811_27115458_structured.txt"

PRODUCT_HEADER_KEYS = ("品目号", "采购标的", "品牌", "规格型号")
LIST_DELIMS = ("、", "；", ";", "，", ",")


def cell_text(cell):
    for br in cell.find_all("br"):
        br.replace_with("\n")
    return cell.get_text("", strip=True)


def get_span(cell, attr):
    try:
        return max(1, int(cell.get(attr, 1)))
    except (TypeError, ValueError):
        return 1


def build_grid(table):
    """把 table 展开成二维网格，处理 rowspan/colspan。

    每个网格单元为 dict: {text, origin}
      origin=None 表示原始单元格；
      origin=(r,c) 表示该位置由 (r,c) 的 span 覆盖（继承）。
    返回 (grid, ncols)。
    """
    grid = []

    def ensure(r, c):
        while len(grid) <= r:
            grid.append([])
        while len(grid[r]) <= c:
            grid[r].append(None)

    occupied = set()  # 已被 span 占位的 (r,c)

    for r, tr in enumerate(table.find_all("tr")):
        c = 0
        for cell in tr.find_all(["th", "td"]):
            while (r, c) in occupied:
                c += 1
            rowspan = get_span(cell, "rowspan")
            colspan = get_span(cell, "colspan")
            txt = cell_text(cell)
            for dr in range(rowspan):
                for dc in range(colspan):
                    rr, cc = r + dr, c + dc
                    ensure(rr, cc)
                    if dr == 0 and dc == 0:
                        grid[rr][cc] = {"text": txt, "origin": None,
                                        "rs": rowspan, "cs": colspan}
                    else:
                        grid[rr][cc] = {"text": txt, "origin": (r, c)}
                    occupied.add((rr, cc))
            c += colspan

    ncols = max((len(row) for row in grid), default=0)
    for row in grid:
        while len(row) < ncols:
            row.append(None)
    return grid, ncols


def split_list(s):
    """按候选分隔符切分。选择"能切成最多非空段"的那个分隔符，避免误伤。"""
    best = None
    for d in ("；", ";", "、"):
        parts = [p.strip() for p in s.split(d) if p.strip()]
        if len(parts) > 1 and (best is None or len(parts) > len(best)):
            best = parts
    return best or [s.strip()]


def header_names(table):
    rows = table.find_all("tr")
    if not rows:
        return []
    return [cell_text(c) for c in rows[0].find_all(["th", "td"])]


def is_product_table(table):
    hdr = "|".join(header_names(table))
    return all(k in hdr for k in PRODUCT_HEADER_KEYS)


def analyze():
    html = SRC.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")

    out = []
    out.append("HTML 表格结构恢复结果")
    out.append(f"源文件: {SRC.name}")
    out.append(f"表格总数: {len(tables)}")
    n_rowspan = len(soup.find_all(attrs={"rowspan": True}))
    n_colspan = len(soup.find_all(attrs={"colspan": True}))
    out.append(f"rowspan 单元格: {n_rowspan} | colspan 单元格: {n_colspan}")
    out.append("说明: 本文件无 rowspan；15 个 colspan 全部在表0(公告信息)。")
    out.append("      产品表(表3/表4)不含 rowspan/colspan，其'复杂'来自单元格内的分隔符并行列表。")
    out.append("=" * 78)

    for ti, table in enumerate(tables):
        grid, ncols = build_grid(table)
        out.append(f"\n【表{ti}】 展开后行数={len(grid)} 列数={ncols} "
                   f"{'<< 产品表 >>' if is_product_table(table) else ''}")
        # 表头
        hdr = header_names(table)
        out.append("表头: " + " | ".join(hdr))

        for r, row in enumerate(grid):
            out.append(f"\n[行{r + 1}]")
            for c, unit in enumerate(row):
                if unit is None:
                    out.append(f"列{c + 1}=[无法从HTML结构确定: 该位置无单元格]")
                elif unit["origin"] is not None:
                    orr, orc = unit["origin"]
                    out.append(f"列{c + 1}=[继承自第{orr + 1}行第{orc + 1}列] "
                               f"原文={unit['text']!r}")
                else:
                    tag = ""
                    if unit["rs"] > 1 or unit["cs"] > 1:
                        tag = f" (rowspan={unit['rs']},colspan={unit['cs']})"
                    out.append(f"列{c + 1}={unit['text']!r}{tag}")

        # 产品表：并行列表位置对应
        if is_product_table(table) and len(grid) >= 2:
            out.append("\n  ---- 单元格内并行列表：品牌 <-> 规格型号 位置对应 ----")
            # 依据表头定位列
            def col_of(key):
                for i, h in enumerate(hdr):
                    if key in h:
                        return i
                return None
            ci_name = col_of("采购标的")
            ci_brand = col_of("品牌")
            ci_spec = col_of("规格型号")
            # 每个数据行
            for r in range(1, len(grid)):
                row = grid[r]
                lot = row[ci_name]["text"] if ci_name is not None else ""
                brand_txt = row[ci_brand]["text"] if ci_brand is not None else ""
                spec_txt = row[ci_spec]["text"] if ci_spec is not None else ""
                brands = split_list(brand_txt)
                specs = split_list(spec_txt)
                out.append(f"\n  数据行{r + 1} 采购标的={lot!r}")
                out.append(f"    品牌列表条目={len(brands)} 规格列表条目={len(specs)}")
                if len(brands) == len(specs):
                    out.append("    长度相等 → 可按位置对应（结构推断；HTML 未显式将每对包成独立行）:")
                    for i, (b, sp) in enumerate(zip(brands, specs), 1):
                        out.append(f"      [{i}] 品牌={b} | 规格型号={sp}")
                else:
                    out.append("    长度不等 → 无法从HTML结构确定一一对应，不猜测。")

    # 总结：可恢复 / 不可恢复
    out.append("\n" + "=" * 78)
    out.append("可恢复性结论")
    out.append("=" * 78)
    out.append("1. 9 个表格的行列关系均可恢复；无 rowspan，colspan 仅在公告信息表，已展开标记。")
    out.append("2. 产品表实为 2 个'宗/包'(1-1 AIGC, 2-1 人工智能与数字电路)。")
    out.append("3. 每个'宗'内品牌串与规格串等长(19=19, 28=28)，")
    out.append("   可按位置恢复 47 对 品牌<->规格型号 的对应（结构推断，非显式标签）。")
    out.append("4. 即使结构恢复，以下仍无法从HTML确定：")
    out.append("   - 各子项的单价/总价：本表只有 品目号/品目名称/采购标的/品牌/规格型号/数量，无价格列。")
    out.append("   - 子项独立名称：采购标的单元格只有整宗名称(AIGC)，无逐子项名称。")
    out.append("   - 位置对应依赖'两份列表按同一次序列出'这一前提；HTML 未逐对包裹，")
    out.append("     故标注为结构推断而非显式事实；若来源未严格对齐则无法保证。")

    return "\n".join(out)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    text = analyze()
    OUT.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n已保存: {OUT}")


if __name__ == "__main__":
    main()
