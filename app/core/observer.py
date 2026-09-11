"""任务事件发布订阅：TaskSubject 异步通知各 TaskObserver。"""
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set
import logging

from app.core.time_utils import utcnow

logger = logging.getLogger("app.observer")


class TaskEventType(str, Enum):
    """任务生命周期事件枚举。"""
    TASK_CREATED = "task_created"
    TASK_STARTED = "task_started"
    TASK_PROGRESS_UPDATED = "task_progress_updated"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"


@dataclass
class TaskEvent:
    """一次任务状态变更的载荷。"""
    event_type: TaskEventType
    task_id: str
    task_type: str
    timestamp: str = field(default_factory=lambda: utcnow().isoformat())
    
    status: Optional[str] = None
    progress: Optional[int] = None
    message: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转 dict，便于序列化。"""
        return {
            "event_type": self.event_type.value,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "timestamp": self.timestamp,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "metadata": self.metadata,
        }


class TaskObserver(ABC):
    """观察者需实现异步 on_task_event。"""
    
    @abstractmethod
    async def on_task_event(self, event: TaskEvent) -> None:
        """处理一条任务事件。"""
        pass
    
    def get_observer_name(self) -> str:
        """默认用类名。"""
        return self.__class__.__name__


class TaskSubject:
    """维护观察者列表；可按事件类型过滤；notify 时 gather 且单点异常不向外抛。"""
    
    def __init__(self):
        self._observers: List[TaskObserver] = []
        self._filtered_observers: Dict[TaskEventType, Set[TaskObserver]] = {}
        self._lock = asyncio.Lock()
        # asyncio.Lock is not re-entrant: nested notify in the same task would deadlock.
        self._notify_depth = 0
        
        self._event_count = 0
        self._error_count = 0
    
    async def attach(
        self, 
        observer: TaskObserver, 
        event_types: Optional[List[TaskEventType]] = None
    ) -> None:
        """注册观察者；event_types 非空则只收这些类型。"""
        async with self._lock:
            if observer not in self._observers:
                self._observers.append(observer)
                logger.info(f"[success] 注册观察者: {observer.get_observer_name()}")
            
            if event_types:
                for event_type in event_types:
                    if event_type not in self._filtered_observers:
                        self._filtered_observers[event_type] = set()
                    self._filtered_observers[event_type].add(observer)
                logger.info(
                    f"  过滤器: {observer.get_observer_name()} → {[et.value for et in event_types]}"
                )
    
    async def detach(self, observer: TaskObserver) -> None:
        """移除观察者及其过滤条目。"""
        async with self._lock:
            if observer in self._observers:
                self._observers.remove(observer)
                logger.info(f"[event] 注销观察者: {observer.get_observer_name()}")
            
            for event_type_set in self._filtered_observers.values():
                event_type_set.discard(observer)
    
    async def detach_all(self) -> int:
        """清空列表，返回原长度。"""
        async with self._lock:
            count = len(self._observers)
            self._observers.clear()
            self._filtered_observers.clear()
            logger.info(f"[event] 已注销所有观察者: {count} 个")
            return count
    
    async def notify(self, event: TaskEvent) -> None:
        """并发调用相关观察者的 on_task_event。"""
        if self._notify_depth > 0:
            logger.warning(
                "Skipped re-entrant TaskSubject.notify for %s (task_id=%s)",
                event.event_type.value,
                event.task_id,
            )
            return

        self._notify_depth += 1
        try:
            self._event_count += 1

            async with self._lock:
                observers_to_notify = self._get_observers_for_event(event)

            if not observers_to_notify:
                logger.debug(f"[event] 事件 {event.event_type.value} 无订阅者")
                return

            logger.debug(
                f"[event] 发布事件: {event.event_type.value} "
                f"[task_id={event.task_id}] → {len(observers_to_notify)} 个观察者"
            )

            tasks = []
            for observer in observers_to_notify:
                task = asyncio.create_task(
                    self._notify_observer(observer, event)
                )
                tasks.append(task)

            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self._notify_depth -= 1
    
    def _get_observers_for_event(self, event: TaskEvent) -> List[TaskObserver]:
        """有过滤器的只收匹配类型；无过滤器收全部。"""
        observers_to_notify = []
        
        for observer in self._observers:
            has_filter = any(
                observer in observer_set 
                for observer_set in self._filtered_observers.values()
            )
            
            if has_filter:
                if event.event_type in self._filtered_observers:
                    if observer in self._filtered_observers[event.event_type]:
                        observers_to_notify.append(observer)
            else:
                observers_to_notify.append(observer)
        
        return observers_to_notify
    
    async def _notify_observer(self, observer: TaskObserver, event: TaskEvent) -> None:
        """单观察者 try/except，失败只记 error_count。"""
        try:
            await observer.on_task_event(event)
        except Exception as e:
            self._error_count += 1
            logger.error(
                f"[error] 观察者 {observer.get_observer_name()} "
                f"处理事件 {event.event_type.value} 失败: {e}",
                exc_info=True
            )
    
    def get_stats(self) -> Dict[str, Any]:
        """观察者数量与事件计数。"""
        return {
            "observer_count": len(self._observers),
            "event_count": self._event_count,
            "error_count": self._error_count,
            "filtered_observers": {
                event_type.value: len(observers)
                for event_type, observers in self._filtered_observers.items()
            }
        }
