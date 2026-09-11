import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.adapters.context_compression import IntegrationContextCompressorAdapter
from app.core.exceptions import APIException, ExternalServiceError
from app.core.security import get_current_user
from app.core.validators.conversation_id import validate_conversation_id
from app.ports.contracts.identity import CurrentUserPort
from app.adapters.web.base import FormatJSONResponse
from app.usecases.context_compression.compress import CompressContextCommand, CompressContextUseCase

logger = logging.getLogger(__name__)
router = APIRouter()


class ContextCompressionRequest(BaseModel):
    user_id: str = Field(
        ..., min_length=1, max_length=512, description="User ID for fetching conversation"
    )
    conversation_id: str = Field(
        ..., min_length=1, max_length=128, description="Dify conversation ID"
    )
    n_recent: int = Field(
        5, ge=1, le=100, description="Number of recent dialogue turns to keep"
    )

    @field_validator("conversation_id")
    @classmethod
    def conversation_id_path_safe(cls, v: str) -> str:
        return validate_conversation_id(v)


@router.post("/compress")
async def compress_chat_context(
    request: ContextCompressionRequest,
    current_user: CurrentUserPort = Depends(get_current_user),
):
    """
    Compress chat context based on Dify conversation ID.
    """
    try:
        result = await CompressContextUseCase(IntegrationContextCompressorAdapter()).execute(
            CompressContextCommand(
                user_id=request.user_id,
                conversation_id=request.conversation_id,
                n_recent=request.n_recent,
                current_user=current_user,
            )
        )
        return FormatJSONResponse(
            data={"compressed_context": result.compressed},
            message="Context compressed successfully",
        )
    except ExternalServiceError as e:
        logger.warning(
            "Context compression misconfigured for conversation %s: %s",
            request.conversation_id,
            str(e)[:500],
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=e.message) from e
    except HTTPException:
        raise
    except APIException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(
            "Failed to compress context for conversation %s: %s",
            request.conversation_id,
            str(e)[:2000],
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to compress context",
        ) from e
