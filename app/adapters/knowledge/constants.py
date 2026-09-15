"""Table names for the knowledge domain (vector collections)."""

# 语义集合名（逻辑表名，供向量写入 / 检索 API 使用）
KNOWLEDGE_COLLECTION_NAME = "knowledge_chunks"
# PGVector 物理表名：data_<逻辑表名>（由 PGVectorStore 自动加 data_ 前缀）
KNOWLEDGE_CHUNKS_TABLE = "data_knowledge_chunks"

# Excel 类数据库集合（sheet 名写入 chunk metadata）
EXCEL_DB_COLLECTION_NAME = "excel_db_chunks"
EXCEL_DB_CHUNKS_TABLE = "data_excel_db_chunks"
