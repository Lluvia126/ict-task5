import re


def preprocess_text(text):
    """文本预处理：去空白、统一换行、清理 HTML 标签，保留关键信息。"""
    if not text:
        return ""
    # 清理 HTML 标签
    text = re.sub(r"<[^>]+>", " ", text)
    # 常见 HTML 实体
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">"))
    # 统一换行
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 去行尾空白
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    # 连续空行最多留一个
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 压缩连续空格（保留单个空格，不破坏规格型号中的空格）
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()
