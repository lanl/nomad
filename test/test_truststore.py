from __future__ import annotations

import sys
import types

import httpx

from nomad import truststore


def test_configure_huggingface_http_uses_httpx_client_factory(monkeypatch):
    fake_huggingface_hub = types.ModuleType("huggingface_hub")
    calls = {}

    def set_client_factory(factory):
        calls["factory"] = factory

    fake_huggingface_hub.set_client_factory = set_client_factory
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_huggingface_hub)

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.delenv("NO_PROXY", raising=False)

    truststore.configure_huggingface_http()

    client = calls["factory"]()
    try:
        assert isinstance(client, httpx.Client)
        assert client.follow_redirects is True
        assert any(
            getattr(pattern, "pattern", None) == "https://"
            and isinstance(transport, httpx.HTTPTransport)
            for pattern, transport in client._mounts.items()
        )
    finally:
        client.close()
