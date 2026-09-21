"""异步 Redis：通用 KV、API 缓存键、简易限流、任务状态等。"""
import hashlib
import json
from typing import Any, Optional, Dict

import redis.asyncio as redis

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time_utils import utcnow_naive

logger = get_logger("cache")


class RedisKVStore:
    """通用 Redis KV 封装：set/get/delete/keys，含 JSON 序列化与容错。

    被 :class:`AsyncRedisManager`（全局单例，``from_url`` 自建 client）与
    ``TaskManager.create_thread_safe_instance``（每线程独立 client，事件循环绑定）
    共用，消除两处重复的 set/get/delete/keys 实现。
    """

    def __init__(self, redis_client):
        self.redis_client = redis_client

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            if ttl:
                return await self.redis_client.setex(key, ttl, value)
            return await self.redis_client.set(key, value)
        except Exception as e:
            logger.error(f"Error setting cache key {key}: {e}")
            return False

    async def get(self, key: str) -> Optional[Any]:
        try:
            value = await self.redis_client.get(key)
            if value is None:
                return None
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        except Exception as e:
            logger.error(f"Error getting cache key {key}: {e}")
            return None

    async def delete(self, key: str) -> bool:
        try:
            return bool(await self.redis_client.delete(key))
        except Exception as e:
            logger.error(f"Error deleting cache key {key}: {e}")
            return False

    async def incr(self, key: str, amount: int = 1) -> Optional[int]:
        """自增并返回新值；异常返回 ``None``（调用方按「Redis 不可用」处理）。

        issue #21：检索缓存的集合版本号靠它 bump。调用方需保证**不给版本键设 TTL**
        ——版本号一旦过期归零，失效前的旧缓存条目可能被重新命中。
        """
        try:
            return int(await self.redis_client.incrby(key, int(amount)))
        except Exception as e:
            logger.error(f"Error incrementing cache key {key}: {e}")
            return None

    async def keys(self, pattern: str) -> list:
        result = []
        try:
            async for key in self.redis_client.scan_iter(match=pattern):
                if isinstance(key, bytes):
                    key = key.decode('utf-8')
                result.append(key)
        except Exception as e:
            logger.error(f"Error listing keys with pattern {pattern}: {e}")
        return result


class AsyncRedisManager(RedisKVStore):
    """单例连接池；decode_responses=True。"""
    _instance = None
    _initialized = False
    
    def __new__(cls):
        """单例。"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """仅首次 from_url。"""
        if self._initialized:
            return
        
        kwargs = {
            "max_connections": settings.REDIS_MAX_CONNECTIONS,
            "decode_responses": True,
            # 出借前校验：空闲超过该秒数的连接借出前自动 PING，失败即换（对齐 PG
            # pool_pre_ping）。0 = 关闭。redis-py 由 ConnectionPool 周期性触发。
            "health_check_interval": settings.REDIS_HEALTH_CHECK_INTERVAL_SEC,
        }
        if settings.REDIS_PASSWORD:
            kwargs["password"] = settings.REDIS_PASSWORD
        self.redis_client = redis.from_url(settings.REDIS_URL, **kwargs)
        
        self.__class__._initialized = True

    async def _test_connection(self):
        try:
            await self.redis_client.ping()
            logger.info("Redis connection established successfully")
        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise

    async def test_connection(self):
        """ping。"""
        return await self._test_connection()

    async def close(self):
        """断开连接池并 aclose 客户端。"""
        try:
            logger.info("正在关闭 Redis 连接...")
            if hasattr(self.redis_client, 'connection_pool'):
                pool = self.redis_client.connection_pool
                await pool.disconnect()
                logger.info("[success] Redis 连接池已断开")
            
            await self.redis_client.aclose()
            logger.info("[success] Redis 客户端已关闭")
        except Exception as e:
            logger.error(f"关闭 Redis 连接时出错: {e}", exc_info=True)

    async def delete_pattern(self, pattern: str) -> int:
        deleted = 0
        try:
            async for key in self.redis_client.scan_iter(match=pattern):
                deleted += await self.redis_client.delete(key)
            return deleted
        except Exception as e:
            logger.error(f"Error deleting keys with pattern {pattern}: {e}")
            return deleted

    async def exists(self, key: str) -> bool:
        try:
            return bool(await self.redis_client.exists(key))
        except Exception as e:
            logger.error(f"Error checking existence of key {key}: {e}")
            return False

    async def expire(self, key: str, ttl: int) -> bool:
        try:
            return bool(await self.redis_client.expire(key, ttl))
        except Exception as e:
            logger.error(f"Error setting expiration for key {key}: {e}")
            return False

    async def cache_api_response(self, endpoint: str, params: Dict[str, Any], response_data: Any,
                                 ttl: Optional[int] = None) -> bool:
        cache_key = self._generate_api_cache_key(endpoint, params)
        ttl = ttl or settings.CACHE_API_RESPONSE_TTL
        return await self.set(cache_key, response_data, ttl)

    async def get_cached_api_response(self, endpoint: str, params: Dict[str, Any]) -> Optional[Any]:
        cache_key = self._generate_api_cache_key(endpoint, params)
        return await self.get(cache_key)

    async def invalidate_api_cache(self, endpoint: str) -> int:
        pattern = f"api_cache:{endpoint}:*"
        return await self.delete_pattern(pattern)

    def _generate_api_cache_key(self, endpoint: str, params: Dict[str, Any]) -> str:
        params_str = json.dumps(params, sort_keys=True, ensure_ascii=False)
        params_hash = hashlib.md5(params_str.encode()).hexdigest()
        return f"api_cache:{endpoint}:{params_hash}"

    async def set_job_status(self, job_id: str, status: str, progress: int = 0,
                             metadata: Optional[Dict[str, Any]] = None, ttl: Optional[int] = None) -> bool:
        job_key = f"job_status:{job_id}"
        job_data = {
            "status": status,
            "progress": progress,
            "updated_at": utcnow_naive().isoformat(),
            "metadata": metadata or {}
        }
        ttl = ttl or settings.CACHE_JOB_STATUS_TTL
        return await self.set(job_key, job_data, ttl)

    async def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        job_key = f"job_status:{job_id}"
        return await self.get(job_key)

    async def delete_job_status(self, job_id: str) -> bool:
        job_key = f"job_status:{job_id}"
        return await self.delete(job_key)

    async def cache_data_source_overview(self, source_id: int, overview_data: Dict[str, Any], ttl: int = 300) -> bool:
        cache_key = f"ds_overview:{source_id}"
        return await self.set(cache_key, overview_data, ttl)

    async def get_cached_data_source_overview(self, source_id: int) -> Optional[Dict[str, Any]]:
        cache_key = f"ds_overview:{source_id}"
        return await self.get(cache_key)

    async def invalidate_data_source_cache(self, source_id: int) -> bool:
        cache_key = f"ds_overview:{source_id}"
        return await self.delete(cache_key)

    async def set_upload_progress(self, file_id: str, total_size: int, uploaded_size: int, status: str = "uploading",
                                  ttl: int = 3600) -> bool:
        progress_key = f"upload_progress:{file_id}"
        progress_data = {
            "total_size": total_size,
            "uploaded_size": uploaded_size,
            "progress": int((uploaded_size / total_size) * 100) if total_size > 0 else 0,
            "status": status,
            "updated_at": utcnow_naive().isoformat()
        }
        return await self.set(progress_key, progress_data, ttl)

    async def get_upload_progress(self, file_id: str) -> Optional[Dict[str, Any]]:
        progress_key = f"upload_progress:{file_id}"
        return await self.get(progress_key)

    async def delete_upload_progress(self, file_id: str) -> bool:
        progress_key = f"upload_progress:{file_id}"
        return await self.delete(progress_key)

    async def get_redis_stats(self) -> Dict[str, Any]:
        try:
            info = await self.redis_client.info()

            async def _count_keys(match: str) -> int:
                count = 0
                async for _ in self.redis_client.scan_iter(match=match):
                    count += 1
                return count

            cache_stats = {
                "api_cache_keys": await _count_keys("api_cache:*"),
                "rate_limit_keys": await _count_keys("rate_limit:*"),
                "job_status_keys": await _count_keys("job_status:*"),
                "upload_progress_keys": await _count_keys("upload_progress:*"),
                "data_source_keys": await _count_keys("ds_overview:*")
            }
            return {
                "redis_info": {
                    "used_memory": info.get("used_memory"),
                    "used_memory_human": info.get("used_memory_human"),
                    "connected_clients": info.get("connected_clients"),
                    "total_commands_processed": info.get("total_commands_processed"),
                    "keyspace_hits": info.get("keyspace_hits"),
                    "keyspace_misses": info.get("keyspace_misses")
                },
                "cache_stats": cache_stats
            }
        except Exception as e:
            logger.error(f"Error getting Redis stats: {e}")
            return {}


redis_manager = AsyncRedisManager()
