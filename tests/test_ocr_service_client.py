"""PaddleX Serving OCR 客户端单测（issue #1 OCR 服务化）。

进程内 PaddleOCR 已剥离，无文本层的 PDF 页渲染成 PNG 后经
``PaddleXOcrClient`` 调独立 paddlex 容器（POST /ocr，fileType=1，
visualize=false）。本文件用 monkeypatch 假 httpx 客户端覆盖协议层：

- 成功路径：rec_texts 按行 join；
- 失败降级：网络异常 / HTTP 非 2xx / errorCode!=0 / 响应非 JSON /
  空 ocrResults —— 一律返回空串并继续（逐页降级语义，与进程内时期一致）；
- 请求体契约：file 为 base64、fileType=1、visualize=False。

CI 说明：客户端模块只依赖 httpx + config，无重依赖，CI 可跑；
PdfParser 侧用例沿用 importorskip 模式（pdfplumber 在 dev 镜像才有）。
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest


# ---------------------------------------------------------------------------
# 假 httpx 客户端：记录请求体，按脚本返回响应/抛异常
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, *, status_code=200, payload=None, raise_on_json=None):
        self.status_code = status_code
        self._payload = payload
        self._raise_on_json = raise_on_json

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        if self._raise_on_json is not None:
            raise self._raise_on_json
        return self._payload


class _FakeHttpClient:
    def __init__(self, script):
        # script: 可调用(payload: dict) -> _FakeResponse 或抛异常
        self.script = script
        self.calls: list[dict] = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "payload": json, "timeout": timeout})
        return self.script(json)


def _ok_payload(texts):
    return {
        "logId": "x",
        "errorCode": 0,
        "result": {
            "ocrResults": [{"prunedResult": {"rec_texts": texts, "rec_scores": [0.9] * len(texts)}}]
        },
    }


@pytest.fixture()
def client(monkeypatch):
    """构造 PaddleXOcrClient，并把共享 httpx 单例替换为假客户端。"""
    from app.adapters.doc_processing.ocr_service_client import PaddleXOcrClient

    fake = _FakeHttpClient(lambda payload: _FakeResponse(payload=_ok_payload(["你好", "世界"])))
    monkeypatch.setattr(
        "app.adapters.doc_processing.ocr_service_client.get_sync_http_client",
        lambda: fake,
    )
    return fake, PaddleXOcrClient("http://paddlex:9002/ocr")


# ---------------------------------------------------------------------------
# 成功路径 + 请求契约
# ---------------------------------------------------------------------------


def test_ocr_success_joins_rec_texts(client):
    fake, ocr = client
    text = ocr.ocr_image_bytes(b"\x89PNG-fake", page_no=1)
    assert text == "你好\n世界"
    # 请求契约：base64 / fileType=1 / visualize=False
    assert fake.calls[0]["payload"]["file"] == base64.b64encode(b"\x89PNG-fake").decode("ascii")
    assert fake.calls[0]["payload"]["fileType"] == 1
    assert fake.calls[0]["payload"]["visualize"] is False
    assert fake.calls[0]["url"] == "http://paddlex:9002/ocr"


# ---------------------------------------------------------------------------
# 逐页降级：一切失败都返回空串，不抛异常
# ---------------------------------------------------------------------------


def test_http_500_degrades_to_empty(monkeypatch):
    from app.adapters.doc_processing.ocr_service_client import PaddleXOcrClient

    fake = _FakeHttpClient(lambda payload: _FakeResponse(status_code=500))
    monkeypatch.setattr(
        "app.adapters.doc_processing.ocr_service_client.get_sync_http_client", lambda: fake
    )
    assert PaddleXOcrClient("http://x/ocr").ocr_image_bytes(b"img", page_no=3) == ""


def test_network_error_degrades_to_empty(monkeypatch):
    from app.adapters.doc_processing.ocr_service_client import PaddleXOcrClient

    def boom(payload):
        raise httpx.ConnectError("connection refused")

    fake = _FakeHttpClient(boom)
    monkeypatch.setattr(
        "app.adapters.doc_processing.ocr_service_client.get_sync_http_client", lambda: fake
    )
    assert PaddleXOcrClient("http://x/ocr").ocr_image_bytes(b"img", page_no=2) == ""


def test_business_error_code_degrades_to_empty(monkeypatch):
    from app.adapters.doc_processing.ocr_service_client import PaddleXOcrClient

    payload = {"logId": "x", "errorCode": 500, "errorMsg": "Internal server error"}
    fake = _FakeHttpClient(lambda p: _FakeResponse(payload=payload))
    monkeypatch.setattr(
        "app.adapters.doc_processing.ocr_service_client.get_sync_http_client", lambda: fake
    )
    assert PaddleXOcrClient("http://x/ocr").ocr_image_bytes(b"img", page_no=4) == ""


def test_malformed_json_degrades_to_empty(monkeypatch):
    from app.adapters.doc_processing.ocr_service_client import PaddleXOcrClient

    fake = _FakeHttpClient(lambda p: _FakeResponse(raise_on_json=json.JSONDecodeError("bad", "", 0)))
    monkeypatch.setattr(
        "app.adapters.doc_processing.ocr_service_client.get_sync_http_client", lambda: fake
    )
    assert PaddleXOcrClient("http://x/ocr").ocr_image_bytes(b"img", page_no=5) == ""


def test_empty_ocr_results_degrades_to_empty(monkeypatch):
    from app.adapters.doc_processing.ocr_service_client import PaddleXOcrClient

    payload = {"logId": "x", "errorCode": 0, "result": {"ocrResults": []}}
    fake = _FakeHttpClient(lambda p: _FakeResponse(payload=payload))
    monkeypatch.setattr(
        "app.adapters.doc_processing.ocr_service_client.get_sync_http_client", lambda: fake
    )
    assert PaddleXOcrClient("http://x/ocr").ocr_image_bytes(b"img", page_no=6) == ""


# ---------------------------------------------------------------------------
# PdfParser 集成点：endpoint 未配置 → OCR 整体禁用（不构造客户端）
# ---------------------------------------------------------------------------


def test_pdf_parser_disables_ocr_without_endpoint(monkeypatch):
    pytest.importorskip("pdfplumber")
    from app.adapters.doc_processing import doc_reader

    monkeypatch.setattr(doc_reader.settings, "PADDLE_OCR_ENDPOINT", None)
    parser = doc_reader.PdfParser(save_dir="/tmp/nbhx_test_ocr_images")
    assert parser._ocr_client is None


def test_pdf_parser_builds_client_with_endpoint(monkeypatch):
    pytest.importorskip("pdfplumber")
    from app.adapters.doc_processing import doc_reader

    monkeypatch.setattr(
        doc_reader.settings, "PADDLE_OCR_ENDPOINT", "http://paddlex:9002/ocr"
    )
    parser = doc_reader.PdfParser(save_dir="/tmp/nbhx_test_ocr_images")
    assert parser._ocr_client is not None
