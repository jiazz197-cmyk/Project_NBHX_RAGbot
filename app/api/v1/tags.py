"""OpenAPI tag strings and tag metadata; single source of truth for v1."""
from __future__ import annotations

# FastAPI `tags=[...]` on routes — keep stable for generated clients
Tag = str

AUTHENTICATION: Tag = "Authentication"
EXAMPLE: Tag = "Example"
FILE_MANAGEMENT: Tag = "File Management"
QUOTATION_GENERATION: Tag = "Quotation Generation"
DOCUMENT_PROCESSING: Tag = "Document Processing"
IMAGE_PROCESSING: Tag = "Image Processing"
PDF_PROCESSING: Tag = "PDF Processing"
RETRIEVER: Tag = "Retriever"
CHAT_SUMMARY: Tag = "Chat Summary"
CLOSING_FORM: Tag = "Closing Form"
CONTEXT_COMPRESSION: Tag = "Context Compression"
SQLSERVER_QUERY: Tag = "SQLServer Query"
OCR: Tag = "OCR"

# Descriptions for FastAPI `openapi_tags` in main app
OPENAPI_TAG_METADATA: list[dict] = [
    {
        "name": AUTHENTICATION,
        "description": "Login, registration, and current user profile.",
    },
    {
        "name": EXAMPLE,
        "description": "Health-style examples for smoke tests.",
    },
    {
        "name": FILE_MANAGEMENT,
        "description": "File upload, download, and listing backed by MinIO and DB records.",
    },
    {
        "name": QUOTATION_GENERATION,
        "description": "Quotation tasks and file handling.",
    },
    {
        "name": DOCUMENT_PROCESSING,
        "description": "Asynchronous document ingestion, task status, and task progress WebSocket.",
    },
    {
        "name": IMAGE_PROCESSING,
        "description": "Legacy URL prefix for async image upload (prefer OCR tag and /ocr paths).",
    },
    {
        "name": PDF_PROCESSING,
        "description": "Legacy URL prefix for PDF to image (prefer OCR tag and /ocr paths).",
    },
    {
        "name": OCR,
        "description": "OCR pipeline: image upload and PDF to images under a single /ocr prefix.",
    },
    {
        "name": RETRIEVER,
        "description": "RAG / DB / Excel and chart-style retrieval entrypoints.",
    },
    {
        "name": CHAT_SUMMARY,
        "description": "Chat summary storage and read APIs.",
    },
    {
        "name": CLOSING_FORM,
        "description": "Smart scale order form submission and approval.",
    },
    {
        "name": CONTEXT_COMPRESSION,
        "description": "Context compression for long conversations.",
    },
    {
        "name": SQLSERVER_QUERY,
        "description": "U8 / PDM and other SQL Server backed queries.",
    },
]
