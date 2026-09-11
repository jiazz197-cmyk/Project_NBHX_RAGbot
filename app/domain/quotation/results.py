"""DTOs for quotation phase execution results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class Phase1Result:
    """Phase 1 output: keywords + PDM response + extracted PARTIDs."""

    keywords_payload: Dict[str, Any]
    pdm_result: Dict[str, Any]
    pdm_partids: List[str]
    temp_image_minio_path: str
    temp_image_url: str
    raw_extracted_info: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "keywords_payload": self.keywords_payload,
            "pdm_result": self.pdm_result,
            "pdm_partids": self.pdm_partids,
            "temp_image_minio_path": self.temp_image_minio_path,
            "temp_image_url": self.temp_image_url,
            "raw_extracted_info": self.raw_extracted_info,
        }


@dataclass
class Phase2Result:
    """Phase 2 output: final U8 BOM inventory response."""

    u8_result: Dict[str, Any]
    u8_result_by_type: Dict[str, Any] = field(default_factory=dict)
    # 故障隔离时被跳过的根编码（DB 层超时/锁/死锁），供任务层向用户上报部分数据缺失。
    failed_root_codes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "u8_result": self.u8_result,
            "u8_result_by_type": self.u8_result_by_type,
            "failed_root_codes": self.failed_root_codes,
        }
