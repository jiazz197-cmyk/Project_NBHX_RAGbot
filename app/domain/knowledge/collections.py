"""Knowledge domain: logical vector collection names (domain concepts)."""

from __future__ import annotations

# 文档知识集合（向量写入 data_<name>，由 PGVectorStore 加 data_ 前缀）
KNOWLEDGE_COLLECTION_NAME = "knowledge_chunks"

# Excel 类数据库集合（sheet 名写入 chunk metadata）
EXCEL_DB_COLLECTION_NAME = "excel_db_chunks"
