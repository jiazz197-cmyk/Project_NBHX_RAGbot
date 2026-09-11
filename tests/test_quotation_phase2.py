from __future__ import annotations

from typing import Any

from app.usecases.quotation.execute_phase2 import (
    _U8_MAX_DEPTH,
    ExecuteQuotationPhase2Command,
    ExecuteQuotationPhase2UseCase,
)


class _FakeU8Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self) -> dict[str, Any]:
        return self._payload


class _FakeU8Query:
    def __init__(self, by_parent: dict[str, dict[str, Any]]) -> None:
        self._by_parent = by_parent
        self.commands: list[Any] = []

    def run(self, command: Any, cancel_checker: Any = None, user_key: Any = None) -> _FakeU8Response:
        self.commands.append(command)
        payload = self._by_parent.get(command.parent_inv_codes, {"total": 0, "items": []})
        return _FakeU8Response(payload)


def test_project_code_type_uses_shallow_then_deep_virtual_children():
    shallow_payload = {
        "total": 2,
        "items": [
            {
                "子件层级": 1,
                "子件名称": "虚拟件A",
                "材料编码（物料编码）": "VA001",
                "供应类型": "虚拟件",
                "累计用量": 2,
                "单价": 0,
                "总价": 0,
                "__root_inv_code": "60334P542",
                "__root_inv_name": "项目60334P542",
                "__parent_inv_code": "60334P542",
            },
            {
                "子件层级": 1,
                "子件名称": "外购件B",
                "材料编码（物料编码）": "PB002",
                "供应类型": "领用",
                "累计用量": 1,
                "单价": 100,
                "总价": 100,
                "__root_inv_code": "60334P542",
                "__root_inv_name": "项目60334P542",
                "__parent_inv_code": "60334P542",
            },
        ],
    }
    deep_payload = {
        "total": 1,
        "items": [
            {
                "子件层级": 2,
                "子件名称": "子件A1",
                "材料编码（物料编码）": "SA001",
                "累计用量": 2,
                "单价": 50,
                "总价": 100,
                "__root_inv_code": "VA001",
                "__root_inv_name": "虚拟件A",
                "__parent_inv_code": "VA001",
            }
        ],
    }

    fake_query = _FakeU8Query(
        {
            "60334P542": shallow_payload,
            "VA001": deep_payload,
        }
    )

    result = ExecuteQuotationPhase2UseCase(fake_query).execute(
        ExecuteQuotationPhase2Command(
            pdm_partids=["60334P542"],
            approved_partids=["60334P542"],
            manual_partid_types={"60334P542": "60334P542"},
            code_type="project",
        )
    )

    assert len(fake_query.commands) == 2
    assert fake_query.commands[0].parent_inv_codes == "60334P542"
    assert fake_query.commands[0].max_depth == 1
    assert fake_query.commands[1].parent_inv_codes == "VA001"
    assert fake_query.commands[1].max_depth == _U8_MAX_DEPTH

    assert result.u8_result_by_type["total"] == 2
    groups = {g["type"]: g for g in result.u8_result_by_type["items"]}
    assert "虚拟件A" in groups
    assert "外购件B" in groups
    assert groups["虚拟件A"]["total"] == 1
    assert groups["虚拟件A"]["summary_only"] is False
    assert groups["外购件B"]["total"] == 0
    assert groups["外购件B"]["summary_only"] is True
