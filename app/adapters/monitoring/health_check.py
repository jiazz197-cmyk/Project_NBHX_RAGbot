"""
系统健康检查服务
提供 Redis、PostgreSQL 等基础服务的健康状态检测
"""
import asyncio
import logging
from typing import Dict, Any

from app.core.cache import redis_manager
from app.core.database import check_db_connection_async
from app.core.logging import get_logger
from app.core.time_utils import utcnow

logger = get_logger("monitoring.health_check")


class HealthCheckService:
    """
    系统健康检查服务（单例模式）
    
    特性：
    - 并行检查各服务，减少总耗时
    - 每次检查使用独立的数据库会话
    - 超时控制，避免单个服务阻塞整体响应
    - [success] 单例模式确保全局唯一实例
    """
    _instance = None
    _initialized = False
    
    def __new__(cls, timeout: float = 2.0):
        """确保只创建一个 HealthCheckService 实例"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, timeout: float = 2.0):
        """
        初始化（只执行一次）
        
        Args:
            timeout: 单个服务检查的超时时间（秒）
        """
        if self._initialized:
            return
        
        self.check_timeout = timeout
        self.__class__._initialized = True
    
    async def check_all_services(self) -> Dict[str, Dict[str, Any]]:
        """
        并行检查所有服务的健康状态
        
        Returns:
            各服务的健康状态字典
        """
        # 并行执行所有检查
        redis_task = asyncio.create_task(self._check_redis_async())
        pg_task = asyncio.create_task(self._check_postgresql_async())
        
        results = {}
        
        # 等待所有检查完成
        redis_result, pg_result = await asyncio.gather(
            redis_task, pg_task, return_exceptions=True
        )
        
        # 处理 Redis 结果
        if isinstance(redis_result, Exception):
            results["redis"] = self._error_result(str(redis_result))
        else:
            results["redis"] = redis_result
        
        # 处理 PostgreSQL 结果
        if isinstance(pg_result, Exception):
            results["postgresql"] = self._error_result(str(pg_result))
        else:
            results["postgresql"] = pg_result
        
        return results
    
    def _success_result(self, latency_ms: float = None) -> Dict[str, Any]:
        """构造成功响应"""
        result = {
            "status": "healthy",
            "timestamp": utcnow().isoformat()
        }
        if latency_ms is not None:
            result["latency_ms"] = round(latency_ms, 2)
        return result
    
    def _error_result(self, error: str) -> Dict[str, Any]:
        """构造错误响应"""
        return {
            "status": "unhealthy",
            "error": error,
            "timestamp": utcnow().isoformat()
        }
    
    async def _check_redis_async(self) -> Dict[str, Any]:
        """异步检查 Redis 连接（带超时）"""
        try:
            start = utcnow()
            # 使用 asyncio.wait_for 添加超时控制
            await asyncio.wait_for(
                redis_manager.redis_client.ping(),
                timeout=self.check_timeout
            )
            latency = (utcnow() - start).total_seconds() * 1000
            return self._success_result(latency)
        except asyncio.TimeoutError:
            logger.warning(f"Redis 健康检查超时 (>{self.check_timeout}s)")
            return self._error_result(f"timeout after {self.check_timeout}s")
        except Exception as e:
            logger.error(f"Redis 健康检查失败: {e}")
            return self._error_result(str(e))
    
    async def _check_postgresql_async(self) -> Dict[str, Any]:
        """异步检查 PostgreSQL 连接（带超时）"""
        try:
            start = utcnow()
            ok = await asyncio.wait_for(
                check_db_connection_async(),
                timeout=self.check_timeout,
            )
            latency = (utcnow() - start).total_seconds() * 1000
            if ok:
                return self._success_result(latency)
            return self._error_result("connection check failed")
        except asyncio.TimeoutError:
            logger.warning(f"PostgreSQL 健康检查超时 (>{self.check_timeout}s)")
            return self._error_result(f"timeout after {self.check_timeout}s")
        except Exception as e:
            logger.error(f"PostgreSQL 健康检查失败: {e}")
            return self._error_result(str(e))


# 全局健康检查实例（2秒超时）
health_service = HealthCheckService(timeout=2.0) 