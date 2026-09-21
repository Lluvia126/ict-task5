from .base import BaseExtractor


class TransformerExtractor(BaseExtractor):
    """方案B：Transformer NER 抽取器（接口预留，后续实现训练与推理）。

    当前不实现训练，仅保留接口，方便后续接入第二条技术路线：
        文本 -> Transformer NER -> 实体识别 -> 字段映射 -> 结构化字段
    """

    def __init__(self, model_path=None):
        self.model_path = model_path
        # TODO: 后续加载微调后的 Transformer NER 模型

    def extract(self, text):
        raise NotImplementedError("Transformer NER 路线尚未实现，当前请使用 DeepSeekExtractor。")
