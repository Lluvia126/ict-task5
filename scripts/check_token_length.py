import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.file_parser import parse_file
from services.ai_adapter import _truncate, MAX_TEXT_CHARS

SAMPLE_DIR = ROOT / "test_data" / "official_samples"

# 尝试加载 Qwen tokenizer
tokenizer = None
try:
    from transformers import AutoTokenizer
    print("加载 Qwen tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(
        "Qwen/Qwen2.5-7B-Instruct",
        trust_remote_code=True,
    )
    print("tokenizer 加载成功\n")
except Exception as e:
    print(f"没装 transformers 或加载失败: {e}")
    print("改用字符估算\n")


def count_tokens(text: str) -> int:
    if tokenizer is not None:
        return len(tokenizer.encode(text, add_special_tokens=False))
    # 粗估：中文 1 字 ≈ 1 token，英文 1 字 ≈ 0.4 token
    zh = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other = len(text) - zh
    return int(zh * 1.0 + other * 0.4)


# 收集所有样本
samples = []
for f in sorted(SAMPLE_DIR.rglob("*")):
    if not f.is_file() or f.name.startswith("."):
        continue
    if f.suffix.lower() not in (".html", ".htm", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip"):
        continue
    try:
        r = parse_file(str(f), f.name)
        text = r.get("text", "") or ""
        samples.append((f.name, text))
    except Exception as e:
        print(f"解析失败 {f.name}: {e}")

if not samples:
    print("没有样本")
    sys.exit(0)

print(f"{'文件':<60} {'字符数':>10} {'token数':>10}")
print("-" * 82)

all_chars, all_tokens = [], []
for name, text in samples:
    chars = len(text)
    toks = count_tokens(text)
    all_chars.append(chars)
    all_tokens.append(toks)
    print(f"{name[:58]:<60} {chars:>10} {toks:>10}")

all_chars.sort()
all_tokens.sort()

print(f"\n{'='*82}")
print(f"统计（{len(samples)} 个样本）")
print(f"  字符数：min={all_chars[0]}, 中位={all_chars[len(all_chars)//2]}, max={all_chars[-1]}, avg={sum(all_chars)//len(all_chars)}")
print(f"  token数：min={all_tokens[0]}, 中位={all_tokens[len(all_tokens)//2]}, max={all_tokens[-1]}, avg={sum(all_tokens)//len(all_tokens)}")

print(f"\n{'='*82}")
print(f"截断策略对比（MAX_TEXT_CHARS = {MAX_TEXT_CHARS}）")
print(f"  截断前 > 12000 字符：{sum(1 for c in all_chars if c > 12000)} 条")
print(f"  截断后估算 token 上限：{count_tokens('中' * MAX_TEXT_CHARS)}")
print(f"  Qwen 常见上限：16384 token")
print(f"  是否安全：{'✅ 是' if count_tokens('中' * MAX_TEXT_CHARS) < 16384 else '❌ 否，需降低 MAX_TEXT_CHARS'}")