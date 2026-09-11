"""
示例路由模块
演示标准的 API 路由设计模式
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import forbid_in_production, get_async_db
from app.core.logging import get_logger

router = APIRouter(dependencies=[Depends(forbid_in_production)])
logger = get_logger("example")


# ==================== 响应模型 ====================
class HelloResponse(BaseModel):
    """Hello 响应模型"""
    message: str
    code: int = 200


class ItemResponse(BaseModel):
    """通用响应模型示例"""
    id: int
    name: str
    description: str | None = None


# ==================== 路由定义 ====================
@router.get("/hello", response_model=HelloResponse, summary="Hello Yamato")
async def hello_yamato():
    """
    返回 Hello Yamato 问候语。
    
    这是一个最简单的 GET 请求示例。
    """
    logger.info("Hello Yamato endpoint called")
    return HelloResponse(message="Hello Yamato")


@router.get("/hello/{name}", response_model=HelloResponse, summary="个性化问候")
async def hello_name(name: str):
    """
    返回个性化问候语。
    
    - **name**: 要问候的名称
    """
    logger.info(f"Hello endpoint called with name: {name}")
    return HelloResponse(message=f"Hello {name}")


@router.get("/db-example", response_model=HelloResponse, summary="数据库连接示例")
async def db_example(db: AsyncSession = Depends(get_async_db)):
    """
    演示如何注入数据库会话。
    
    实际业务中可以使用 db 进行数据库操作。
    """
    logger.info("Database example endpoint called")
    # 示例：db.query(Model).all()
    return HelloResponse(message="Hello Yamato with DB connection")
