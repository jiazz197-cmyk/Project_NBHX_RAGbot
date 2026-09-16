"""TagGenerator HTTP 适配器：调用独立的 tagger 容器（issue #10）。

主应用不再在进程内加载 SentenceTransformer / KeyBERT（也就不再需要 GPU 与
torch），标签提取改为调用容器里的 ``POST {TAGGER_ENDPOINT}``：

    请求  {"text": ..., "num_tags": 5, "diversity": 0.5}
    响应  {"tags": [...], "model": ..., "processing_time_ms": ..., "fallback": false}

约定（CLAUDE.md）：失败一律映射为 ``ExternalServiceError``（502），不抛裸
``Exception``。文档处理跑在 worker 线程里，所以实现是**同步**的，复用全局
``httpx.Client`` 连接池（``app.core.http_client``）。

降级分为两层：
- ``TagGenerator`` 薄壳（``text_splitter.py``）捕获本适配器的异常，退化为本地
  CPU ``_simple_tags``，文档流程不阻塞；
- 本适配器内的连续失败熔断（``_FailureCooldown``）：服务整体不可用时不再对
  每个 chunk 都「重试 + 等超时」——一篇文档几百个 chunk，那样会把一次上传从
  秒级拖到小时级。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_fixed

from app.core.config import settings
from app.core.exceptions import ExternalServiceError
from app.core.http_client import get_sync_http_client

logger = logging.getLogger(__name__)

SERVICE_NAME = "TagGenerator"

# 失败详情里回显的响应体长度上限（422 的 detail 很有用，但不能整段入日志）
_BODY_SNIPPET_LIMIT = 300


def _is_retryable(exc: BaseException) -> bool:
    """只重试传输层失败与 5xx/429：4xx 是地址/入参/契约问题，重试无意义。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, httpx.TransportError)


class _FailureCooldown:
    """连续失败熔断（半开）：服务整体不可用时短路，避免逐 chunk 重试拖垮文档处理。

    连续 ``threshold`` 次失败后进入 ``cooldown_sec`` 冷却：冷却期内 ``allow()``
    返回 False（调用方直接走本地兜底，不再发请求）；冷却期满放行一次探测请求，
    成功即复位计数，失败则重新计时。``threshold <= 0`` 表示关闭熔断。

    线程安全：文档处理是多 worker 线程并发调用，状态用锁保护。
    """

    def __init__(self, threshold: int, cooldown_sec: float):
        self._threshold = max(0, int(threshold))
        self._cooldown_sec = max(0.0, float(cooldown_sec))
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._open_until = 0.0

    @property
    def enabled(self) -> bool:
        return self._threshold > 0 and self._cooldown_sec > 0

    def allow(self) -> bool:
        if not self.enabled:
            return True
        with self._lock:
            return time.monotonic() >= self._open_until

    def record_failure(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._threshold:
                # 已开则续期，不重复打日志
                if time.monotonic() >= self._open_until:
                    logger.warning(
                        "TagGenerator 连续失败 %s 次，熔断 %ss（期间直接走本地兜底标签）",
                        self._consecutive_failures,
                        self._cooldown_sec,
                    )
                self._open_until = time.monotonic() + self._cooldown_sec

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._open_until = 0.0

    def reset(self) -> None:
        """清空熔断状态（测试 / 运维手动复位用）。"""
        with self._lock:
            self._consecutive_failures = 0
            self._open_until = 0.0


_fallback_warn_lock = threading.Lock()
_fallback_warned = False


def _warn_service_fallback_once(model: Any) -> None:
    """服务端返回 fallback=true（模型未加载，容器内 CPU 兜底）只告警一次。"""
    global _fallback_warned
    with _fallback_warn_lock:
        if _fallback_warned:
            logger.debug("TagGenerator 服务端仍处于 CPU 兜底状态 (model=%s)", model)
            return
        _fallback_warned = True
    logger.warning(
        "TagGenerator 服务端模型未加载，返回的是容器内 CPU 兜底标签 (model=%s)；"
        "请检查 tagger 容器日志（/health 的 model_loaded）",
        model,
    )


def _reset_fallback_warning() -> None:
    """仅供测试复位「fallback 只告警一次」的模块级标记。"""
    global _fallback_warned
    with _fallback_warn_lock:
        _fallback_warned = False


class HttpTagGeneratorClient:
    """``TagGeneratorPort`` 的 HTTP 实现（同步，供 worker 线程调用）。"""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        timeout_sec: Optional[float] = None,
        max_retries: Optional[int] = None,
        retry_delay_sec: Optional[float] = None,
        probe_timeout_sec: Optional[float] = None,
        failure_threshold: Optional[int] = None,
        cooldown_sec: Optional[float] = None,
    ):
        self.endpoint = endpoint or settings.TAGGER_ENDPOINT
        self._timeout_sec = (
            settings.TAGGER_TIMEOUT_SEC if timeout_sec is None else timeout_sec
        )
        self._max_retries = (
            settings.TAGGER_MAX_RETRIES if max_retries is None else max_retries
        )
        self._retry_delay_sec = (
            settings.TAGGER_RETRY_DELAY_SEC if retry_delay_sec is None else retry_delay_sec
        )
        self._probe_timeout_sec = (
            settings.TAGGER_PROBE_TIMEOUT_SEC
            if probe_timeout_sec is None
            else probe_timeout_sec
        )
        self._cooldown = _FailureCooldown(
            settings.TAGGER_FAILURE_THRESHOLD if failure_threshold is None else failure_threshold,
            settings.TAGGER_COOLDOWN_SEC if cooldown_sec is None else cooldown_sec,
        )
        # 构造只登记地址、不校验连通性；连通性由启动探活（probe）暴露
        logger.info(
            "TagGenerator HTTP 客户端配置完成（仅登记地址，不校验连通性）: %s "
            "(timeout=%ss, retries=%s)",
            self.endpoint,
            self._timeout_sec,
            self._max_retries,
        )

    # ── TagGeneratorPort ──────────────────────────────────────

    def extract_tags(
        self,
        text: str,
        num_tags: int = 5,
        diversity: float = 0.5,
    ) -> List[str]:
        if not text or not text.strip():
            return []

        if not self._cooldown.allow():
            raise ExternalServiceError(
                SERVICE_NAME, "服务连续失败处于熔断冷却期，跳过远程调用"
            )

        payload = {
            "text": text,
            "num_tags": int(num_tags),
            "diversity": float(diversity),
        }

        @retry(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_fixed(self._retry_delay_sec),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
            before_sleep=self._log_retry,
        )
        def _call() -> Any:
            response = get_sync_http_client().post(
                self.endpoint, json=payload, timeout=self._timeout_sec
            )
            response.raise_for_status()
            return response.json()

        try:
            data = _call()
            tags = self._parse_tags(data, num_tags)
        except Exception as exc:
            self._cooldown.record_failure()
            detail = self._describe_failure(exc)
            logger.warning("TagGenerator 调用失败 (%s): %s", self.endpoint, detail)
            raise ExternalServiceError(SERVICE_NAME, detail) from exc

        self._cooldown.record_success()
        return tags

    def probe(self, timeout_sec: Optional[float] = None) -> Dict[str, Any]:
        """单次最小请求探活：不重试、短超时，失败抛 ``ExternalServiceError``。

        供启动阶段使用——地址写错 / 容器没起 / 路径不对会在启动日志里暴露，
        而不是推迟到首次文档上传。返回服务端元信息（model / fallback）。
        """
        payload = {"text": "探活", "num_tags": 1, "diversity": 0.5}
        effective_timeout = (
            self._probe_timeout_sec if timeout_sec is None else timeout_sec
        )
        try:
            response = get_sync_http_client().post(
                self.endpoint, json=payload, timeout=effective_timeout
            )
            response.raise_for_status()
            data = response.json()
            self._parse_tags(data, 1)
        except Exception as exc:
            raise ExternalServiceError(
                SERVICE_NAME, f"探活失败: {self._describe_failure(exc)}"
            ) from exc

        self._cooldown.record_success()
        info = {
            "endpoint": self.endpoint,
            "model": data.get("model") if isinstance(data, dict) else None,
            "fallback": bool(data.get("fallback")) if isinstance(data, dict) else False,
            "processing_time_ms": (
                data.get("processing_time_ms") if isinstance(data, dict) else None
            ),
        }
        return info

    # ── 内部 ──────────────────────────────────────────────────

    def _parse_tags(self, data: Any, num_tags: int) -> List[str]:
        """校验并清洗响应标签：非 list / 元素为空视为契约不符（ValueError）。

        容器不保证去重（``set`` 顺序不定），这里按出现顺序去重后截断到 num_tags。
        """
        if not isinstance(data, dict):
            raise ValueError(f"响应不是 JSON 对象: {type(data).__name__}")
        raw_tags = data.get("tags")
        if not isinstance(raw_tags, list):
            raise ValueError(f"响应缺少 tags 列表: {str(data)[:200]}")

        if data.get("fallback"):
            _warn_service_fallback_once(data.get("model"))

        tags: List[str] = []
        seen = set()
        for item in raw_tags:
            tag = str(item).strip() if item is not None else ""
            if not tag or tag in seen:
                continue
            seen.add(tag)
            tags.append(tag)
        return tags[: max(0, int(num_tags))]

    def _log_retry(self, retry_state) -> None:
        outcome = retry_state.outcome
        exc = outcome.exception() if outcome is not None else None
        logger.warning(
            "TagGenerator 第 %s/%s 次调用失败，%ss 后重试: %s",
            retry_state.attempt_number,
            self._max_retries,
            self._retry_delay_sec,
            self._describe_failure(exc) if exc else "unknown",
        )

    @staticmethod
    def _describe_failure(exc: BaseException) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            body = (exc.response.text or "")[:_BODY_SNIPPET_LIMIT]
            return f"HTTP {exc.response.status_code} from {exc.request.url}: {body}"
        return f"{type(exc).__name__}: {exc}"
