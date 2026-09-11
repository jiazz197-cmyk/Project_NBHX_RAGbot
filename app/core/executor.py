"""
全局线程池：任务提交、按 task_id 保留 Future、协作式取消。

取消只能协作完成（线程无法被强杀）；任务内应检查 CancellationToken。
future.cancel() 仅对尚未开始的任务生效；task_id 须全局唯一。可对接 TaskManager 同步状态。
"""
import asyncio
import inspect
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Dict, Optional, Callable, Any, TYPE_CHECKING

from app.core.config import settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.core.task_manager import TaskManager

logger = get_logger("executor")

# shutdown(cancel_futures=...) 需 Python 3.9+
PYTHON_39_PLUS = sys.version_info >= (3, 9)


def _run_coro_in_fresh_event_loop(coro: Any) -> Any:
    """Run a single coroutine in a new loop and always close the loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.close()
        except Exception:
            pass


# Python version info is logged once at application startup (main.py lifespan).


class CancellationToken:
    """协作式取消：任务内应优先调用 token.is_cancelled()，比按 task_id 查表少锁竞争。"""
    def __init__(self):
        self._event = threading.Event()
    
    def cancel(self):
        """置位取消事件。"""
        self._event.set()
    
    def is_cancelled(self) -> bool:
        """是否已请求取消（Event 查询，开销小）。"""
        return self._event.is_set()


class ExecutorManager:
    """单例：托管 ThreadPoolExecutor，为每个任务注入 CancellationToken 并保留 Future；可选同步 TaskManager。"""
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        """单例首次构造时调用。"""
        if self._initialized:
            return
        
        self._max_workers = getattr(settings, 'EXECUTOR_MAX_WORKERS', 4)
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="bg_executor_"
        )
        self._max_task_history = 60
        self._task_futures: Dict[str, Future] = {}
        self._cancellation_tokens: Dict[str, CancellationToken] = {}
        self._lock = threading.Lock()
        self._shutdown = False
        
        self._task_manager: Optional['TaskManager'] = None
        self._auto_sync_status = False
        
        self._initialized = True
        logger.info(f"初始化全局线程池: {self._max_workers} workers")

    def _trim_task_history_locked(self):
        """在持锁状态下裁剪任务历史。只驱逐已结束（Future.done()）的项，绝不删除运行中任务的 Future/token。"""
        while len(self._task_futures) > self._max_task_history:
            evict_id = next(
                (tid for tid, fut in self._task_futures.items() if fut.done()),
                None,
            )
            if evict_id is None:
                logger.warning(
                    "任务历史超过上限 %s 且均为运行中，跳过驱逐，避免破坏取消",
                    self._max_task_history,
                )
                break
            self._task_futures.pop(evict_id, None)
            self._cancellation_tokens.pop(evict_id, None)

    def forget_task(self, task_id: str) -> bool:
        """Forget a previously submitted task so the same task_id can be submitted again.

        Only effective if the prior Future has finished; otherwise refuses to avoid
        orphaning a running worker. Removes Future and cancellation token.
        Owner data is held by `TaskOwnerRegistry`, not here.
        Returns True when the task was present and cleared.
        """
        with self._lock:
            future = self._task_futures.get(task_id)
            if future is not None and not future.done():
                return False
            had_any = task_id in self._task_futures or task_id in self._cancellation_tokens
            self._task_futures.pop(task_id, None)
            self._cancellation_tokens.pop(task_id, None)
            return had_any
    
    def set_task_manager(self, task_manager: Optional['TaskManager'], auto_sync: bool = True):
        """接入或断开 TaskManager；auto_sync 控制是否自动回写任务状态。"""
        with self._lock:
            self._task_manager = task_manager
            self._auto_sync_status = auto_sync
            if task_manager:
                logger.info(f"[success] ExecutorManager 已集成 TaskManager (auto_sync={auto_sync})")
            else:
                logger.info("[error] ExecutorManager 已禁用 TaskManager 集成")
    
    def generate_task_id(self, task_type: str) -> str:
        """生成 task_id：已接 TaskManager 则委托，否则本地时间戳 + uuid。"""
        if self._task_manager:
            return self._task_manager.generate_task_id(task_type)
        else:
            import uuid
            from app.core.time_utils import utcnow_naive
            timestamp = utcnow_naive().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            return f"{task_type}_{timestamp}_{uuid.uuid4().hex[:8]}"
    
    @property
    def executor(self) -> ThreadPoolExecutor:
        """底层 ThreadPoolExecutor；若已 shutdown 则抛错。"""
        if self._shutdown:
            raise RuntimeError("线程池已关闭")
        return self._executor
    
    def submit_task(
        self, 
        task_id: str, 
        fn: Callable[[CancellationToken, Any], Any],
        *args,
        sync_to_task_manager: Optional[bool] = None,
        **kwargs
    ) -> Future:
        """提交 fn(token, *args, **kwargs)；token 自动注入。sync_to_task_manager 默认跟全局开关。"""
        with self._lock:
            if self._shutdown:
                raise RuntimeError("线程池已关闭，无法提交新任务")
            
            if task_id in self._task_futures:
                raise ValueError(f"task_id '{task_id}' 已存在，请使用唯一标识（如 UUID）")
            
            if getattr(settings, 'DEBUG', False):
                try:
                    sig = inspect.signature(fn)
                    params = list(sig.parameters.values())
                    
                    if not params:
                        raise TypeError(
                            f"函数 {fn.__name__} 必须接受至少一个参数（CancellationToken）"
                        )
                    
                    first_param = params[0]
                    if first_param.name != 'token':
                        logger.warning(
                            f"函数 {fn.__name__} 第一个参数名为 '{first_param.name}'，"
                            f"建议命名为 'token' 以提高可读性"
                        )
                        
                except Exception as e:
                    logger.debug(f"签名验证跳过: {e}")
            
            token = CancellationToken()
            self._cancellation_tokens[task_id] = token
            
            should_sync = sync_to_task_manager if sync_to_task_manager is not None else self._auto_sync_status
            needs_task_manager = should_sync and self._task_manager is not None
            
            def wrapped_fn():
                try:
                    # 在工作线程里起事件循环回写 TaskManager，避免 submit 持锁 await
                    if needs_task_manager:
                        try:
                            _run_coro_in_fresh_event_loop(self._task_manager.start_task(task_id))
                            logger.debug(f"[{task_id}] 已同步状态到 TaskManager: running")
                        except Exception as e:
                            logger.warning(f"[{task_id}] 同步启动状态到 TaskManager 失败: {e}")

                    result = fn(token, *args, **kwargs)
                    
                    if needs_task_manager:
                        try:
                            task_result = result if isinstance(result, dict) else {"result": result}
                            _run_coro_in_fresh_event_loop(
                                self._task_manager.complete_task(task_id, task_result, "任务完成")
                            )
                            logger.debug(f"[{task_id}] 已同步完成状态到 TaskManager")
                        except Exception as e:
                            logger.warning(f"[{task_id}] 同步完成状态到 TaskManager 失败: {e}")
                    
                    return result
                    
                except KeyboardInterrupt:
                    logger.warning(f"任务 {task_id} 收到 KeyboardInterrupt，正在退出")
                    token.cancel()
                    
                    if needs_task_manager:
                        try:
                            _run_coro_in_fresh_event_loop(
                                self._task_manager.fail_task(task_id, "KeyboardInterrupt", "任务被中断")
                            )
                        except Exception:
                            pass
                    
                    raise
                except SystemExit:
                    logger.warning(f"任务 {task_id} 收到 SystemExit，正在退出")
                    raise
                except Exception as e:
                    logger.error(f"任务 {task_id} 执行失败: {e}", exc_info=True)
                    
                    if needs_task_manager:
                        try:
                            _run_coro_in_fresh_event_loop(
                                self._task_manager.fail_task(task_id, str(e), "任务执行失败")
                            )
                            logger.debug(f"[{task_id}] 已同步失败状态到 TaskManager")
                        except Exception as e2:
                            logger.warning(f"[{task_id}] 同步失败状态到 TaskManager 失败: {e2}")
                    
                    raise
            
            future = self._executor.submit(wrapped_fn)
            self._task_futures[task_id] = future
            self._trim_task_history_locked()
            
            def cleanup_callback(f):
                acquired = self._lock.acquire(blocking=False)
                if not acquired:
                    logger.debug(f"任务 {task_id} token 清理延迟（锁被占用）")
                    try:
                        threading.Timer(0.1, self._delayed_cleanup, args=(task_id,)).start()
                    except Exception as timer_err:
                        logger.warning(f"任务 {task_id} 延迟清理 Timer 启动失败: {timer_err}")
                    return
                
                try:
                    self._cancellation_tokens.pop(task_id, None)
                    logger.debug(f"任务 {task_id} token 已清理（Future 保留用于查询）")
                except Exception as e:
                    logger.error(f"清理任务 {task_id} token 失败: {e}")
                finally:
                    self._lock.release()
            
            future.add_done_callback(cleanup_callback)
            logger.debug(f"提交任务到线程池: {task_id}")
            return future
    
    def _delayed_cleanup(self, task_id: str):
        """锁争用时延后删掉 token；Future 仍保留给 wait/get。"""
        try:
            with self._lock:
                self._cancellation_tokens.pop(task_id, None)
                logger.debug(f"任务 {task_id} token 延迟清理完成")
        except Exception as e:
            logger.error(f"延迟清理任务 {task_id} token 失败: {e}")
    
    def is_task_cancelled(self, task_id: str) -> bool:
        """按 task_id 查是否已取消（需加锁）；能拿到 token 时优先用 token.is_cancelled()。"""
        with self._lock:
            token = self._cancellation_tokens.get(task_id)
            return token.is_cancelled() if token else False
    
    def cancel_task(self, task_id: str) -> bool:
        """协作式取消：先置 token，再尝试 future.cancel()；已运行的任务须自行退出。线程安全。"""
        with self._lock:
            future = self._task_futures.get(task_id)
            token = self._cancellation_tokens.get(task_id)
            
            if not future or not token:
                logger.warning(f"无法取消任务 {task_id}：任务不存在")
                return False
            
            token.cancel()
            
            if not future.done():
                cancelled = future.cancel()
                if cancelled:
                    logger.info(f"成功取消未开始的任务: {task_id}")
                    return True
                
                logger.info(f"任务 {task_id} 正在运行，已设置取消标志（需任务主动检查）")
                return True
            
            logger.warning(f"任务 {task_id} 已完成，无法取消")
            return False
    
    def wait_task(self, task_id: str, timeout: Optional[float] = None) -> Any:
        """阻塞直到 Future 完成或超时；异常与原任务一致。"""
        future = self.get_task_future(task_id)
        if not future:
            raise KeyError(f"任务 {task_id} 不存在或已完成")
        
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            logger.warning(f"等待任务 {task_id} 超时")
            raise
        except Exception as e:
            logger.error(f"任务 {task_id} 执行失败: {e}")
            raise
    
    def get_task_future(self, task_id: str) -> Optional[Future]:
        """取 Future；完成后仍会保留直到历史裁剪或 shutdown。"""
        with self._lock:
            return self._task_futures.get(task_id)
    
    def get_active_task_count(self) -> int:
        """当前缓存的 task 条数（含已完成未清掉的 Future）。"""
        with self._lock:
            return len(self._task_futures)
    
    def get_running_task_count(self) -> int:
        """未 done 的 Future 数量。"""
        with self._lock:
            return sum(1 for future in self._task_futures.values() if not future.done())
    
    def shutdown(self, wait: bool = True, cancel_futures: bool = False, timeout: float = 30.0):
        """关闭线程池。timeout 只打日志，标准库 shutdown 无超时参数。shutdown 后不可再 submit。"""
        with self._lock:
            if self._shutdown:
                logger.warning("线程池已经关闭")
                return
            
            self._shutdown = True
            active_count = len(self._task_futures)
            
            if cancel_futures:
                for task_id, token in self._cancellation_tokens.items():
                    token.cancel()
                logger.info(f"已设置 {len(self._cancellation_tokens)} 个任务的取消标志")
        
        logger.info(f"关闭线程池: wait={wait}, cancel_futures={cancel_futures}, 期望超时={timeout}s, 活跃任务={active_count}")
        
        try:
            if PYTHON_39_PLUS:
                self._executor.shutdown(
                    wait=wait, 
                    cancel_futures=cancel_futures
                )
            else:
                logger.warning(f"Python {sys.version_info.major}.{sys.version_info.minor} 不支持 shutdown cancel_futures 参数")
                if cancel_futures:
                    logger.warning("cancel_futures=True 在 Python < 3.9 中不可用，等待中的任务会继续执行")
                self._executor.shutdown(wait=wait)
            
            logger.info("线程池已关闭")
        except Exception as e:
            logger.error(f"关闭线程池时发生错误: {e}")
        finally:
            with self._lock:
                self._task_futures.clear()
                self._cancellation_tokens.clear()


executor_manager = ExecutorManager()


def attach_future_result_logger(future: Future, task_id: str) -> Future:
    """Attach a done-callback that retrieves and logs any exception on ``future``.

    OCR/doc tasks are submitted with auto_sync=False (no TaskManager writeback),
    and their result is consumed lazily via polling (get_task_future().result()).
    If nobody polls a task that raised, Python emits "exception was never
    retrieved" warnings and the failure is invisible. This callback marks the
    exception retrieved and logs it, so failures always surface in the logs even
    when the caller abandons the task.
    """
    def _on_done(fut: Future) -> None:
        exc = fut.exception()
        if exc is not None:
            logger.error("后台任务异常退出 task_id=%s: %s", task_id, exc, exc_info=exc)
    future.add_done_callback(_on_done)
    return future