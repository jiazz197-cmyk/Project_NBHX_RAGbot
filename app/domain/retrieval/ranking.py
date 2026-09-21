"""检索排序领域规则：关键词清洗、去重键、RRF 融合（纯逻辑，无 IO）。

issue #16（路线 1：PG 全文 + RRF）把「dense 向量召回」与「字面/关键词召回」
两路结果合成一份候选池。两路的分数量纲不可比（余弦相似度 vs 命中词数），
因此不能用分数相加，只能用**名次**融合——本模块实现该规则。

注意：本模块只依赖 stdlib（domain 层禁 import 外层），
``rrf_fuse`` 的输入顺序即路径优先级，调用方保证稳定（dense 在前、lexical 在后）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

# 两路召回的名字（融合溯源用）
PATH_DENSE = "dense"
PATH_LEXICAL = "lexical"

# 默认参数：与 app.core.config 的 RETRIEVAL_* 默认值保持一致
DEFAULT_RRF_K = 60
DEFAULT_MAX_KEYWORDS = 12
DEFAULT_KEYWORD_MAX_LEN = 64


def normalize_keywords(
    raw: Any,
    *,
    max_count: int = DEFAULT_MAX_KEYWORDS,
    max_len: int = DEFAULT_KEYWORD_MAX_LEN,
) -> list[str]:
    """清洗改写步骤产出的关键词。

    规则：非字符串化 → strip → 丢空串 → 丢超长项 → 保序去重 → 封顶条数。
    返回空列表表示「本请求没有可用关键词」，调用方据此跳过稀疏检索路。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        candidates: Iterable[Any] = [raw]
    elif isinstance(raw, (list, tuple, set)):
        candidates = raw
    else:
        candidates = [raw]

    out: list[str] = []
    for item in candidates:
        if item is None:
            continue
        text = str(item).strip()
        if not text:
            continue
        if max_len > 0 and len(text) > max_len:
            continue
        if text in out:
            continue
        out.append(text)
        if max_count > 0 and len(out) >= max_count:
            break
    return out


def dedupe_key(node_id: Optional[str], content: Optional[str]) -> str:
    """两路结果的去重键：优先 chunk 的 node_id，缺失时退回内容哈希。"""
    if node_id:
        return str(node_id)
    digest = hashlib.sha1((content or "").encode("utf-8")).hexdigest()
    return f"sha1:{digest}"


@dataclass(frozen=True)
class FusedItem:
    """融合后的一条候选（只带身份与名次，正文由调用方按 key 取回）。"""

    key: str
    score: float
    paths: Tuple[str, ...]
    dense_rank: Optional[int] = None
    lexical_rank: Optional[int] = None


def rrf_fuse(
    rankings: Mapping[str, Sequence[str]],
    *,
    k: int = DEFAULT_RRF_K,
) -> list[FusedItem]:
    """Reciprocal Rank Fusion：``score = Σ 1/(k + rank)``（rank 从 1 起）。

    ``rankings`` 的插入顺序即路径优先级（dense 在前），仅影响**同分**时的
    兜底顺序与 ``FusedItem.paths`` 的排列；分数本身与顺序无关。

    排序：score desc → dense 名次 asc → lexical 名次 asc → key asc，
    因此结果完全确定（可复现、可断言）。缺某一路的候选该路名次为 None，
    排在有两路名次的候选之后。
    """
    safe_k = k if k and k > 0 else DEFAULT_RRF_K
    scores: Dict[str, float] = {}
    ranks: Dict[str, Dict[str, int]] = {}
    path_order: list[str] = []

    for path_name, keys in rankings.items():
        if path_name not in path_order:
            path_order.append(path_name)
        for position, key in enumerate(keys or (), start=1):
            if key is None:
                continue
            key = str(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (safe_k + position)
            ranks.setdefault(key, {})[path_name] = position

    missing = float("inf")
    items = [
        FusedItem(
            key=key,
            score=score,
            paths=tuple(name for name in path_order if name in ranks[key]),
            dense_rank=ranks[key].get(PATH_DENSE),
            lexical_rank=ranks[key].get(PATH_LEXICAL),
        )
        for key, score in scores.items()
    ]
    items.sort(
        key=lambda item: (
            -item.score,
            item.dense_rank if item.dense_rank is not None else missing,
            item.lexical_rank if item.lexical_rank is not None else missing,
            item.key,
        )
    )
    return items
