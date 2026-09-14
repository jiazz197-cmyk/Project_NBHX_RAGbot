"""FastAPI 应用入口。"""
import os

# GPU：须在导入其他模块前配置；.env 由 dotenv 加载
from dotenv import load_dotenv
load_dotenv()

import warnings
warnings.filterwarnings("ignore", message="CUDA initialization")

# 热重载会重复导入模块，用环境变量避免重复初始化 GPU
if not os.environ.get("_GPU_INITIALIZED"):
    os.environ["_GPU_INITIALIZED"] = "1"
    
    LOCAL_GPU_DEVICE = os.environ.get("LOCAL_MODEL_GPU_DEVICE", "3")
    # 不设 CUDA_VISIBLE_DEVICES，Docker 内仍需访问多块 GPU；设备由各库 API 指定
    os.environ.setdefault("LOCAL_MODEL_GPU_DEVICE", LOCAL_GPU_DEVICE)
    
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    os.environ.setdefault("FLAGS_use_cudnn", "1")

    try:
        import torch
        if torch.cuda.is_available():
            print(f"[success] PyTorch CUDA 可用，本地模型将使用 GPU:{LOCAL_GPU_DEVICE}")
        else:
            print("[info] PyTorch CUDA 不可用")
    except ImportError:
        pass
else:
    LOCAL_GPU_DEVICE = os.environ.get("LOCAL_MODEL_GPU_DEVICE", "3")

from app.adapters.ragsystem.RAGretriever import create_rag_retriever_system

import asyncio
from contextlib import asynccontextmanager

import sys
from typing import Any, Dict

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware
from app.api.v1.registry import api_router
from app.api.v1.tags import OPENAPI_TAG_METADATA
from app.core.cache import redis_manager
from app.core.config import settings
from app.core.exceptions import APIException
from app.core.logging import get_logger, setup_logging
from app.core.middleware.middleware_cache import CacheMiddleware
from app.core.middleware.monitoring import MonitoringMiddleware
from app.core.middleware.rate_limit import RateLimitMiddleware
from app.core.middleware.request_size import RequestSizeMiddleware
from app.core.middleware.security_headers import SecurityHeadersMiddleware
from app.adapters.monitoring.health_check import health_service
from app.adapters.monitoring.prometheus import metrics as prometheus_metrics

logger = get_logger("main")


def require_metrics_access(x_api_key: str | None = Header(default=None, alias="X-API-KEY")) -> None:
    """生产场景下用 API Key 限制 /metrics，避免公网裸奔。"""
    if not settings.METRICS_REQUIRE_API_KEY:
        return
    if x_api_key != settings.INTERNAL_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API Key",
        )


async def shutdown_all_pools() -> None:
    """有序关闭所有连接池：PG -> Redis -> 模型池 -> MinIO。

    收口到一处：一处看全、一处改全。各池独立 try/except，单个失败不阻塞后续。
    须在消费方（executor / 观察者 / retention / WebSocket）已停后再调用，否则
    归还在途的连接可能打到已关的池上（post-close 路径会兜底关闭，不致命）。
    """
    # 1. PostgreSQL（同步 + 异步 engine）
    try:
        from app.core.database import dispose_async_engine, dispose_engine
        await dispose_async_engine()
        dispose_engine()
        print("[success] PostgreSQL 连接池已释放（sync + async）")
    except Exception as e:
        print(f"[warning] 释放 PostgreSQL 连接池时出错: {e}")

    # 2. Redis
    try:
        await asyncio.wait_for(redis_manager.close(), timeout=2.0)
        print("[success] Redis 已关闭")
    except asyncio.TimeoutError:
        print("[warning] Redis 关闭超时，跳过")
    except Exception as e:
        print(f"[warning] 关闭 Redis 时出错: {e}")

    # 3. 文档处理模型池（PaddleOCR / TagGenerator）
    try:
        from app.adapters.doc_processing.doc_reader import _paddleocr_pool
        from app.adapters.doc_processing.text_splitter import _taggen_pool
        _paddleocr_pool.close()
        _taggen_pool.close()
        print("[success] 文档处理模型池已关闭")
    except Exception as e:
        print(f"[warning] 关闭文档处理模型池时出错: {e}")

    # 5. MinIO
    try:
        from app.core.async_storage import AsyncMinioClientPool
        await AsyncMinioClientPool.close()
        print("[success] 异步 MinIO 客户端已关闭")
    except Exception as e:
        print(f"[warning] 关闭异步 MinIO 时出错: {e}")


def pool_snapshot() -> Dict[str, Dict[str, Any]]:
    """三池连接状态快照：``{pool: {idle, checked_out, max}}``。

    排查"连接被占满"时三池一张表。纯读池内部计数，不做任何 IO；各池读取独立
    try/except，某个驱动改了内部属性也不会让 /health 挂掉。
    """
    snap: Dict[str, Dict[str, Any]] = {}

    # PostgreSQL（异步 engine 池；同步 engine 复用同一组旋钮，不单独报）
    try:
        from app.core.database import async_engine
        p = async_engine.pool
        snap["postgresql"] = {
            "idle": p.checkedin(),
            "checked_out": p.checkedout(),
            "max": p.size() + getattr(p, "_max_overflow", 0),
        }
    except Exception as exc:  # noqa: BLE001 - 可观测性不能影响 /health
        logger.debug("pool_snapshot postgresql 失败: %s", exc)

    # Redis
    try:
        cp = redis_manager.redis_client.connection_pool
        snap["redis"] = {
            "idle": len(cp._available_connections),
            "checked_out": len(cp._in_use_connections),
            "max": cp.max_connections,
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("pool_snapshot redis 失败: %s", exc)

    return snap


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化依赖，关闭时按序释放。"""
    app.state.metrics = prometheus_metrics
    try:
        from app.core.database import init_db_tables
        await asyncio.to_thread(init_db_tables)
    except Exception as e:
        print(f"[warning] 数据库表初始化失败: {e}")
    
    try:
        from app.core.executor import executor_manager
        from app.core.task_manager import task_manager
        executor_manager.set_task_manager(task_manager, auto_sync=False)  # 默认不向 TaskManager 自动同步
        from app.core.executor import PYTHON_39_PLUS
        print(
            f"[info] Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}, "
            f"advanced shutdown: {PYTHON_39_PLUS}"
        )
        print("[success] ExecutorManager 已集成 TaskManager")
    except Exception as e:
        print(f"[warning] ExecutorManager 集成 TaskManager 失败: {e}")
    
    try:
        from app.core.task_manager import task_manager
        from app.core.observers import (
            LoggingObserver, 
            MetricsCollector,
        )
        from app.core.websocket_task_manager import (
            WebSocketTaskObserver,
            ws_manager,
        )
        
        logging_observer = LoggingObserver()
        await task_manager.register_observer(logging_observer)
        
        metrics_observer = MetricsCollector()
        await task_manager.register_observer(metrics_observer)
        app.state.metrics_observer = metrics_observer
        
        ws_observer = WebSocketTaskObserver(ws_manager)
        await task_manager.register_observer(ws_observer)
        task_manager.set_notify_loop(asyncio.get_running_loop())
        
        print("[success] 任务观察者注册完成")
        observer_stats = task_manager.get_observer_stats()
        print(f"  - 已注册观察者数: {observer_stats['observer_count']}")
        print(f"  - 观察者状态: {'启用' if observer_stats['observer_enabled'] else '禁用'}")
        
    except Exception as e:
        print(f"[warning] 注册任务观察者失败: {e}")

    try:
        from app.core.retention_scheduler import run_reconcile_once, start_reconcile_scheduler

        await run_reconcile_once()
        app.state.reconcile_task = start_reconcile_scheduler()
        print("[success] MinIO reconcile 调度已启动")
    except Exception as e:
        print(f"[warning] MinIO reconcile 调度启动失败: {e}")
    
    try:
        await redis_manager.test_connection()
    except Exception:
        # 启动不依赖 Redis 就绪，后续由中间件兜底
        pass
    
    try:
        from app.core.async_storage import AsyncMinioClientPool, MINIO_BUCKET_NAME
        await AsyncMinioClientPool.ensure_bucket(MINIO_BUCKET_NAME)
        print(f"[success] MinIO 异步连接成功，bucket: {MINIO_BUCKET_NAME}")
    except Exception as e:
        print(f"[warning] MinIO 连接失败: {e}，服务将降级运行")
    
    try:
        rag_system = create_rag_retriever_system(
            host=settings.POSTGRES_SERVER,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            database=settings.POSTGRES_DB,
            port=settings.POSTGRES_PORT,
            table_prefix="doc_collection",
            instance_id=1,
            bge_m3_api_url=settings.BGE_M3_API_URL,
            reranker_api_url=settings.RERANKER_API_URL,
            default_top_k=20,
            default_top_n=3
        )
        app.state.rag = rag_system
        print("[success] RAG 系统初始化完成")
        print(f"  - BGE-M3 嵌入模型: {settings.BGE_M3_API_URL}")
        print(f"  - Reranker 重排序器: {settings.RERANKER_API_URL}")
    except Exception as e:
        print(f"[warning] RAG 系统初始化失败: {e}")
        app.state.rag = None
    
    yield
    
    import signal

    print("\n[shutdown] 开始关闭...")
    def force_exit(signum=None, frame=None):
        print("\n[warning] 关闭超时，强制退出")
        sys.exit(1)
    
    signal.signal(signal.SIGALRM, force_exit)
    signal.alarm(10)
    
    try:
        try:
            if hasattr(app.state, "rag") and app.state.rag:
                try:
                    from app.adapters.ragsystem.retriever_for_yamato import ModelManager

                    ModelManager().clear_cache()
                except Exception as mm_exc:
                    print(f"[warning] 清理 ModelManager 时出错: {mm_exc}")
                await app.state.rag.cleanup()
            print("[success] RAG 系统清理完成")
        except Exception as e:
            print(f"[warning] 清理 RAG 时出错: {e}")

        try:
            from app.core.executor import executor_manager
            executor_manager.shutdown(wait=False, cancel_futures=True)
            print("[success] 线程池已关闭")
        except Exception as e:
            print(f"[warning] 关闭线程池时出错: {e}")

        try:
            from app.core.task_manager import task_manager
            await asyncio.wait_for(task_manager.remove_all_observers(), timeout=1.0)
            print("[success] 观察者已清理")
        except asyncio.TimeoutError:
            print("[warning] 清理观察者超时，跳过")
        except Exception as e:
            print(f"[warning] 清理观察者时出错: {e}")
        
        try:
            from app.core.retention_scheduler import stop_reconcile_scheduler
            await stop_reconcile_scheduler()
            print("[success] MinIO reconcile 调度已停止")
        except Exception as e:
            print(f"[warning] 停止 MinIO reconcile 调度时出错: {e}")

        try:
            from app.core.websocket_task_manager import ws_manager
            await asyncio.wait_for(ws_manager.disconnect_all(), timeout=1.0)
            print("[success] WebSocket 已关闭")
        except asyncio.TimeoutError:
            print("[warning] WebSocket 关闭超时，跳过")
        except Exception as e:
            print(f"[warning] 关闭 WebSocket 时出错: {e}")

        # 连接池收口：PG -> Redis -> 模型池 -> MinIO，一处看全、一处改全。
        # 在 executor / 观察者 / retention / WebSocket 均已停后调用。
        await shutdown_all_pools()

        try:
            from app.core.http_client import HttpClientManager, SyncHttpClientManager
            await HttpClientManager.close()
            SyncHttpClientManager.close()
            print("[success] HTTP 客户端已关闭")
        except Exception as e:
            print(f"[warning] 关闭 HTTP 客户端时出错: {e}")

    finally:
        signal.alarm(0)
        print("[shutdown] 关闭完成\n")

def create_app() -> FastAPI:
    is_production = str(settings.ENVIRONMENT).strip().lower() in {"production", "prod"}
    docs_url = None if is_production else f"{settings.API_V1_STR}/docs"
    redoc_url = None if is_production else f"{settings.API_V1_STR}/redoc"
    openapi_url = None if is_production else f"{settings.API_V1_STR}/openapi.json"

    app = FastAPI(
        title=settings.PROJECT_NAME,
        description=settings.DESCRIPTION,
        version=settings.VERSION,
        debug=settings.DEBUG,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        openapi_tags=OPENAPI_TAG_METADATA,
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.redis = redis_manager.redis_client

    cors_origins = settings.BACKEND_CORS_ORIGINS or []
    allow_credentials = "*" not in cors_origins
    allowed_hosts = settings.ALLOWED_HOSTS or ["localhost", "127.0.0.1"]

    if settings.ENABLE_SECURITY_HEADERS:
        app.add_middleware(SecurityHeadersMiddleware)   # 最内
    app.add_middleware(RequestSizeMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(CacheMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    app.add_middleware(MonitoringMiddleware)             # 最外

    @app.get("/")
    async def root():
        return {"project": settings.PROJECT_NAME, "version": settings.VERSION}

    @app.get(f"{settings.API_V1_STR}/health")
    async def api_health():
        services = await health_service.check_all_services()
        overall = "healthy" if all(item["status"] == "healthy" for item in services.values()) else "unhealthy"
        return {"status": overall, "services": services, "pools": pool_snapshot()}

    @app.get(f"{settings.API_V1_STR}/metrics", include_in_schema=False)
    async def metrics_endpoint(_: None = Depends(require_metrics_access)):
        data = prometheus_metrics.get_metrics()
        return Response(content=data, media_type="text/plain")

    @app.exception_handler(APIException)
    async def api_exception_handler(request, exc: APIException):
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(api_router, prefix=settings.API_V1_STR)

    return app


setup_logging()
app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",  # 使用字符串以支持热重载
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.RELOAD,
        ws_ping_interval=30.0,
        ws_ping_timeout=60.0,
    )
