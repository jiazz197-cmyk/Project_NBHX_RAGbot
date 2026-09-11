"""Closing form embedding pipeline adapter."""

from __future__ import annotations

from typing import Optional

from llama_index.core.schema import TextNode

from app.core.config import settings
from app.core.logging import get_logger
from app.adapters.closing_form.constants import (
    CLOSING_FORM_INSTANCE_ID,
    CLOSING_FORM_TABLE_PREFIX,
)
from app.adapters.doc_processing.embedding_store import (
    BGEM3EmbeddingWrapper,
    VectorStoreManager,
)
from app.adapters.doc_processing.exceptions import EmbeddingError, VectorStoreError

logger = get_logger("closing_form.embedding")

_EMBED_FAIL_MSG = "嵌入服务暂时不可用，表单已保留，请稍后重试"
_VECTOR_FAIL_MSG = "写入知识库失败，表单已保留，请稍后重试"
_GENERIC_APPROVE_FAIL = "审批失败，表单已保留，请稍后重试"


class ClosingFormEmbeddingAdapter:

    def upsert_approved_form(
        self,
        *,
        text: str,
        uploader: str,
        upload_time: str,
        image_url_1: Optional[str] = None,
        image_url_2: Optional[str] = None,
    ) -> None:
        node = TextNode(
            text=text,
            metadata={
                "uploader": uploader,
                "upload_time": upload_time,
                "status": "approved",
                "image_url_1": image_url_1 or "",
                "image_url_2": image_url_2 or "",
            },
        )
        db_config = {
            "host": settings.POSTGRES_SERVER,
            "user": settings.POSTGRES_USER,
            "password": settings.POSTGRES_PASSWORD,
            "database": settings.POSTGRES_DB,
            "port": settings.POSTGRES_PORT,
        }
        try:
            embedding_model = BGEM3EmbeddingWrapper()
            vector_store_manager = VectorStoreManager(
                db_config=db_config,
                table_prefix=CLOSING_FORM_TABLE_PREFIX,
            )
            vector_store_manager.upsert_chunks(
                chunks=[node],
                instance_id=CLOSING_FORM_INSTANCE_ID,
                embedding_model=embedding_model,
            )
        except EmbeddingError as e:
            logger.error("审批嵌入失败: error=%s", e)
            from app.core.exceptions import APIException
            raise APIException(_EMBED_FAIL_MSG, status_code=503) from e
        except VectorStoreError as e:
            logger.error("审批写入向量存储失败: error=%s", e)
            from app.core.exceptions import APIException
            raise APIException(_VECTOR_FAIL_MSG, status_code=500) from e
        except Exception:
            logger.exception("审批嵌入阶段异常")
            from app.core.exceptions import APIException
            raise APIException(_GENERIC_APPROVE_FAIL, status_code=500) from None
