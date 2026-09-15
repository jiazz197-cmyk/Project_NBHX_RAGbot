"""Knowledge domain rules for upload: file-type whitelist and conflict semantics."""

from __future__ import annotations

# 文档知识上传允许的扩展名（xlsx/xls 显式拒绝，提示走 excel-db 接口）
DOCUMENT_ALLOWED_EXTENSIONS = frozenset(
    {"txt", "md", "pdf", "doc", "docx", "ppt", "pptx", "html", "json"}
)

# Excel 类数据库上传允许的扩展名
EXCEL_DB_ALLOWED_EXTENSIONS = frozenset({"xlsx", "xls"})

# 同名冲突处理策略
CONFLICT_REPLACE = "replace"
CONFLICT_APPEND = "append"
VALID_ON_CONFLICT_VALUES = frozenset({CONFLICT_REPLACE, CONFLICT_APPEND})

# 固定分块参数（前端不暴露）
DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 50

# 大小上限（字节，超限 422）
MAX_DOCUMENT_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50MB，对齐全局 MAX_FILE_SIZE
MAX_EXCEL_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20MB，Excel 全表解析内存膨胀较大

# Excel 大文件保护：单文件总行数上限与总 chunk 数上限
MAX_EXCEL_TOTAL_ROWS = 50_000
MAX_EXCEL_TOTAL_CHUNKS = 20_000


def normalize_extension(file_name: str) -> str:
    """返回小写、去点的扩展名；无扩展名返回空串。"""
    name = (file_name or "").strip()
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def is_document_allowed(file_name: str) -> bool:
    return normalize_extension(file_name) in DOCUMENT_ALLOWED_EXTENSIONS


def is_excel_db_allowed(file_name: str) -> bool:
    return normalize_extension(file_name) in EXCEL_DB_ALLOWED_EXTENSIONS
