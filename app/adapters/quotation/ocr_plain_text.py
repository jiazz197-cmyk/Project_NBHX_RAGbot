"""Adapter: pdftotext + DotsOCR plain-text extraction for quotation."""

from __future__ import annotations

import base64
import io
import json
import logging
import subprocess
import tempfile

import httpx

from app.core.config import settings
from app.core.http_client import get_sync_http_client
from app.adapters.ocr.pdf2image import pdf_to_images
from app.adapters.ocr.text_cleaning import clean_dotsocr_text, pdftotext_has_key_fields
from app.ports.outbound.quotation import CancelChecker, OcrPlainTextPort, OcrTextExtractionResult

logger = logging.getLogger(__name__)

_PLAIN_TEXT_PROMPT = (
    "Extract all text from this image. Preserve the layout. Output as plain text."
)


class OcrPlainTextAdapter(OcrPlainTextPort):
    def extract_text(
        self,
        *,
        pdf_bytes: bytes,
        cancel_checker: CancelChecker = None,
        ocr_dpi: int = 200,
    ) -> OcrTextExtractionResult:
        if cancel_checker and cancel_checker():
            from app.domain.quotation.exceptions import QuotationPipelineCancelledError
            raise QuotationPipelineCancelledError("任务已取消")

        # 尝试 pdftotext
        pdftotext_text, pdftotext_chars = self._try_pdftotext(pdf_bytes)
        if pdftotext_chars > 100 and pdftotext_has_key_fields(pdftotext_text):
            logger.info("pdftotext 成功, %d 字符", pdftotext_chars)
            return OcrTextExtractionResult(
                text=pdftotext_text,
                extract_method="pdftotext",
                pdftotext_chars=pdftotext_chars,
            )

        # 回退到 DotsOCR
        logger.info("pdftotext 获取 %d 字符, 切换到 DotsOCR...", pdftotext_chars)
        if cancel_checker and cancel_checker():
            from app.domain.quotation.exceptions import QuotationPipelineCancelledError
            raise QuotationPipelineCancelledError("任务已取消")

        dotsocr_text, dotsocr_chars = self._try_dotsocr(pdf_bytes, ocr_dpi)
        if dotsocr_chars > 50:
            cleaned = clean_dotsocr_text(dotsocr_text)
            logger.info("DotsOCR 清洗后, %d 字符", len(cleaned))
            return OcrTextExtractionResult(
                text=cleaned,
                extract_method="dotsocr",
                pdftotext_chars=pdftotext_chars,
                dotsocr_chars=dotsocr_chars,
            )

        logger.warning("DotsOCR 获取 %d 字符, 不足以继续", dotsocr_chars)
        return OcrTextExtractionResult(
            text="",
            extract_method="failed",
            pdftotext_chars=pdftotext_chars,
            dotsocr_chars=dotsocr_chars,
        )

    # ── pdftotext ───────────────────────────────────────────

    def _try_pdftotext(self, pdf_bytes: bytes) -> tuple[str, int]:
        if not getattr(settings, "OCR_PDFTEXT_ENABLED", True):
            return "", 0
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(pdf_bytes)
                tmp_path = tmp.name
            result = subprocess.run(
                ["pdftotext", "-layout", tmp_path, "-"],
                capture_output=True, text=True,
                timeout=getattr(settings, "OCR_PDFTEXT_TIMEOUT", 30),
            )
            text = result.stdout if result.returncode == 0 else ""
        except FileNotFoundError:
            logger.warning("pdftotext 未安装, 跳过")
            return "", 0
        except Exception as e:
            logger.warning("pdftotext 失败: %s", e)
            return "", 0
        finally:
            if tmp_path:
                try:
                    import os
                    os.unlink(tmp_path)
                except Exception:
                    pass
        return text, len(text.strip())

    # ── DotsOCR plain text ──────────────────────────────────

    def _try_dotsocr(self, pdf_bytes: bytes, dpi: int) -> tuple[str, int]:
        try:
            pages = pdf_to_images(pdf_bytes, dpi=dpi, fmt="JPEG", quality=85)
        except Exception as e:
            logger.error("pdf2image 转换失败: %s", e)
            return "", 0

        if not pages:
            logger.warning("PDF 无页面")
            return "", 0

        endpoint = settings.DOTS_OCR_ENDPOINT
        max_tokens = getattr(settings, "OCR_DOTSOCR_MAX_TOKENS", 4096)
        ct = settings.OCR_HTTP_CONNECT_TIMEOUT
        rt = settings.OCR_HTTP_READ_TIMEOUT

        all_texts: list[str] = []
        for page_idx, (img_bytes, _) in enumerate(pages):
            b64_data = base64.b64encode(img_bytes).decode("ascii")
            payload = {
                "model": "rednote-hilab/dots.ocr",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _PLAIN_TEXT_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_data}"}},
                    ],
                }],
                "max_tokens": max_tokens,
                "temperature": 0,
            }

            try:
                client = get_sync_http_client()
                resp = client.post(
                    endpoint,
                    json=payload,
                    timeout=httpx.Timeout(rt, connect=ct),
                )
                if not resp.is_success:
                    logger.warning("DotsOCR 第%d页 HTTP %d", page_idx + 1, resp.status_code)
                    continue
                result = resp.json()
                text = ""
                if "choices" in result and result["choices"]:
                    text = result["choices"][0].get("message", {}).get("content", "")
                elif "content" in result:
                    text = result["content"]
                elif "text" in result:
                    text = result["text"]
                if text.strip():
                    all_texts.append(text)
                    logger.info("DotsOCR 第%d页 识别成功, %d 字符", page_idx + 1, len(text))
            except httpx.TimeoutException as e:
                logger.warning("DotsOCR 第%d页 超时: %s", page_idx + 1, e)
            except Exception as e:
                logger.warning("DotsOCR 第%d页 异常: %s", page_idx + 1, e)

        combined = "\n".join(all_texts)
        return combined, len(combined.strip())
