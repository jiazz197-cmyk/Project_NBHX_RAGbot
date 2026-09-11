"""SQL Server query use cases."""

from __future__ import annotations

from typing import Any, Optional

from app.ports.outbound.sqlserver_queries import PdmBomQueryPort, PdmMatchQueryPort, U8BomInventoryQueryPort
from app.ports.dto.sqlserver_queries import PdmBomCommand, PdmMatchCommand, U8BomInventoryCommand


class RunU8BomInventoryQueryUseCase:
    def __init__(self, port: U8BomInventoryQueryPort):
        self._port = port

    def execute(
        self, payload: U8BomInventoryCommand, *, user_key: Optional[str] = None
    ) -> Any:
        return self._port.run(payload, user_key=user_key)


class RunPdmBomQueryUseCase:
    def __init__(self, port: PdmBomQueryPort):
        self._port = port

    def execute(self, payload: PdmBomCommand) -> Any:
        return self._port.run(payload)


class RunPdmMatchQueryUseCase:
    def __init__(self, port: PdmMatchQueryPort):
        self._port = port

    def execute(self, payload: PdmMatchCommand) -> Any:
        return self._port.run(payload)
