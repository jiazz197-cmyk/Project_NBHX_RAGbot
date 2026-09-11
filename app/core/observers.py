"""TaskManager 事件观察者：日志、指标、进度、告警、历史、WebSocket 等。"""
from typing import Dict, Any

from app.core.observer import TaskObserver, TaskEvent, TaskEventType
from app.core.logging import get_logger
from app.core.time_utils import utc_timestamp

logger = get_logger("task_observers")


class LoggingObserver(TaskObserver):
    """把任务事件打到日志；进度类事件用 debug 降噪。"""
    
    async def on_task_event(self, event: TaskEvent) -> None:
        """组装一行摘要后 info/debug。"""
        log_msg = (
            f"[event] 任务事件: {event.event_type.value} | "
            f"ID: {event.task_id} | "
            f"类型: {event.task_type} | "
            f"状态: {event.status or 'N/A'}"
        )
        
        if event.progress is not None:
            log_msg += f" | 进度: {event.progress}%"
        
        if event.message:
            log_msg += f" | 消息: {event.message}"
        
        if event.error:
            log_msg += f" | 错误: {event.error}"
        
        if event.event_type == TaskEventType.TASK_PROGRESS_UPDATED:
            logger.debug(log_msg)
        else:
            logger.info(log_msg)


class MetricsCollector(TaskObserver):
    """内存里计数各生命周期事件，可选配合 Prometheus。"""
    
    def __init__(self):
        self._metrics: Dict[str, int] = {
            "tasks_created": 0,
            "tasks_started": 0,
            "tasks_completed": 0,
            "tasks_failed": 0,
        }
        self._task_durations: Dict[str, float] = {}
    
    async def on_task_event(self, event: TaskEvent) -> None:
        """按事件类型累加计数并算耗时。"""
        if event.event_type == TaskEventType.TASK_CREATED:
            self._metrics["tasks_created"] += 1
        
        elif event.event_type == TaskEventType.TASK_STARTED:
            self._metrics["tasks_started"] += 1
            self._task_durations[event.task_id] = utc_timestamp()
        
        elif event.event_type == TaskEventType.TASK_COMPLETED:
            self._metrics["tasks_completed"] += 1
            if event.task_id in self._task_durations:
                start_time = self._task_durations.pop(event.task_id)
                duration = utc_timestamp() - start_time
                logger.info(f"[event] 任务 {event.task_id} 执行时长: {duration:.2f}秒")
        
        elif event.event_type == TaskEventType.TASK_FAILED:
            self._metrics["tasks_failed"] += 1
            self._task_durations.pop(event.task_id, None)
    
    def get_metrics(self) -> Dict[str, int]:
        """计数器快照。"""
        return self._metrics.copy()
    
    def get_observer_name(self) -> str:
        return "MetricsCollector"

