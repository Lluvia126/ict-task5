import json
import re

from openai import OpenAI

from ..config import (
    QWEN_API_KEY, QWEN_BASE_URL, QWEN_MODEL,
    EXTRACT_PROMPT, empty_result, PRODUCT_FIELDS,
)
from .base import BaseExtractor


def _clean_str(v):
    """字符串字段 strip，空串转 None；清理引用性后缀和纯引用文字。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    # 纯引用性文字：以"详见"/"等详见"/"等 详见"开头
    if s.startswith("详见") or s.startswith("等详见") or s.startswith("等 详见"):
        return None
    # 混合内容：真实值 + 引用性后缀，截断保留前面内容
    for marker in ("等详见", "等 详见", "详见投标文件", "详见详细分项报价表", "详见采购文件", "详见分项"):
        idx = s.find(marker)
        if idx > 0:
            s = s[:idx].rstrip("等、，, ·")
            break
    if not s:
        return None
    return s


def _to_number(v):
    """数字字段：保留数字，剥离单位等非数字字符。不做金额换算。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    if not s:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    num = m.group()
    return float(num) if "." in num else int(num)


def _clean_list(v):
    """保证返回 list；字符串元素 strip 去空。"""
    if v is None:
        return []
    if isinstance(v, list):
        out = []
        for x in v:
            if isinstance(x, str):
                s = x.strip()
                if s:
                    out.append(s)
            elif x is not None:
                out.append(x)  # 保留对象等结构
        return out
    if isinstance(v, str):
        s = v.strip()
        return [s] if s else []
    return []


class QwenExtractor(BaseExtractor):
    """方案A：Qwen Prompt 抽取。保留原报错溯源逻辑。

    announcement_id/file_id/source_file 由后端管理，AI 原样返回，不自行生成。
    """

    def __init__(self, api_key=None, base_url=None, model=None):
        self.api_key = api_key or QWEN_API_KEY
        self.base_url = base_url or QWEN_BASE_URL
        self.model = model or QWEN_MODEL
        if not self.api_key:
            raise ValueError("未配置 QWEN_API_KEY，请设置环境变量或传入 api_key。")
        # 复用 client，避免每次调用都新建
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def extract(self, text, announcement_id, file_id="", source_file=""):
        # 后端字段原样回填，模型不参与
        result = empty_result(announcement_id, file_id, source_file)

        # 合并式 extra_body：保留已配置项，仅补充 enable_thinking=False
        # （Qwen3 推理模型在非流式调用下必须显式关闭 thinking，否则 400）
        extra_body = dict(getattr(self, "extra_body", None) or {})
        extra_body["enable_thinking"] = False

        # 1) 调用 API（非流式；下游按完整响应解析）
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": EXTRACT_PROMPT.replace("{text}", text)}],
                response_format={"type": "json_object"},
                temperature=0,
                stream=False,
                extra_body=extra_body,
            )
            raw = resp.choices[0].message.content
        except Exception as e:
            print("===== API 调用出错 =====")
            print("错误类型:", type(e).__name__)
            print("错误信息:", e)
            raise RuntimeError(f"API调用失败: {type(e).__name__}: {e}")

        print("===== 大模型原始返回 =====")
        print(raw)

        # 2) 清洗可能的 ```json ... ``` 包裹
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        # 3) 解析 JSON
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            print("===== JSON 解析失败 =====")
            print("错误类型:", type(e).__name__)
            print("错误信息:", e)
            print("大模型原始返回:", raw)
            raise RuntimeError(f"JSON解析失败: {e}; 原始返回: {raw}")

        if not isinstance(data, dict):
            data = {}

        # 4) 项目级字段填充 + 清洗
        result["project_name"] = _clean_str(data.get("project_name"))
        result["project_code"] = _clean_str(data.get("project_code"))
        result["purchaser"] = _clean_str(data.get("purchaser"))
        result["project_amount"] = _to_number(data.get("project_amount"))
        result["winning_suppliers"] = _clean_list(data.get("winning_suppliers"))
        result["bid_participants"] = _clean_list(data.get("bid_participants"))

        # 5) 产品级字段填充：每个 product 保证含全部 7 字段，缺失填 null
        raw_products = data.get("products")
        if not isinstance(raw_products, list):
            raw_products = []
        products = []
        for p in raw_products:
            if not isinstance(p, dict):
                continue
            products.append({
                "product_service_name": _clean_str(p.get("product_service_name")),
                "category": _clean_str(p.get("category")),
                "brand": _clean_str(p.get("brand")),
                "spec_model": _clean_str(p.get("spec_model")),
                "unit_price": _to_number(p.get("unit_price")),
                "quantity": _to_number(p.get("quantity")),
                "total_price": _to_number(p.get("total_price")),
            })
        result["products"] = products

        missing_proj = [f for f in ("project_name", "project_code", "purchaser", "project_amount")
                        if result[f] is None]
        if missing_proj and not products:
            print("===== 提示：项目级字段与产品均为空（已填 null/[]）=====")
            print("缺失:", missing_proj)

        return result
