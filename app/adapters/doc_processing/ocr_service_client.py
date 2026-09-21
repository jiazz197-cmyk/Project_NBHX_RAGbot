"""PaddleX Serving OCR 客户端（HTTP，逐页调用）。

对接独立 paddlex 容器（issue #1「把 PaddleOCR 拆成独立 OCR 容器」，进程内
paddlepaddle 已剥离）。协议（PaddleX Serving 标准 schema，实测确认）：

    POST {PADDLE_OCR_ENDPOINT}
    {"file": "<base64 或 URL>", "fileType": 1, "visualize": false}
    → 200 {"logId": ..., "errorCode": 0, "result": {"ocrResults": [
         {"prunedResult": {"rec_texts": [...], "rec_scores": [...]}}}]}}

fileType：0=PDF、1=图片。本项目走 **逐页** 调用（fileType=1）：调用方
（``doc_reader.PdfParser``）先用 pdfplumber 提文本层，只有无文本层的页才
渲染成 PNG 送来这里——保住「有文本层的 PDF 零 OCR」短路，页数完整、
单页失败只影响单页（整本直传实测会丢页且响应高达数十 MB）。

``visualize: false`` 只回 prunedResult：实测 300DPI A4 扫描页响应从
1.9MB 降到 4.9KB（省掉 ocrImage / docPreprocessingImage / inputImage 回显）。

失败语义与进程内 PaddleOCR 时期保持一致——**逐页降级**：任何失败
（网络异常 / HTTP 非 2xx / 业务 errorCode≠0 / 响应解析失败）记 WARNING
并返回空字符串，任务继续，不上抛。
"""

from __future__ import annotations

import base64
import logging

import httpx

from app.core.config import settings
from app.core.http_client import get_sync_http_client

logger = logging.getLogger(__name__)


class PaddleXOcrClient:
    """同步 OCR 客户端，供 worker 线程（PdfParser）使用。

    复用共享 ``httpx.Client`` 单例（连接池），单请求自带超时覆盖
    （约定见 ``app.core.http_client`` 模块注释）。
    """

    def __init__(self, endpoint: str):
        self._endpoint = endpoint

    def ocr_image_bytes(self, image_bytes: bytes, *, page_no: int) -> str:
        """识别单页图片，返回按行 ``"\\n"`` 拼接的文本；失败返回空串（逐页降级）。"""
        payload = {
            "file": base64.b64encode(image_bytes).decode("ascii"),
            "fileType": 1,  # 1=图片；PDF 由调用方逐页渲染后逐页送入
            "visualize": False,  # 只要 prunedResult，砍掉 MB 级回显图
        }
        try:
            resp = get_sync_http_client().post(
                self._endpoint,
                json=payload,
                timeout=httpx.Timeout(
                    settings.OCR_HTTP_READ_TIMEOUT,
                    connect=settings.OCR_HTTP_CONNECT_TIMEOUT,
                ),
            )
        except Exception as exc:
            logger.warning("第 %d 页 OCR 服务调用失败: %s", page_no, exc)
            return ""

        try:
            if not resp.is_success:
                logger.warning("第 %d 页 OCR 服务 HTTP %d", page_no, resp.status_code)
                return ""
            data = resp.json()
        except Exception as exc:
            logger.warning("第 %d 页 OCR 响应解析失败: %s", page_no, exc)
            return ""

        error_code = data.get("errorCode")
        if error_code not in (0, None):
            logger.warning(
                "第 %d 页 OCR 服务返回错误 errorCode=%s errorMsg=%s",
                page_no,
                error_code,
                data.get("errorMsg"),
            )
            return ""

        ocr_results = (data.get("result") or {}).get("ocrResults") or []
        if not ocr_results:
            logger.warning("第 %d 页 OCR 服务未返回结果", page_no)
            return ""

        rec_texts = ocr_results[0].get("prunedResult", {}).get("rec_texts") or []
        return "\n".join(str(text) for text in rec_texts)
