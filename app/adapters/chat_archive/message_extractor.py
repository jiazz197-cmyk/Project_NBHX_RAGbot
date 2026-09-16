"""LangChain-based user query summarization (long-term memory generation).

Message text comes exclusively from the local
:class:`app.ports.outbound.chat.ChatMessageRepositoryPort`; the only external
dependency is the configured OpenAI-compatible LLM used for summarization.
Both the endpoint and the model come from ``settings`` (``QWEN3_6_35B_*``), so
pointing the app at a real inference gateway is a pure configuration change.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.ports.outbound.chat import ChatMessageRepositoryPort

logger = logging.getLogger(__name__)

# Some gateways do not require a key at all; an empty explicit key keeps the
# OpenAI client happy while the real one is optional.
_NO_AUTH_PLACEHOLDER = "not-needed"


class LlmSummarizationError(RuntimeError):
    """The configured LLM could not produce a summary."""


def _llm_credentials() -> str:
    """Reuse the shared inference-gateway key when one is configured."""

    return (settings.AI_INFERENCE_API_KEY or "").strip() or _NO_AUTH_PLACEHOLDER


class MessageExtractor:
    """Extract local query text and summarize query patterns with LangChain."""

    def __init__(
        self,
        message_repository: ChatMessageRepositoryPort,
        llm_base_url: Optional[str] = None,
        llm_model_name: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        self._messages = message_repository
        self.llm_base_url = llm_base_url or settings.QWEN3_6_35B_API_URL
        self.llm_model_name = llm_model_name or settings.QWEN3_6_35B_MODEL
        self.timeout = (
            float(timeout)
            if timeout is not None
            else float(settings.LANGCHAIN_CHAT_TIMEOUT_SEC)
        )

    async def extract_queries(
        self,
        user_id: str,
        conversation_id: str,
        limit: int = 20,
    ) -> List[str]:
        """Return user query texts from the local message repository."""

        queries = await self._messages.list_message_queries(
            user_id=user_id,
            conversation_id=conversation_id,
            limit=limit,
        )
        cleaned = [str(item).strip() for item in (queries or []) if str(item).strip()]
        logger.info(
            "Extracted %s queries from local conversation %s",
            len(cleaned),
            conversation_id,
        )
        return cleaned[:limit]

    async def summarize_queries_with_llm(
        self,
        queries: List[str],
        previous_summary: Optional[str] = None,
        llm_base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> str:
        """Summarize query patterns using the configured OpenAI-compatible LLM.

        Raises :class:`LlmSummarizationError` when the model call fails: a
        failed summary must never be persisted as if it were a real profile.
        """

        if not queries:
            logger.warning("No queries to summarize")
            raise LlmSummarizationError("没有可总结的提问记录")

        queries_text = "\n".join(
            f"{index + 1}. {query}" for index, query in enumerate(queries)
        )
        llm = ChatOpenAI(
            base_url=llm_base_url or self.llm_base_url,
            model=model_name or self.llm_model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=self.timeout,
            api_key=_llm_credentials(),
        )

        if previous_summary:
            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        """/nothink 你是一位专业的数据分析师。你的任务是基于用户之前的聊天习惯总结，结合新的提问记录，更新用户的提问习惯和需求总结。

                            请从以下几个方面进行分析：
                            1. 用户主要关注的话题和领域（对比之前是否有变化）
                            2. 提问的风格和特点（例如：简短直接、详细描述、偏好某种类型的问题等）
                            3. 可能的业务需求或使用场景
                            4. 新的模式或趋势

                            请用简洁的中文总结，控制在150字以内。注意要整合之前的总结和新的提问记录。""",
                    ),
                    (
                        "human",
                        """用户之前的聊天习惯总结：
{previous_summary}

以下是用户最新的{count}条提问记录：
{queries}

请基于之前的总结和新的提问记录，生成更新后的用户聊天习惯总结。""",
                    ),
                ]
            )
            payload = {
                "previous_summary": previous_summary,
                "count": len(queries),
                "queries": queries_text,
            }
            logger.info(
                "Updating summary with %s new queries and previous summary",
                len(queries),
            )
        else:
            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        """/nothink 你是一位专业的数据分析师。你的任务是分析用户的提问记录，总结他们的提问习惯和需求。

                            请从以下几个方面进行分析：
                            1. 用户主要关注的话题和领域
                            2. 提问的风格和特点（例如：简短直接、详细描述、偏好某种类型的问题等）
                            3. 可能的业务需求或使用场景
                            4. 其他值得注意的模式

                            请用简洁的中文总结，控制在150字以内。""",
                    ),
                    (
                        "human",
                        """以下是用户的{count}条提问记录：

{queries}

请分析并总结用户的提问习惯和需求。""",
                    ),
                ]
            )
            payload = {"count": len(queries), "queries": queries_text}
            logger.info("Creating new summary from %s queries", len(queries))

        try:
            summary = await (prompt | llm | StrOutputParser()).ainvoke(payload)
        except Exception as exc:  # noqa: BLE001 - surfaced as a 502 to the caller
            logger.error("Failed to summarize queries with LLM: %s", exc)
            raise LlmSummarizationError(
                f"用户画像摘要生成失败（LLM {self.llm_base_url}）：{exc}"
            ) from exc

        logger.info("Successfully generated summary")
        return summary


__all__ = ["MessageExtractor", "LlmSummarizationError"]
