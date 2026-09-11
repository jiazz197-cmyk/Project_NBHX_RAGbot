"""Shared helpers for quotation use-cases."""

from __future__ import annotations

import dataclasses
from typing import Any, Dict


def response_to_dict(response: Any) -> Dict[str, Any]:
    """Serialize a query-response-like object to a dict.

    Handles plain dataclasses (the port-level ``SqlserverQueryResultDTO``) and
    pydantic models (v2 ``model_dump`` / v1 ``dict``).
    """
    if dataclasses.is_dataclass(response) and not isinstance(response, type):
        return {f.name: getattr(response, f.name) for f in dataclasses.fields(response)}
    dumper = getattr(response, "model_dump", None)
    if callable(dumper):
        return dumper()
    return response.dict()  # type: ignore[attr-defined]
