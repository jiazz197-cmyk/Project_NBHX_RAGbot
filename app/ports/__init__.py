"""Application ports package.

Import explicitly from subpackages:
- ``app.ports.dto`` — dataclasses only (read models / command results).
- ``app.ports.contracts`` — cross-cutting ``Protocol`` definitions.
- ``app.ports.outbound`` — business-line outbound ports (depend on dto + contracts only).

This module does not re-export symbols; use paths above to keep layering obvious.
"""

__all__: list[str] = []
