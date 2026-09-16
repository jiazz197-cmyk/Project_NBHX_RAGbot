"""Use case: cooperatively cancel a running chat generation."""

from __future__ import annotations

from dataclasses import dataclass

from app.ports.outbound.chat import ChatOrchestratorPort


@dataclass
class StopMessageCommand:
    task_id: str
    user_id: str


class StopMessageUseCase:
    def __init__(self, orchestrator: ChatOrchestratorPort):
        self._orchestrator = orchestrator

    async def execute(self, cmd: StopMessageCommand) -> bool:
        return await self._orchestrator.stop(cmd.task_id, cmd.user_id)
