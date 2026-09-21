"""最小运行示例：预处理 → 分块 → Qwen 抽取（带后端传入的 ID 字段）。"""
from dotenv import load_dotenv

load_dotenv()  # 读取 .env 中的 QWEN_API_KEY 等

from ai_extraction import preprocess_text, chunk_text, QwenExtractor

# 仅用于测试程序是否正常运行，非官方数据
SAMPLE = "某单位采购联想ThinkPad X1 Carbon笔记本电脑10台，单价8500元，总价85000元。"


def main():
    text = preprocess_text(SAMPLE)
    chunks = chunk_text(text)
    extractor = QwenExtractor()  # 从环境变量读 Key

    for idx, c in enumerate(chunks, 1):
        print(f"--- chunk {idx} ---")
        try:
            result = extractor.extract(
                c,
                announcement_id="TEST_001",
                file_id="FILE_001",
                source_file="test.pdf",
            )
            print("最终结果:", result)
        except Exception as e:
            print("抽取失败:", e)


if __name__ == "__main__":
    main()
