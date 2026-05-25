from __future__ import annotations
from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMAdapter(Protocol):
    name: str

    def complete(self, system: str, user: str, **opts) -> str: ...
