"""
Chat Summary API Endpoints
Provides endpoints for managing user chat summaries
"""
from fastapi import APIRouter, Depends, HTTPException
import logging
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.web.base import (
    ChatSummaryRequest,
    ChatSummaryResponse,
    UserSummaryResponse,
    FormatJSONResponse
)
from app.adapters.chat_summary import (
    MessageExtractorChatArchiveAdapter,
    SqlAlchemyUserLookupAdapter,
    UserProfileSummaryRepoAdapter,
)
from app.core.config import settings
from app.core.dependencies import get_async_db
from app.core.security import get_current_user
from app.ports.contracts.identity import CurrentUserPort
from app.usecases.chat_summary.create_chat_summary import (
    CreateChatSummaryCommand,
    CreateChatSummaryUseCase,
)
from app.usecases.chat_summary.query_user_summary import (
    QueryUserSummaryQuery,
    QueryUserSummaryUseCase,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/create", response_model=ChatSummaryResponse)
async def create_chat_summary(
    request: ChatSummaryRequest,
    current_user: CurrentUserPort = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Create or update user chat summary

    This endpoint:
    1. Extracts queries from the specified conversation
    2. Gets user's previous summary (if exists)
    3. Generates new summary using LLM
    4. Updates database with new summary

    Args:
        request: ChatSummaryRequest containing user_id, conversation_id, and optional limit

    Returns:
        ChatSummaryResponse with operation results

    Example:
        POST /api/v1/chat-summary/create
        {
            "user_id": "abc-123",
            "conversation_id": "cd78daf6-f9e4-4463-9ff2-54257230a0ce",
            "limit": 20
        }
    """
    try:
        user_lookup = SqlAlchemyUserLookupAdapter(db)
        chat_archive = MessageExtractorChatArchiveAdapter(api_key=settings.CHAT_API_KEY)
        usecase = CreateChatSummaryUseCase(user_lookup=user_lookup, chat_archive=chat_archive)

        cmd = CreateChatSummaryCommand(
            user_id=request.user_id,
            conversation_id=request.conversation_id,
            limit=request.limit or 20,
            current_user=current_user,
        )

        logger.info(f"Creating chat summary for user {request.user_id}, conversation {request.conversation_id}")
        result = await usecase.execute(cmd)

        response_data = {
            "user_id": result.user_id,
            "conversation_id": result.conversation_id,
            "query_count": result.query_count,
            "previous_summary": result.previous_summary,
            "new_summary": result.new_summary,
            "is_first_time": result.is_first_time,
            "db_updated": result.db_updated,
        }

        if not result.db_updated:
            logger.warning(f"Failed to update database for user {request.user_id}")

        return FormatJSONResponse(
            data=response_data,
            message="Chat summary created successfully" if result.db_updated else "Summary generated but database update failed"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create chat summary: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create chat summary")


@router.get("/query/{user_id}", response_model=UserSummaryResponse)
async def query_user_summary(
    user_id: str,
    current_user: CurrentUserPort = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Query user's latest chat summary

    This endpoint retrieves the latest summary for a specific user from the database.

    Args:
        user_id: User identifier

    Returns:
        UserSummaryResponse with user's latest summary

    Example:
        GET /api/v1/chat-summary/query/abc-123
    """
    try:
        user_lookup = SqlAlchemyUserLookupAdapter(db)
        summary_repo = UserProfileSummaryRepoAdapter()
        usecase = QueryUserSummaryUseCase(user_lookup=user_lookup, summary_repo=summary_repo)

        query = QueryUserSummaryQuery(user_id=user_id, current_user=current_user)

        logger.info(f"Querying summary for user {user_id}")
        result = await usecase.execute(query)

        response_data = {
            "user_id": result.user_id,
            "latest_summary": result.latest_summary,
            "exists": result.exists,
        }

        return FormatJSONResponse(
            data=response_data,
            message="User summary found" if result.exists else "No summary found for this user"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to query user summary: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to query user summary")
