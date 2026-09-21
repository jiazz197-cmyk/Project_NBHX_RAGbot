"""集合名 → PGVector 物理表名映射，以及懒建表容错（issue #16 抽公共）。

PGVector 内部把物理表存为 ``data_<逻辑集合名>``（llama-index
``get_data_model``: ``tablename = "data_%s" % index_name``）。物理表是
**懒建表**——首次 upsert 才创建，因此「表不存在」是正常中间态，不是错误。

原实现内联在 ``app/adapters/knowledge/metadata.py``；issue #16 的字面检索
适配器需要同一套映射与容错，故抽到本模块作单一事实来源。
"""

from __future__ import annotations

import re

from sqlalchemy.exc import ProgrammingError

from app.core.exceptions import ValidationError

# collection 直接拼进物理表名 data_<collection>，必须白名单式校验防注入
_COLLECTION_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def physical_table(collection: str) -> str:
    """逻辑集合名 → 物理表名；非法集合名抛 ``ValidationError``。"""
    if not _COLLECTION_NAME_PATTERN.match(collection or ""):
        raise ValidationError(f"非法集合名: {collection}")
    return f"data_{collection}"


def is_missing_table(exc: ProgrammingError) -> bool:
    """判断异常是否为「表不存在」（懒建表的正常中间态）。"""
    msg = f"{exc.orig} {exc}".lower()
    return "does not exist" in msg or "undefinedtable" in msg
