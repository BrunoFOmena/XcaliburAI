"""Test-wide guards."""

import socket

import pytest


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No test may reach the network (LLM tests use a simulated HTTP transport)."""

    def blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in tests")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
