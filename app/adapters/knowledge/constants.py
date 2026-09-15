"""Table names for the knowledge domain (vector collections)."""

from app.domain.knowledge.collections import (  # noqa: F401  (re-export)
    EXCEL_DB_COLLECTION_NAME,
    KNOWLEDGE_COLLECTION_NAME,
)

# PGVector 物理表名：data_<逻辑表名>（由 PGVectorStore 自动加 data_ 前缀）
KNOWLEDGE_CHUNKS_TABLE = "data_knowledge_chunks"
EXCEL_DB_CHUNKS_TABLE = "data_excel_db_chunks"
