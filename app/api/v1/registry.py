"""Assemble the v1 APIRouter: canonical mounts first, then legacy path aliases."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    auth,
    chat_summary,
    closing_form,
    context_compression,
    document_processing,
    example,
    file_manager,
    image2url,
    pdf2image,
    quotation_generation,
    retriever,
    sqlserver_queries,
    websocket_notifier,
)
from app.api.v1 import prefixes as p
from app.api.v1 import tags as t


def _mount(
    out: APIRouter,
    subrouter: APIRouter,
    prefix: str,
    tags: list,
) -> None:
    out.include_router(subrouter, prefix=prefix, tags=tags)


def build_api_router() -> APIRouter:
    """Register all v1 sub-routers. Legacy prefixes are duplicate mounts, same handlers."""
    r = APIRouter()

    # Core (unchanged names)
    _mount(r, auth.router, p.AUTH, [t.AUTHENTICATION])
    _mount(r, example.router, p.EXAMPLE, [t.EXAMPLE])
    _mount(r, file_manager.router, p.FILES, [t.FILE_MANAGEMENT])
    _mount(r, quotation_generation.router, p.QUOTATION, [t.QUOTATION_GENERATION])

    # Document processing + task WebSocket (canonical)
    _mount(
        r,
        document_processing.router,
        p.DOCUMENT_TASKS,
        [t.DOCUMENT_PROCESSING],
    )
    _mount(
        r,
        websocket_notifier.router,
        p.DOCUMENT_TASKS,
        [t.DOCUMENT_PROCESSING],
    )

    # OCR: image + PDF under one prefix
    _mount(r, image2url.router, p.OCR, [t.OCR])
    _mount(r, pdf2image.router, p.OCR, [t.OCR])

    _mount(r, retriever.router, p.RETRIEVER, [t.RETRIEVER])
    _mount(r, chat_summary.router, p.CHAT_SUMMARY, [t.CHAT_SUMMARY])
    _mount(r, closing_form.router, p.CLOSING_FORM, [t.CLOSING_FORM])
    _mount(
        r,
        context_compression.router,
        p.CONTEXT_COMPRESSION,
        [t.CONTEXT_COMPRESSION],
    )
    _mount(r, sqlserver_queries.router, p.SQLSERVER, [t.SQLSERVER_QUERY])

    # --- Legacy path aliases (same router objects; keep old clients working) ---
    _mount(
        r,
        document_processing.router,
        p.DOCS_DEPRECATED,
        [t.DOCUMENT_PROCESSING],
    )
    _mount(
        r,
        websocket_notifier.router,
        p.DOCS_DEPRECATED,
        [t.DOCUMENT_PROCESSING],
    )
    _mount(
        r,
        image2url.router,
        p.IMAGE2URL_DEPRECATED,
        [t.IMAGE_PROCESSING],
    )
    _mount(
        r,
        pdf2image.router,
        p.PDF2IMAGE_DEPRECATED,
        [t.PDF_PROCESSING],
    )

    return r


api_router = build_api_router()
