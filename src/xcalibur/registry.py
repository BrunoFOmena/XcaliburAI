"""Domain plugin discovery.

A domain package exposes a :class:`DomainPlugin` through the ``xcalibur.domains``
entry-point group. Installing a package is all it takes to add a domain; the
core never imports domain code by name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path

from xcalibur.agents.base import AgentSpec
from xcalibur.agents.tools import Tool
from xcalibur.manifest import DomainManifest

ENTRY_POINT_GROUP = "xcalibur.domains"


@dataclass(frozen=True)
class DomainPlugin:
    """Everything the core needs to know about one domain package."""

    manifest: DomainManifest
    root: Path
    """Package directory; the manifest's workflow entrypoint resolves against it."""
    agents: tuple[AgentSpec, ...] = field(default_factory=tuple)
    tools: tuple[Tool, ...] = field(default_factory=tuple)
    """Domain tools; agents see them as ``<domain>__<tool>``."""

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def workflow_path(self) -> Path:
        return self.root / self.manifest.workflow.entrypoint


class DomainRegistry:
    def __init__(self) -> None:
        self._domains: dict[str, DomainPlugin] = {}

    def register(self, plugin: DomainPlugin) -> None:
        if plugin.name in self._domains:
            raise ValueError(f"domain '{plugin.name}' is already registered")
        self._domains[plugin.name] = plugin

    def get(self, name: str) -> DomainPlugin:
        try:
            return self._domains[name]
        except KeyError:
            known = ", ".join(sorted(self._domains)) or "none"
            raise KeyError(f"unknown domain '{name}' (installed: {known})") from None

    def names(self) -> list[str]:
        return sorted(self._domains)

    def __iter__(self):
        return iter(self._domains.values())

    def __len__(self) -> int:
        return len(self._domains)

    @classmethod
    def discover(cls) -> DomainRegistry:
        """Build a registry from every installed ``xcalibur.domains`` entry point."""
        registry = cls()
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            plugin = ep.load()
            if callable(plugin):
                plugin = plugin()
            if not isinstance(plugin, DomainPlugin):
                raise TypeError(f"entry point '{ep.name}' did not provide a DomainPlugin")
            registry.register(plugin)
        return registry
