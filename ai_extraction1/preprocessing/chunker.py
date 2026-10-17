import re


def chunk_text(text, max_chars=4000):
    """按段落切分文本，控制单块长度，避免在商品信息中间随意截断。"""
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks = []
    paragraphs = re.split(r"\n{2,}", text)
    cur = ""
    for p in paragraphs:
        if not p.strip():
            continue
        # 单段超过上限：硬切（尽量整段送，别在词中间断）
        if len(p) > max_chars:
            if cur:
                chunks.append(cur.strip())
                cur = ""
            for i in range(0, len(p), max_chars):
                chunks.append(p[i:i + max_chars].strip())
            continue
        # 能并入当前块就并入
        if len(cur) + len(p) + 2 <= max_chars:
            cur = cur + "\n\n" + p if cur else p
        else:
            if cur:
                chunks.append(cur.strip())
            cur = p
    if cur:
        chunks.append(cur.strip())
    return chunks
