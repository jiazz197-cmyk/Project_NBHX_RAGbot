"""SQLServer query DTOs (pure dataclass, no Pydantic coupling)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PdmBomCommand:
    """Command for querying PDM BOM data."""

    keywords: Any = None


@dataclass
class PdmMatchCommand:
    """Command for PDM part matching (four-channel recall + scoring)."""

    keywords: Any = None


@dataclass
class U8BomInventoryCommand:
    """Command for querying U8 BOM inventory."""

    parent_inv_codes: str | List[str] = ""
    max_depth: int = 3


@dataclass
class SqlserverQueryResultDTO:
    """U8/PDM query result DTO (port-level; web layer maps to QueryResponse).

    Driven adapters return this pure dataclass so they no longer depend on the
    web-layer pydantic ``QueryResponse``. The API route maps it back to the
    response model.
    """

    total: int = 0
    items: List[Dict[str, Any]] = field(default_factory=list)
    components: List[Dict[str, Any]] = field(default_factory=list)
    failed_root_codes: List[str] = field(default_factory=list)
    partial: bool = False
