"""应用依赖装配（.dsh/ragchain-interfaces.md §13）。"""

from __future__ import annotations

from dataclasses import dataclass

from .clients.backend_client import BackendClient
from .clients.llm_client import LLMClient
from .clients.reranker_client import RerankerClient
from .clients.retriever_client import RetrieverClient
from .clients.search_client import SearchClient
from .config import Settings
from .task_registry import TaskRegistry
from .tools.python_executor import PythonExecutor


@dataclass
class AppDeps:
    settings: Settings
    backend: BackendClient
    retriever: RetrieverClient
    reranker: RerankerClient
    search: SearchClient
    llm: LLMClient
    executor: PythonExecutor
    registry: TaskRegistry


def build_deps(settings: Settings) -> AppDeps:
    return AppDeps(
        settings=settings,
        backend=BackendClient(settings.MAIN_APP_BASE_URL, settings.OUTBOUND_HTTP_TIMEOUT_SEC),
        retriever=RetrieverClient(settings.MAIN_APP_BASE_URL, settings.OUTBOUND_HTTP_TIMEOUT_SEC),
        reranker=RerankerClient(
            settings.RERANKER_API_URL,
            settings.RERANKER_MODEL_NAME,
            settings.AI_INFERENCE_API_KEY,
            settings.RERANKER_TIMEOUT_SEC,
        ),
        search=SearchClient(settings.SEARCH_ENGINE_URL, settings.SEARCH_TIMEOUT_SEC),
        llm=LLMClient(settings),
        executor=PythonExecutor(
            settings.TOOL_EXEC_TIMEOUT_SEC,
            settings.TOOL_CODE_MAX_CHARS,
            settings.TOOL_OUTPUT_MAX_CHARS,
        ),
        registry=TaskRegistry(settings.TASK_REGISTRY_TTL_SEC),
    )


async def close_deps(deps: AppDeps) -> None:
    for client in (deps.backend, deps.retriever, deps.reranker, deps.search, deps.llm):
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001 - 关闭阶段不影响进程退出
            pass
