"""Local chat message archive and LangChain-based user query summarization.

Message text comes exclusively from the local
:class:`app.ports.outbound.chat.ChatMessageRepositoryPort`.  This module no
longer performs any external chat-service HTTP calls; the only remaining
external dependency is the configured OpenAI-compatible LLM used for
summarization.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import psycopg2
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from psycopg2.extras import RealDictCursor

from app.core.async_bridge import run_async
from app.core.config import settings
from app.core.time_utils import utcnow
from app.ports.outbound.chat import ChatMessageRepositoryPort

logger = logging.getLogger(__name__)

_DEFAULT_LLM_BASE_URL = "http://localhost:80/llm/qwen8b/v1"
_DEFAULT_LLM_MODEL = "Qwen/Qwen3-8B-FP8"
_LOCAL_LLM_API_KEY = "not-needed"


class UserProfileDB:
    """Manage user chat profile database operations."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        database: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self.connection_params = {
            "host": host or settings.POSTGRES_SERVER,
            "port": port or settings.POSTGRES_PORT,
            "database": database or settings.POSTGRES_DB,
            "user": user or settings.POSTGRES_USER,
            "password": password or settings.POSTGRES_PASSWORD,
        }

    def get_connection(self):
        """Get a new database connection."""
        try:
            return psycopg2.connect(**self.connection_params)
        except psycopg2.Error as exc:
            logger.error("Failed to connect to database: %s", exc)
            raise

    def ensure_profile_table(self, conn) -> None:
        """Create the profile table when missing (development bootstrap)."""
        with conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS user_chat_profile (
                    user_id VARCHAR(128) PRIMARY KEY,
                    latest_summary TEXT,
                    update_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        conn.commit()

    def get_latest_summary(self, user_id: str) -> Optional[str]:
        """Return the latest summary for a user, or ``None``."""
        conn = None
        try:
            conn = self.get_connection()
            self.ensure_profile_table(conn)
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT latest_summary FROM user_chat_profile WHERE user_id = %s",
                    (user_id,),
                )
                result = cursor.fetchone()

                if result:
                    logger.info("Found existing summary for user %s", user_id)
                    return result["latest_summary"]

                logger.info("No existing summary for user %s", user_id)
                return None
        except psycopg2.Error as exc:
            logger.error("Failed to get latest summary for user %s: %s", user_id, exc)
            return None
        finally:
            if conn:
                conn.close()

    def upsert_latest_summary(self, user_id: str, latest_summary: str) -> bool:
        """Insert or update the latest summary for a user."""
        conn = None
        try:
            conn = self.get_connection()
            self.ensure_profile_table(conn)
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO user_chat_profile (user_id, latest_summary, update_time)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id)
                    DO UPDATE SET
                        latest_summary = EXCLUDED.latest_summary,
                        update_time = EXCLUDED.update_time
                    """,
                    (user_id, latest_summary, utcnow()),
                )
                conn.commit()
                logger.info("Successfully updated summary for user %s", user_id)
                return True
        except psycopg2.Error as exc:
            logger.error("Failed to upsert summary for user %s: %s", user_id, exc)
            if conn:
                conn.rollback()
            return False
        finally:
            if conn:
                conn.close()


class MessageExtractor:
    """Extract local query text and summarize query patterns with LangChain."""

    def __init__(
        self,
        message_repository: ChatMessageRepositoryPort,
        llm_base_url: str = _DEFAULT_LLM_BASE_URL,
        llm_model_name: str = _DEFAULT_LLM_MODEL,
        timeout: int = 30,
    ):
        self._messages = message_repository
        self.llm_base_url = llm_base_url
        self.llm_model_name = llm_model_name
        self.timeout = timeout

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
        """Summarize query patterns using the configured OpenAI-compatible LLM."""

        if not queries:
            logger.warning("No queries to summarize")
            return "No queries available for analysis."

        try:
            llm = ChatOpenAI(
                base_url=llm_base_url or self.llm_base_url,
                model=model_name or self.llm_model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                api_key=_LOCAL_LLM_API_KEY,
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
                queries_text = "\n".join(
                    f"{index + 1}. {query}" for index, query in enumerate(queries)
                )
                logger.info(
                    "Updating summary with %s new queries and previous summary",
                    len(queries),
                )
                summary = await (prompt | llm | StrOutputParser()).ainvoke(
                    {
                        "previous_summary": previous_summary,
                        "count": len(queries),
                        "queries": queries_text,
                    }
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
                queries_text = "\n".join(
                    f"{index + 1}. {query}" for index, query in enumerate(queries)
                )
                logger.info("Creating new summary from %s queries", len(queries))
                summary = await (prompt | llm | StrOutputParser()).ainvoke(
                    {
                        "count": len(queries),
                        "queries": queries_text,
                    }
                )

            logger.info("Successfully generated summary")
            return summary
        except Exception as exc:  # noqa: BLE001 - keep existing graceful degradation
            logger.error("Failed to summarize queries with LLM: %s", exc)
            return f"Error generating summary: {exc}"


async def async_summarize_user_queries(
    message_repository: ChatMessageRepositoryPort,
    user_id: str,
    conversation_id: str,
    limit: int = 20,
    llm_base_url: str = _DEFAULT_LLM_BASE_URL,
    llm_model_name: str = _DEFAULT_LLM_MODEL,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Extract local queries and generate a summary."""

    extractor = MessageExtractor(
        message_repository,
        llm_base_url=llm_base_url,
        llm_model_name=llm_model_name,
        timeout=timeout,
    )
    queries = await extractor.extract_queries(user_id, conversation_id, limit)
    summary = await extractor.summarize_queries_with_llm(queries)
    return {
        "queries": queries,
        "summary": summary,
        "query_count": len(queries),
    }


def summarize_user_queries(
    message_repository: ChatMessageRepositoryPort,
    user_id: str,
    conversation_id: str,
    limit: int = 20,
    llm_base_url: str = _DEFAULT_LLM_BASE_URL,
    llm_model_name: str = _DEFAULT_LLM_MODEL,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Synchronous wrapper for background/script use."""

    return run_async(
        async_summarize_user_queries(
            message_repository,
            user_id,
            conversation_id,
            limit,
            llm_base_url,
            llm_model_name,
            timeout,
        )
    )


async def update_user_profile_with_new_queries(
    message_repository: ChatMessageRepositoryPort,
    user_id: str,
    conversation_id: str,
    db_config: Optional[Dict[str, Any]] = None,
    limit: int = 20,
    llm_base_url: str = _DEFAULT_LLM_BASE_URL,
    llm_model_name: str = _DEFAULT_LLM_MODEL,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Get previous summary, extract local queries, summarize, and persist."""

    if db_config is None:
        db = UserProfileDB()
    else:
        db = UserProfileDB(**db_config)

    extractor = MessageExtractor(
        message_repository,
        llm_base_url=llm_base_url,
        llm_model_name=llm_model_name,
        timeout=timeout,
    )

    result: Dict[str, Any] = {
        "user_id": user_id,
        "conversation_id": conversation_id,
        "queries": [],
        "query_count": 0,
        "previous_summary": None,
        "new_summary": None,
        "db_updated": False,
        "is_first_time": False,
    }

    logger.info("Step 1: Getting previous summary for user %s", user_id)
    previous_summary = db.get_latest_summary(user_id)
    result["previous_summary"] = previous_summary
    if previous_summary is None:
        result["is_first_time"] = True
        logger.info("First time user: %s", user_id)

    logger.info("Step 2: Extracting local queries from conversation %s", conversation_id)
    queries = await extractor.extract_queries(user_id, conversation_id, limit)
    result["queries"] = queries
    result["query_count"] = len(queries)

    if not queries:
        logger.warning("No queries extracted, skipping summary generation")
        return result

    logger.info(
        "Step 3: Generating new summary (with previous: %s)",
        previous_summary is not None,
    )
    new_summary = await extractor.summarize_queries_with_llm(
        queries=queries,
        previous_summary=previous_summary,
    )
    result["new_summary"] = new_summary

    logger.info("Step 4: Updating database for user %s", user_id)
    result["db_updated"] = db.upsert_latest_summary(user_id, new_summary)
    if result["db_updated"]:
        logger.info("Successfully updated user profile for %s", user_id)
    else:
        logger.error("Failed to update database for user %s", user_id)

    return result
