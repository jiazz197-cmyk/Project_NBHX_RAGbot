"""FastAPI 依赖（数据库会话、RAG 实例等）。"""
from collections.abc import AsyncGenerator

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger

logger = get_logger("dependencies")


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """请求级 AsyncSession：正常结束 commit；HTTPException 与其它异常 rollback。"""
    async with AsyncSessionLocal() as db:
        try:
            yield db
            await db.commit()
        except HTTPException as e:
            await db.rollback()
            logger.warning(
                f"异步数据库操作触发HTTP异常,已回滚事务: {e.status_code}: {e.detail}"
            )
            raise
        except Exception as e:
            await db.rollback()
            logger.error(f"异步数据库操作异常,已回滚事务: {e}", exc_info=True)
            raise


def get_rag_instance(request: Request):
    """从 app.state.rag 取 RAG；未初始化则 503。"""
    rag = getattr(request.app.state, "rag", None)
    if rag is None:
        raise HTTPException(status_code=503, detail="RAG service unavailable")
    return rag


def forbid_in_production() -> None:
    """Hide dev/demo routes in production (return 404; do not reveal existence)."""
    env = str(getattr(settings, "ENVIRONMENT", "") or "").strip().lower()
    if env in ("production", "prod"):
        raise HTTPException(status_code=404, detail="Not found")
