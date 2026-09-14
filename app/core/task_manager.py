"""
异步任务管理器
用于管理长时间运行的后台任务的状态和结果
支持 Redis 存储和内存存储两种模式

🆕 新特性：
- 线程安全的异步操作辅助方法
- 与 ExecutorManager 协作支持
- [info] 观察者模式支持（可选，不影响原有轮询机制）
"""

import asyncio
import json
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional, Literal, List

from app.core.time_utils import utcnow, utcnow_naive

from app.core.logging import get_logger

logger = get_logger("task_manager")
from app.core.observer import TaskSubject, TaskObserver, TaskEvent, TaskEventType


@dataclass
class TaskStatus:
    """任务状态数据类"""
    task_id: str
    task_type: str
    status: str
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    progress: int = 0
    message: str = ""
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class MemoryTaskStorage:
    """内存任务存储（Redis 不可用时的备用方案）"""
    
    def __init__(self):
        self._tasks: Dict[str, Any] = {}
        self._expires_at: Dict[str, float] = {}
        self.default_ttl = 3600 * 24  # 24小时

    def _prune_expired(self) -> None:
        now = time.time()
        expired_keys = [key for key, expires_at in self._expires_at.items() if expires_at <= now]
        for key in expired_keys:
            self._tasks.pop(key, None)
            self._expires_at.pop(key, None)
    
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """设置任务数据"""
        try:
            self._prune_expired()
            self._tasks[key] = value
            expire_after = self.default_ttl if ttl is None else ttl
            if expire_after and expire_after > 0:
                self._expires_at[key] = time.time() + float(expire_after)
            else:
                self._expires_at.pop(key, None)
            return True
        except Exception as e:
            logger.error(f"内存存储设置失败 {key}: {e}")
            return False
    
    async def get(self, key: str) -> Optional[Any]:
        """获取任务数据"""
        try:
            self._prune_expired()
            return self._tasks.get(key)
        except Exception as e:
            logger.error(f"内存存储获取失败 {key}: {e}")
            return None
    
    async def delete(self, key: str) -> bool:
        """删除任务数据"""
        try:
            self._prune_expired()
            if key in self._tasks:
                del self._tasks[key]
                self._expires_at.pop(key, None)
                return True
            return False
        except Exception as e:
            logger.error(f"内存存储删除失败 {key}: {e}")
            return False
    
    async def keys(self, pattern: str) -> list:
        """获取匹配的键列表"""
        try:
            self._prune_expired()
            # 简单的前缀匹配
            prefix = pattern.replace("*", "")
            return [key for key in self._tasks.keys() if key.startswith(prefix)]
        except Exception as e:
            logger.error(f"内存存储键列表获取失败: {e}")
            return []


class TaskManager:
    """
    异步任务管理器（单例模式）
    
    职责：
    - 管理任务状态（pending/running/completed/failed）
    - 存储任务元数据和结果
    - 提供任务查询接口
    - 🆕 提供线程安全的异步操作辅助方法
    - [info] 支持观察者模式（事件驱动通知）
    
    注意：
    - 仅负责状态管理，不负责任务执行
    - 不管理线程池或进程池
    - 任务的实际执行由调用方负责（通常是 ExecutorManager）
    - 🆕 支持在后台线程中调用异步方法
    - [success] 全局单例，确保任务状态统一管理
    - [info] 观察者模式与轮询机制并存，互不影响
    """
    _instance = None
    _initialized = False
    
    def __new__(cls):
        """确保只创建一个 TaskManager 实例（主实例）"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """初始化（只执行一次）"""
        if self._initialized:
            # 确保观察者相关属性存在（兼容旧实例）
            if not hasattr(self, '_subject'):
                self._subject = TaskSubject()
            if not hasattr(self, '_observer_enabled'):
                self._observer_enabled = True
            return
        
        self.task_prefix = "task:"
        self.default_ttl = 3600 * 24  # 24小时
        
        # 初始化为内存存储，稍后可能升级为 Redis
        self.storage = MemoryTaskStorage()
        self.storage_type = "memory"
        self._init_attempted = False
        
        # 🆕 线程本地存储（用于在后台线程中创建独立的事件循环）
        self._thread_local = threading.local()
        
        # [note] 观察者模式支持（可选特性）
        self._subject = TaskSubject()
        self._observer_enabled = True  # 全局开关
        # 主事件循环桥接（用于线程任务回投事件到主循环）
        self._notify_loop = None
        self._bridge_subject = self._subject

        # 尝试初始化存储
        self._try_init_storage()
        
        self.__class__._initialized = True

    def _try_init_storage(self):
        """尝试初始化存储（优先 Redis，降级到内存）"""
        if self._init_attempted:
            return

        self._init_attempted = True

        # # [warning] 临时禁用 Redis，直接使用内存存储
        # # 原因：当前 Redis (107.174.1.76:6379) 是只读副本，无法写入
        # logger.warning("[warning] 检测到 Redis 只读副本，自动降级到内存存储")
        # logger.info("如需使用 Redis，请配置可写的 Redis 主节点")
        # self.storage = MemoryTaskStorage()
        # self.storage_type = "memory"
        # logger.info("[success] 任务管理器使用内存存储")
        # return

        # 待 Redis 配置修复后，取消下面代码的注释
        try:
            from app.core.cache import redis_manager
            if (hasattr(redis_manager, 'redis_client') and
                redis_manager.redis_client is not None):
                self.storage = redis_manager
                self.storage_type = "redis"
                logger.info("[success] 任务管理器使用 Redis 存储")
                return
        except Exception as e:
            logger.warning(f"无法初始化 Redis: {e}")
        
        logger.info("[success] 任务管理器使用内存存储")

    def generate_task_id(self, task_type: str) -> str:
        """生成任务ID"""
        timestamp = utcnow_naive().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        return f"{task_type}_{timestamp}_{uuid.uuid4().hex[:8]}"
    
    # ==================== 观察者模式接口 ====================
    
    async def register_observer(
        self, 
        observer: TaskObserver,
        event_types: Optional[List[TaskEventType]] = None
    ) -> None:
        """
        注册任务观察者
        
        Args:
            observer: 观察者实例
            event_types: 可选，只接收特定类型的事件（None = 接收所有事件）
        
        Example:
            ```python
            # 注册接收所有事件的观察者
            await task_manager.register_observer(MyObserver())

            # 注册只接收完成和失败事件的观察者
            await task_manager.register_observer(
                MyObserver(),
                event_types=[TaskEventType.TASK_COMPLETED, TaskEventType.TASK_FAILED]
            )
            ```
        """
        await self._subject.attach(observer, event_types)
        logger.info(f"[info] TaskManager 注册观察者: {observer.get_observer_name()}")
    
    async def unregister_observer(self, observer: TaskObserver) -> None:
        """
        注销任务观察者
        
        Args:
            observer: 要注销的观察者实例
        """
        await self._subject.detach(observer)
        logger.info(f"[info] TaskManager 注销观察者: {observer.get_observer_name()}")
    
    def enable_observers(self) -> None:
        """启用观察者通知（全局开关）"""
        self._observer_enabled = True
        logger.info("[info] TaskManager 观察者通知已启用")
    
    def disable_observers(self) -> None:
        """禁用观察者通知（全局开关）"""
        self._observer_enabled = False
        logger.info("[info] TaskManager 观察者通知已禁用")
    
    def get_observer_stats(self) -> Dict[str, Any]:
        """
        获取观察者统计信息
        
        Returns:
            包含观察者数量、事件数等信息的字典
        """
        stats = self._subject.get_stats()
        stats["observer_enabled"] = self._observer_enabled
        return stats

    def set_notify_loop(self, loop) -> None:
        """设置主事件循环，供线程任务回投观察者事件。"""
        self._notify_loop = loop
        self._bridge_subject = self._subject
    
    async def remove_all_observers(self) -> int:
        """
        移除所有观察者（用于关闭时清理）
        
        Returns:
            移除的观察者数量
        """
        count = await self._subject.detach_all()
        logger.info(f"[info] TaskManager 已移除所有观察者: {count} 个")
        return count
    
    async def _emit_event(
        self, 
        event_type: TaskEventType,
        task_id: str,
        task_type: str,
        **kwargs
    ) -> None:
        """
        发布任务事件（内部方法）
        
        Args:
            event_type: 事件类型
            task_id: 任务ID
            task_type: 任务类型
            **kwargs: 其他事件数据（status, progress, message, result, error 等）
        
        Note:
            - 如果观察者已禁用，此方法不会执行任何操作
            - 事件发布不会阻塞主流程（即使失败也不影响任务执行）
        """
        # 容错检查：确保观察者相关属性存在
        if not hasattr(self, '_observer_enabled') or not hasattr(self, '_subject'):
            return
        
        if not self._observer_enabled:
            return
        
        try:
            event = TaskEvent(
                event_type=event_type,
                task_id=task_id,
                task_type=task_type,
                **kwargs
            )
            # [debug] 调试日志：记录事件发布
            logger.debug(f"[event] 准备发布事件: {event_type.value} [task_id={task_id}]")

            notify_loop = getattr(self, "_notify_loop", None)
            bridge_subject = getattr(self, "_bridge_subject", self._subject)
            if notify_loop is not None:
                try:
                    current_loop = asyncio.get_running_loop()
                except RuntimeError:
                    current_loop = None

                if current_loop is not notify_loop:
                    # 在线程事件循环中，将通知回投到主事件循环，避免跨循环访问 WS/Lock。
                    asyncio.run_coroutine_threadsafe(
                        bridge_subject.notify(event),
                        notify_loop,
                    )
                    logger.debug(f"[success] 事件已桥接到主循环: {event_type.value} [task_id={task_id}]")
                    return

            await self._subject.notify(event)
            logger.debug(f"[success] 事件发布完成: {event_type.value} [task_id={task_id}]")
        except Exception as e:
            # 观察者通知失败不应影响任务执行
            logger.warning(f"[warning] 发布任务事件失败 [{event_type.value}]: {e}")

    async def create_task(self, task_type: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """创建新任务"""
        try:
            task_id = self.generate_task_id(task_type)

            task_status = TaskStatus(
                task_id=task_id,
                task_type=task_type,
                status="pending",
                created_at=utcnow().isoformat(),
                metadata=metadata or {}
            )

            success = await self._save_task_status(task_status)
            if not success:
                logger.error(f"创建任务失败: {task_id}")
                raise Exception(f"无法保存任务状态: {task_id}")

            # Lifecycle INFO is emitted by LoggingObserver via _emit_event.
            await self._emit_event(
                TaskEventType.TASK_CREATED,
                task_id=task_id,
                task_type=task_type,
                status="pending",
                metadata=metadata
            )
            
            return task_id

        except Exception as e:
            logger.error(f"创建任务时发生错误: {e}")
            raise

    async def start_task(self, task_id: str) -> bool:
        """标记任务为运行状态"""
        try:
            task_status = await self.get_task_status(task_id)
            if not task_status:
                logger.error(f"任务不存在: {task_id}")
                return False

            task_status.status = "running"
            task_status.started_at = utcnow().isoformat()
            task_status.progress = 0

            success = await self._save_task_status(task_status)
            if success:
                # Lifecycle INFO is emitted by LoggingObserver via _emit_event.
                await self._emit_event(
                    TaskEventType.TASK_STARTED,
                    task_id=task_id,
                    task_type=task_status.task_type,
                    status="running",
                    progress=0
                )
            
            return success

        except Exception as e:
            logger.error(f"启动任务时发生错误 {task_id}: {e}")
            return False

    async def update_task_progress(self, task_id: str, progress: int, message: str = "") -> bool:
        """更新任务进度"""
        try:
            task_status = await self.get_task_status(task_id)
            if not task_status:
                return False

            task_status.progress = max(0, min(100, progress))
            if message:
                task_status.message = message

            success = await self._save_task_status(task_status)
            
            if success:
                # [note] 发布进度更新事件
                await self._emit_event(
                    TaskEventType.TASK_PROGRESS_UPDATED,
                    task_id=task_id,
                    task_type=task_status.task_type,
                    status=task_status.status,
                    progress=task_status.progress,
                    message=message
                )
            
            return success

        except Exception as e:
            logger.error(f"更新任务进度时发生错误 {task_id}: {e}")
            return False

    async def update_task_message(self, task_id: str, message: str) -> bool:
        """Update task message only (preserves status and progress)."""
        try:
            task_status = await self.get_task_status(task_id)
            if not task_status:
                return False
            task_status.message = message
            return await self._save_task_status(task_status)
        except Exception as e:
            logger.error(f"更新任务消息时发生错误 {task_id}: {e}")
            return False

    async def update_status(self, task_id: str, status: str, message: str = "") -> bool:
        """Update task status string."""
        try:
            task_status = await self.get_task_status(task_id)
            if not task_status:
                return False

            task_status.status = status
            if message:
                task_status.message = message

            success = await self._save_task_status(task_status)
            if success:
                await self._emit_event(
                    TaskEventType.TASK_PROGRESS_UPDATED,
                    task_id=task_id,
                    task_type=task_status.task_type,
                    status=status,
                    progress=task_status.progress,
                    message=message or task_status.message,
                )
            return success
        except Exception as e:
            logger.error(f"更新任务状态时发生错误 {task_id}: {e}")
            return False

    async def complete_task(self, task_id: str, result: Dict[str, Any], message: str = "任务完成") -> bool:
        """标记任务为完成状态"""
        try:
            task_status = await self.get_task_status(task_id)
            if not task_status:
                return False

            task_status.status = "completed"
            task_status.completed_at = utcnow().isoformat()
            task_status.progress = 100
            task_status.message = message
            task_status.result = result

            success = await self._save_task_status(task_status)
            if success:
                await self._emit_event(
                    TaskEventType.TASK_COMPLETED,
                    task_id=task_id,
                    task_type=task_status.task_type,
                    status="completed",
                    progress=100,
                    message=message,
                    result=result
                )
            
            return success

        except Exception as e:
            logger.error(f"完成任务时发生错误 {task_id}: {e}")
            return False

    async def fail_task(self, task_id: str, error: str, message: str = "任务失败") -> bool:
        """标记任务为失败状态"""
        try:
            task_status = await self.get_task_status(task_id)
            if not task_status:
                return False

            task_status.status = "failed"
            task_status.completed_at = utcnow().isoformat()
            task_status.message = message
            task_status.error = error

            success = await self._save_task_status(task_status)
            if success:
                logger.error(f"任务失败: {task_id} - {error}")
                
                # [note] 发布任务失败事件
                await self._emit_event(
                    TaskEventType.TASK_FAILED,
                    task_id=task_id,
                    task_type=task_status.task_type,
                    status="failed",
                    message=message,
                    error=error
                )
            
            return success

        except Exception as e:
            logger.error(f"标记任务失败时发生错误 {task_id}: {e}")
            return False
    
    async def get_task_status(self, task_id: str) -> Optional[TaskStatus]:
        """获取任务状态"""
        try:
            task_key = f"{self.task_prefix}{task_id}"
            
            if self.storage_type == "redis":
                task_data = await self.storage.get(task_key)
                if task_data is None:
                    return None
                
                if isinstance(task_data, str):
                    task_data = json.loads(task_data)
                
                return TaskStatus(**task_data)
            else:
                # 内存存储
                return await self.storage.get(task_key)
                
        except Exception as e:
            logger.error(f"获取任务状态失败 {task_id}: {e}")
            return None
    
    async def delete_task(self, task_id: str) -> bool:
        """删除任务记录"""
        try:
            task_key = f"{self.task_prefix}{task_id}"
            result = await self.storage.delete(task_key)
            if result:
                logger.info(f"删除任务: {task_id}")
            return bool(result)
        except Exception as e:
            logger.error(f"删除任务失败 {task_id}: {e}")
            return False
    
    async def _save_task_status(self, task_status: TaskStatus) -> bool:
        """保存任务状态"""
        try:
            task_key = f"{self.task_prefix}{task_status.task_id}"
            
            if self.storage_type == "redis":
                task_data = asdict(task_status)
                await self.storage.set(task_key, json.dumps(task_data, ensure_ascii=False), self.default_ttl)
            else:
                # 内存存储直接保存对象
                await self.storage.set(task_key, task_status, self.default_ttl)
            
            return True
        except Exception as e:
            logger.error(f"保存任务状态失败 {task_status.task_id}: {e}")
            return False
    
    async def list_tasks(self, task_type: Optional[str] = None, limit: int = 10) -> list:
        """
        列出任务列表
        
        Args:
            task_type: 可选，按任务类型筛选
            limit: 返回数量限制
        
        Returns:
            任务状态列表
        """
        try:
            pattern = f"{self.task_prefix}*"
            
            if self.storage_type == "redis":
                keys = []
                async for key in self.storage.redis_client.scan_iter(match=pattern):
                    if isinstance(key, bytes):
                        key = key.decode('utf-8')
                    keys.append(key)
            else:
                # 内存存储
                keys = await self.storage.keys(pattern)
            
            tasks = []
            for key in keys[:limit]:
                task_id = key.replace(self.task_prefix, "")
                task_status = await self.get_task_status(task_id)
                
                if task_status:
                    # 过滤任务类型
                    if task_type and task_status.task_type != task_type:
                        continue
                    
                    tasks.append(task_status)
            
            # 按创建时间倒序排序
            tasks.sort(key=lambda x: x.created_at, reverse=True)
            return tasks[:limit]
            
        except Exception as e:
            logger.error(f"列出任务失败: {e}")
            return []
    
    # 🆕 线程安全的辅助方法
    
    @staticmethod
    def create_thread_safe_instance():
        """
        为后台线程创建独立的 TaskManager 实例
        
        [warning] 重要：由于 Redis 连接绑定到事件循环，在后台线程中必须创建独立实例
        
        Returns:
            新的 TaskManager 实例（带有独立的事件循环和存储）
        
        Example:
            ```python
            def background_task(token, task_id):
                # 在后台线程中创建独立的 TaskManager
                thread_tm = TaskManager.create_thread_safe_instance()
                
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                try:
                    loop.run_until_complete(thread_tm.start_task(task_id))
                    # ... 执行任务 ...
                    loop.run_until_complete(thread_tm.complete_task(task_id, result))
                finally:
                    loop.close()
            ```
        """
        import redis.asyncio as redis
        from app.core.config import settings
        
        # 创建新实例（不使用单例）
        instance = object.__new__(TaskManager)
        instance.task_prefix = "task:"
        instance.default_ttl = 3600 * 24
        instance._init_attempted = True  # 跳过全局初始化
        
        # 尝试创建 Redis 存储
        try:
            redis_kwargs = {
                "max_connections": settings.REDIS_MAX_CONNECTIONS,
                "decode_responses": True,
                # 与全局 redis_manager 对齐：空闲超过该秒数的连接借出前自动 PING，
                # 失败即换。此 client 虽为任务级短生命周期，但配置对称可消除疑问、
                # 且未来若复用为长连接也不会漏配。0 = 关闭。
                "health_check_interval": settings.REDIS_HEALTH_CHECK_INTERVAL_SEC,
            }
            if settings.REDIS_PASSWORD:
                redis_kwargs["password"] = settings.REDIS_PASSWORD
            
            # 创建线程专用的 Redis 客户端
            redis_client = redis.from_url(settings.REDIS_URL, **redis_kwargs)

            # 复用 core.cache.RedisKVStore（与全局 AsyncRedisManager 同一份 set/get/delete/keys）；
            # 每线程独立 redis_client（事件循环绑定）不变，RedisKVStore 暴露 self.redis_client。
            from app.core.cache import RedisKVStore

            instance.storage = RedisKVStore(redis_client)
            instance.storage_type = "redis"
            logger.debug("为后台线程创建了独立的 Redis 存储")
            
        except Exception as e:
            # 降级到内存存储
            logger.warning(f"无法为后台线程创建 Redis 存储，使用内存存储: {e}")
            instance.storage = MemoryTaskStorage()
            instance.storage_type = "memory"
        
        # 线程实例使用独立 subject，事件通过桥接回投到全局主循环。
        global_instance = TaskManager._instance
        if global_instance and hasattr(global_instance, '_subject'):
            instance._subject = TaskSubject()
            instance._observer_enabled = global_instance._observer_enabled
            instance._notify_loop = getattr(global_instance, "_notify_loop", None)
            instance._bridge_subject = global_instance._subject
            observer_count = len(global_instance._subject._observers) if hasattr(global_instance._subject, '_observers') else 0
            logger.info(f"[info] 线程安全实例已启用主循环桥接（{observer_count} 个观察者）")
        else:
            # 如果全局实例还没有观察者，初始化空的观察者
            instance._subject = TaskSubject()
            instance._observer_enabled = True
            instance._notify_loop = None
            instance._bridge_subject = instance._subject
            logger.warning("[warning] 线程安全实例创建了独立观察者（全局实例不可用）")
        
        return instance


# 全局任务管理器实例
task_manager = TaskManager() 