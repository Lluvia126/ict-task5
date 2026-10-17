# -*- coding: utf-8 -*-
"""test_extract_request_params.py — 校验 QwenExtractor 发出的请求参数（离线 mock，不联网）。

背景：qwen3-8b-33dd2dfc123f 在非流式调用下若未显式关闭 thinking，服务端返回
  400 parameter.enable_thinking must be set to false for non-streaming calls
因此 extract() 必须显式 stream=False 且 extra_body.enable_thinking=False，
同时保持 model / messages / response_format / temperature 原样不变。

运行：
  E:\\ICT竞赛项目\\.venv\\Scripts\\python.exe ai_extraction/tests/test_extract_request_params.py
（也可用 pytest 收集 test_* 函数）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openai import OpenAI  # noqa: E402

from ai_extraction.extractors.qwen_extractor import QwenExtractor  # noqa: E402

MODEL = "qwen3-8b-33dd2dfc123f"
FAKE_RESPONSE = '{"project_name": "测试项目", "products": []}'


class _FakeCompletions:
    def __init__(self, recorder):
        self._recorder = recorder

    def create(self, **kwargs):
        self._recorder.append(kwargs)
        return type("R", (), {"choices": [
            type("C", (), {"message": type("M", (), {"content": FAKE_RESPONSE})()})()
        ]})()


class _FakeClient:
    """替身客户端：记录入参，不发起任何网络请求。"""

    def __init__(self):
        self.calls = []
        self.chat = type("Chat", (), {"completions": _FakeCompletions(self.calls)})()


def _make_extractor(extra_body=None):
    ex = QwenExtractor(api_key="test-key", base_url="http://127.0.0.1:9/v1", model=MODEL)
    if extra_body is not None:
        ex.extra_body = extra_body
    ex.client = _FakeClient()
    return ex


def test_stream_false_and_enable_thinking_false():
    ex = _make_extractor()
    ex.extract("正文", "ANN_TEST", "F1", "a.html")
    assert len(ex.client.calls) == 1, "应只发起一次请求"
    kwargs = ex.client.calls[0]

    assert kwargs.get("stream") is False, f"stream 必须显式 False，实际 {kwargs.get('stream')!r}"
    body = kwargs.get("extra_body")
    assert isinstance(body, dict), f"extra_body 必须是 dict，实际 {type(body).__name__}"
    assert body.get("enable_thinking") is False, \
        f"enable_thinking 必须为 False，实际 {body.get('enable_thinking')!r}"


def test_other_params_preserved():
    ex = _make_extractor()
    ex.extract("正文", "ANN_TEST")
    kwargs = ex.client.calls[0]
    assert kwargs.get("model") == MODEL, "model 不应改变"
    assert kwargs.get("temperature") == 0, "temperature 不应改变"
    assert kwargs.get("response_format") == {"type": "json_object"}, "response_format 不应改变"
    msgs = kwargs.get("messages")
    assert isinstance(msgs, list) and len(msgs) == 1, "messages 结构不应改变"
    assert msgs[0]["role"] == "user"
    assert "正文" in msgs[0]["content"], "用户输入应被注入 messages"


def test_extra_body_merged_not_overwritten():
    ex = _make_extractor(extra_body={"x": 1, "enable_thinking": True})
    ex.extract("正文", "ANN_TEST")
    body = ex.client.calls[0]["extra_body"]
    assert body.get("x") == 1, "已有 extra_body 字段不应丢失"
    assert body.get("enable_thinking") is False, "enable_thinking 应被置为 False"
    assert ex.extra_body == {"x": 1, "enable_thinking": True}, "不得修改实例上的原 extra_body"


def test_wire_payload_via_mock_transport():
    """走真实 OpenAI 客户端 + httpx.MockTransport，校验真正发出的 JSON body（无网络）。"""
    import json

    import httpx

    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={
            "id": "chatcmpl-test", "object": "chat.completion", "created": 0,
            "model": MODEL,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": FAKE_RESPONSE}}],
        })

    ex = QwenExtractor(api_key="test-key", base_url="http://127.0.0.1:9/v1", model=MODEL)
    ex.client = OpenAI(api_key="test-key", base_url="http://127.0.0.1:9/v1",
                       http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    ex.extract("正文", "ANN_TEST")

    body = captured["body"]
    assert body.get("stream") is False, f"线上 body.stream 应为 False，实际 {body.get('stream')!r}"
    assert body.get("enable_thinking") is False, \
        f"线上 body.enable_thinking 应为 False，实际 {body.get('enable_thinking')!r}"
    assert body.get("model") == MODEL
    assert body.get("temperature") == 0
    assert body.get("response_format") == {"type": "json_object"}


def run():
    tests = [
        test_stream_false_and_enable_thinking_false,
        test_other_params_preserved,
        test_extra_body_merged_not_overwritten,
        test_wire_payload_via_mock_transport,
    ]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"  PASS {t.__name__}")
    print(f"== 结果 == 通过 {passed} / 共 {len(tests)}")
    print("ALL PASS：请求参数含 stream=False 与 extra_body.enable_thinking=False")
    return passed, len(tests)


if __name__ == "__main__":
    run()
