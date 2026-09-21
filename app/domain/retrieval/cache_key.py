"""检索查询级缓存的归一化与键构造（issue #21，纯逻辑，无 IO）。

键设计（详见 ``docs/retrieval-cache.md``）::

    retrieval:cache:{scope}:{collection}:v{version}:{sha256_32}
    retrieval:emb:{model}:{sha256_32}
    retrieval:ver:{collection}

约定与理由：

1. **归一化 = strip + 空白折叠**（含全角空格 U+3000 与不换行空格 U+00A0），
   **不做大小写折叠**——中文为主，且 ``SAP`` / ``sap`` 这类实体大小写有语义，
   折叠后还会让两段不同文本共用同一条查询嵌入。
2. **关键词进键的是「集合」而非「列表」**：与检索侧用同一套
   :func:`~app.domain.retrieval.ranking.normalize_keywords`（同上限）清洗，
   再排序编码。截断子集不同 ⇒ 排序结果必不同，因此不会把两组不同关键词
   映射成同一个键（只提升命中率，不引入错误命中）。
3. **键只含 collection，不含用户身份**：集合白名单（ACL）在路由层先于缓存
   执行；若将来出现「按用户过滤的检索结果」，必须重审键设计（issue #21 要求）。
4. 问题文本只以摘要形式进键，原始文本不落键名。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Optional, Tuple

from app.domain.retrieval.ranking import (
    DEFAULT_KEYWORD_MAX_LEN,
    DEFAULT_MAX_KEYWORDS,
    normalize_keywords,
)

# 键前缀（Redis 里统一落在 retrieval:* 命名空间，便于整段清理）
CACHE_PREFIX_RESULT = "retrieval:cache"
CACHE_PREFIX_EMBEDDING = "retrieval:emb"
CACHE_PREFIX_VERSION = "retrieval:ver"

# 路径标识：chunks = 显式 top_k 的结构化 chunks 路径；legacy = 旧 get_response /
# get_charts 整表路径。两条路径的键必须分开，否则会互相污染（代价与形状都不同）。
PATH_CHUNKS = "chunks"
PATH_LEGACY = "legacy"

# Unicode 空白（含 \u3000 全角空格 / \xa0 NBSP）折叠成单个半角空格
_WHITESPACE_RE = re.compile(r"[\s\u3000\xa0]+")

# 摘要长度：32 hex（128bit）足够，且键名短
_DIGEST_LEN = 32


def normalize_question(text: Any) -> str:
    """问题归一化：str 化 → 空白折叠为单空格 → strip。空输入返回空串。"""
    if text is None:
        return ""
    return _WHITESPACE_RE.sub(" ", str(text)).strip()


def keywords_fingerprint(
    raw: Any,
    *,
    max_count: int = DEFAULT_MAX_KEYWORDS,
    max_len: int = DEFAULT_KEYWORD_MAX_LEN,
) -> Tuple[str, ...]:
    """关键词的规范编码：先按检索侧规则清洗，再排序为稳定元组。

    排序只是「集合」的规范表示：调用方（``HybridRetrieverAdapter``）消费的
    正是同一函数清洗出的集合，因此键与真实检索输入逐项一致。
    """
    return tuple(sorted(normalize_keywords(raw, max_count=max_count, max_len=max_len)))


def _digest(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_DIGEST_LEN]


def retrieval_cache_key(
    *,
    scope: str,
    collection: str,
    path: str,
    question: Any,
    top_k: Optional[int] = None,
    keywords: Iterable[str] = (),
    version: int = 0,
    prefix: str = CACHE_PREFIX_RESULT,
) -> str:
    """检索结果缓存键。

    ``scope`` = 端点域（``db`` / ``excel``）；``path`` = :data:`PATH_CHUNKS` /
    :data:`PATH_LEGACY`；``version`` = 集合版本号（写入端自增，实现失效）。
    ``keywords`` 应为 :func:`keywords_fingerprint` 的结果（仅混合检索开启时传入）。
    """
    digest = _digest(
        {
            "q": normalize_question(question),
            "path": path,
            "top_k": int(top_k) if top_k else None,
            "kw": list(keywords),
        }
    )
    return f"{prefix}:{scope}:{collection}:v{int(version)}:{digest}"


def embedding_cache_key(
    *, model: str, text: Any, prefix: str = CACHE_PREFIX_EMBEDDING
) -> str:
    """查询嵌入缓存键：同一段归一化文本 + 同一模型 → 同一向量。"""
    return f"{prefix}:{model}:{_digest({'t': normalize_question(text)})}"


def collection_version_key(collection: str, prefix: str = CACHE_PREFIX_VERSION) -> str:
    """集合版本号键（写入端 INCR，读端 GET；无 TTL，按集合数量有界）。"""
    return f"{prefix}:{collection}"
