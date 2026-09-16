"""Tag generation outbound port (external tagger service).

文档处理在 worker 线程里同步执行（``DocumentProcessingPipeline.process`` →
``DocumentProcessor.process_document``），所以契约是**同步**方法：调用方
（``app/adapters/doc_processing/text_splitter.TagGenerator``）在线程内直接调用，
不做 async 包装。

服务端契约（tagenerator 容器，``POST {TAGGER_ENDPOINT}``）::

    {"text": "...", "num_tags": 5, "diversity": 0.5}
    -> {"tags": [...], "model": "...", "processing_time_ms": 1.2, "fallback": false}

Port 保持最小：只暴露标签列表，元信息（model / fallback）由实现侧记日志，
调用方不需要感知。
"""

from __future__ import annotations

from typing import List, Protocol


class TagGeneratorPort(Protocol):
    """Abstraction for keyword/tag extraction backed by the tagger service."""

    def extract_tags(
        self,
        text: str,
        num_tags: int = 5,
        diversity: float = 0.5,
    ) -> List[str]:
        """Return up to ``num_tags`` tags for ``text``.

        空文本返回 ``[]``（不发起请求）。服务不可用时由实现抛出异常，
        由调用方决定降级策略（本地 CPU 兜底），Port 不隐藏失败。
        """
        ...
