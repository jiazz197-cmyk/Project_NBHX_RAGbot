"""Quotation adapters.

API / create-task imports should use persistence types from this package only.
Phase execution factories live in ``app.adapters.quotation.deps`` to avoid pulling
optional deps (e.g. pdf2image) when only MinIO/repo adapters are needed.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, List

__all__ = [
    "MinioFileStorageAdapter",
    "ResultPayloadQuotationApprovalSelectionAdapter",
    "SqlAlchemyQuotationTaskRepoAdapter",
]

_EXPORTS = {
    "MinioFileStorageAdapter": ".persistence",
    "SqlAlchemyQuotationTaskRepoAdapter": ".persistence",
    "ResultPayloadQuotationApprovalSelectionAdapter": ".persistence",
}


def __getattr__(name: str) -> Any:
    mod = _EXPORTS.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(mod, __name__), name)


def __dir__() -> List[str]:
    return sorted({*__all__, *globals().keys()})
