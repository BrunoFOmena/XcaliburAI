"""Supervisor: decides which domain (and then which agent) handles a message.

Routing is a strategy behind the :class:`Router` protocol. The deterministic
:class:`KeywordRouter` works without any LLM; :class:`LLMRouter` asks the model and falls
back to keywords whenever the model fails or gives an unusable answer.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Protocol

from xcalibur.llm.base import LLMProvider, Message, ToolSpec
from xcalibur.llm.errors import LLMError
from xcalibur.registry import DomainRegistry

ROUTE_TOOL = "choose_domain"
NO_DOMAIN = "none"


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


class LLMRouter:
    """Asks the model to pick a domain through a single structured tool call."""

    def __init__(self, provider: LLMProvider, fallback: Router | None = None) -> None:
        self.provider = provider
        self.fallback = fallback or KeywordRouter()

    def route(self, message: str, registry: DomainRegistry) -> RouteDecision:
        names = registry.names()
        if not names:
            return RouteDecision(None, 0.0, "no domain packages installed")
        catalog = "\n".join(
            f"- {p.name}: {p.manifest.display_name}. {p.manifest.description.strip()} "
            f"Keywords: {', '.join(p.manifest.keywords)}"
            for p in registry
        )
        system = (
            "Route the user's message to the domain that should handle it. Call "
            f"{ROUTE_TOOL} exactly once; use '{NO_DOMAIN}' when no domain fits.\n"
            f"Domains:\n{catalog}"
        )
        tool = ToolSpec(
            name=ROUTE_TOOL,
            description="Record the routing decision.",
            input_schema={
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "enum": [*names, NO_DOMAIN]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string"},
                },
                "required": ["domain", "reason"],
                "additionalProperties": False,
            },
        )
        try:
            completion = self.provider.generate(
                [Message(role="system", content=system), Message(role="user", content=message)],
                [tool],
            )
        except LLMError as exc:
            return self._fallback(message, registry, f"LLM routing failed ({exc})")
        call = next((c for c in completion.tool_calls if c.name == ROUTE_TOOL), None)
        if call is None:
            return self._fallback(message, registry, "LLM gave no routing decision")
        domain = call.input.get("domain")
        if domain != NO_DOMAIN and domain not in names:
            return self._fallback(message, registry, f"LLM chose unknown domain {domain!r}")
        reason = str(call.input.get("reason") or "chosen by the LLM")
        confidence = _confidence(call.input.get("confidence"))
        return RouteDecision(None if domain == NO_DOMAIN else domain, confidence, reason)

    def _fallback(self, message: str, registry: DomainRegistry, why: str) -> RouteDecision:
        decision = self.fallback.route(message, registry)
        return RouteDecision(
            decision.domain, decision.confidence, f"{why}; keyword fallback: {decision.reason}"
        )


UNKNOWN_CONFIDENCE = 0.5


def _confidence(value: object) -> float:
    """The model's confidence clamped to [0, 1]; neutral when missing or not a number."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return UNKNOWN_CONFIDENCE
    return min(1.0, max(0.0, float(value)))


class Supervisor:
    def __init__(self, registry: DomainRegistry, router: Router | None = None) -> None:
        self.registry = registry
        self.router = router or KeywordRouter()

    def route(self, message: str) -> RouteDecision:
        return self.router.route(message, self.registry)
