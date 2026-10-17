from abc import ABC, abstractmethod


class BaseExtractor(ABC):
    """抽取器基类。后续 Transformer NER 路线也继承此类，保证接口统一。"""

    @abstractmethod
    def extract(self, text, announcement_id, file_id="", source_file=""):
        """输入纯文本 + 后端管理的 ID 字段，返回完整结构化 dict。失败时抛出异常。"""
        raise NotImplementedError
