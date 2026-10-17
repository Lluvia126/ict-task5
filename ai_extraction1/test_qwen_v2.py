"""Qwen V2 Prompt 小规模测试脚本（临时）。

对 3 个代表性样本走完整链路：HTML → preprocess_text → chunk_text → QwenExtractor.extract
不修改任何现有业务代码，不运行全部数据，不读取/打印 API Key。
"""
import traceback
from pathlib import Path

from ai_extraction.preprocessing.cleaner import preprocess_text
from ai_extraction.preprocessing.chunker import chunk_text
from ai_extraction.extractors.qwen_extractor import QwenExtractor

SAMPLES = [
    "t20260811_27115458",  # 复杂表格
    "t20260804_27070085",  # 服务类项目
    "t20260306_26238138",  # 详见投标文件
]

INPUT_DIR = Path(__file__).parent / "data" / "baseline_input"


def main():
    extractor = QwenExtractor()

    for aid in SAMPLES:
        print(f"\n{'='*70}")
        print(f"样本: {aid}")
        print(f"{'='*70}")

        html_path = INPUT_DIR / f"{aid}.html"
        if not html_path.exists():
            print(f"  [跳过] 文件不存在: {html_path}")
            continue

        try:
            html = html_path.read_text(encoding="utf-8", errors="ignore")
            text = preprocess_text(html)
            chunks = chunk_text(text)
            full = "\n".join(chunks)

            result = extractor.extract(
                full,
                announcement_id=aid,
                source_file=f"{aid}.html",
            )

            products = result["products"]
            print(f"  产品总数: {len(products)}")
            print(f"  {'#':<3} {'product_service_name':<36} {'brand':<20} {'spec_model':<24} {'unit_price':>10} {'quantity':>8} {'total_price':>10}")
            print(f"  {'-'*120}")

            for i, p in enumerate(products[:10], 1):
                name = str(p.get("product_service_name") or "")[:34]
                brand = str(p.get("brand") or "")[:18]
                spec = str(p.get("spec_model") or "")[:22]
                price = p.get("unit_price")
                qty = p.get("quantity")
                total = p.get("total_price")
                print(f"  {i:<3} {name:<36} {brand:<20} {spec:<24} {str(price):>10} {str(qty):>8} {str(total):>10}")

            if len(products) > 10:
                print(f"  ... 共 {len(products)} 个产品，仅显示前 10 个")

        except Exception:
            print(f"  [失败] 异常:")
            traceback.print_exc()
            continue


if __name__ == "__main__":
    main()
