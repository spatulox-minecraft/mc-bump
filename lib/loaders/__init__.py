"""Loader registry.

Adding NeoForge is: write neoforge.py against base.Loader, import it, add one
line to LOADERS. Nothing outside this package needs to know it exists.
"""

from __future__ import annotations

from ..common import Failure
from .base import Loader, Resolved, Rung
from .fabric import FabricLoader

LOADERS: dict[str, type[Loader]] = {
    FabricLoader.name: FabricLoader,
}

__all__ = ["Loader", "Resolved", "Rung", "get_loader", "LOADERS"]


def get_loader(name: str) -> Loader:
    try:
        return LOADERS[name]()
    except KeyError:
        raise Failure(
            f"loader: '{name}' is not supported. Known loaders: "
            f"{', '.join(sorted(LOADERS))}."
        ) from None
