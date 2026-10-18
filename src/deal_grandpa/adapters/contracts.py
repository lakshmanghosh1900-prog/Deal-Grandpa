from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from deal_grandpa.adapters.fixture_source import LoadedFixture


class SourceAdapterError(RuntimeError):
    """A source could not provide a safe observation batch."""


@runtime_checkable
class SourceAdapter(Protocol):
    """Contract for an approved source adapter.

    Implementations must return normalized offers plus the source registry
    metadata consumed by the existing G1-G7 pipeline. They must not return
    guessed values or silently substitute another source after failure.
    """

    name: str

    def load(self, fault_mode: str | None = None) -> "LoadedFixture":
        """Return a validated, permission-filtered observation batch."""


def fixture_adapter(path: str | Path) -> SourceAdapter:
    """Build the explicitly offline default adapter."""
    from deal_grandpa.adapters.fixture_source import FixtureSource

    return FixtureSource(path)
