"""Pydantic models for U8/PDM SQLServer query API (shared by API routes and quotation pipeline)."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from pydantic import BaseModel, Field, field_validator

# 与 create_direct_u8_task 的 _MAX_PARTIDS 一致，防止 API 路径传入超大编码列表。
_MAX_PARENT_INV_CODES: int = 1500


class U8BomInventoryRequest(BaseModel):
    """U8 BOM + Inventory query request."""

    parent_inv_codes: str | List[str] = Field(
        ...,
        description="父件编码，支持字符串（逗号/空格分隔）或数组",
    )
    max_depth: int = Field(3, ge=1, le=50, description="递归最大深度")

    @field_validator("parent_inv_codes")
    @classmethod
    def _cap_parent_inv_codes(cls, v: str | List[str]) -> str | List[str]:
        """Cap the number of parent codes to avoid unbounded IN-clause / load.

        Mirrors the 1500-partid ceiling enforced on the direct-task path.
        """
        if isinstance(v, list):
            if len(v) > _MAX_PARENT_INV_CODES:
                raise ValueError(f"parent_inv_codes 最多支持 {_MAX_PARENT_INV_CODES} 个编码")
            return v
        # 字符串形式按分隔符拆分计数（与 split_parent_inv_codes 一致）
        codes = [c.strip() for c in re.split(r"[;,/|\s、，；]+", v) if c.strip()]
        if len(codes) > _MAX_PARENT_INV_CODES:
            raise ValueError(f"parent_inv_codes 最多支持 {_MAX_PARENT_INV_CODES} 个编码")
        return v


class PdmBomRequest(BaseModel):
    """PDM BOM_016 query request."""

    keywords: Any = Field(
        ...,
        description=(
            "仅支持两种结构化格式: 单个对象({type, attr})、"
            "对象列表(List[{type, attr}])"
        ),
    )


class QueryResponse(BaseModel):
    """Generic query response."""

    total: int
    items: List[Dict[str, Any]]
    components: List[Dict[str, Any]] = []
    # 故障隔离时被跳过的根编码（DB 层超时/锁/死锁），供调用方/用户感知部分数据缺失。
    failed_root_codes: List[str] = []
    partial: bool = False
