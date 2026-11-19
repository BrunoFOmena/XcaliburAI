"""Provider errors with messages that are safe to show (they never contain a key)."""

from __future__ import annotations


class LLMError(RuntimeError):
    """Any failure talking to the LLM provider."""


class LLMConfigError(LLMError):
    """The provider is not usable as configured (missing key, SDK or model)."""


class LLMAuthError(LLMError):
    """The provider rejected the credentials or their permissions."""


class LLMRateLimitError(LLMError):
    """The provider's usage limits were hit and retries were exhausted."""


class LLMConnectionError(LLMError):
    """Network failure or timeout after the retries were exhausted."""


class LLMRefusalError(LLMError):
    """The model declined the request."""
