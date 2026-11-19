"""Supervisor: decides which domain (and then which agent) handles a message.

Routing is a strategy behind the :class:`Router` protocol. The deterministic
:class:`KeywordRouter` ships first so the system is testable without any LLM;
an LLM-backed router plugs into the same seam later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from xcalibur.registry import DomainRegistry


@dataclass(frozen=True)
class RouteDecision:
    domain: str | None
    confidence: float
    reason: str


class Router(Protocol):
    def route(self, message: str, registry: DomainRegistry) -> RouteDecision: ...


class KeywordRouter:
    """Scores each domain by how many of its manifest keywords appear in the message."""

    def route(self, message: str, registry: DomainRegistry) -> RouteDecision:
        tokens = set(re.findall(r"[\w-]+", message.lower()))
        best_name: str | None = None
        best_hits = 0
        best_total = 1
        for plugin in registry:
            keywords = {k.lower() for k in plugin.manifest.keywords}
            hits = len(keywords & tokens)
            if hits > best_hits:
                best_name, best_hits, best_total = plugin.name, hits, len(keywords)
        if best_name is None:
            return RouteDecision(None, 0.0, "no domain keyword matched")
        return RouteDecision(
            best_name, best_hits / best_total, f"matched {best_hits} keyword(s) of '{best_name}'"
        )


class Supervisor:
    def __init__(self, registry: DomainRegistry, router: Router | None = None) -> None:
        self.registry = registry
        self.router = router or KeywordRouter()

    def route(self, message: str) -> RouteDecision:
        return self.router.route(message, self.registry)
