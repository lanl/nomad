from __future__ import annotations

import ssl
import sys
import types

import httpx
import requests
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.mcp_config import RemoteMCPServer

from nomad import hub, truststore
from nomad.gateway import upstream


def test_requests_adapter_uses_truststore_context_when_verifying(monkeypatch):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    monkeypatch.setattr(truststore, "ssl_context", lambda: context)
    adapter = truststore.TruststoreHTTPAdapter()
    request = requests.Request("GET", "https://example.test").prepare()

    _, pool_kwargs = adapter.build_connection_pool_key_attributes(request, verify=True)

    assert pool_kwargs["ssl_context"] is context


def test_requests_adapter_preserves_explicit_verify_setting(tmp_path):
    adapter = truststore.TruststoreHTTPAdapter()
    request = requests.Request("GET", "https://example.test").prepare()
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.touch()

    _, disabled_kwargs = adapter.build_connection_pool_key_attributes(
        request, verify=False
    )
    _, bundle_kwargs = adapter.build_connection_pool_key_attributes(
        request, verify=str(ca_bundle)
    )

    assert "ssl_context" not in disabled_kwargs
    assert "ssl_context" not in bundle_kwargs
    assert bundle_kwargs["ca_certs"] == str(ca_bundle)


def test_configure_huggingface_http_uses_httpx_client_factory(monkeypatch):
    fake_huggingface_hub = types.ModuleType("huggingface_hub")
    calls = {}

    def set_client_factory(factory):
        calls["factory"] = factory

    def set_async_client_factory(factory):
        calls["async_factory"] = factory

    fake_huggingface_hub.set_client_factory = set_client_factory
    fake_huggingface_hub.set_async_client_factory = set_async_client_factory
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_huggingface_hub)

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.delenv("NO_PROXY", raising=False)
    contexts = []

    def make_context():
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        contexts.append(context)
        return context

    monkeypatch.setattr(truststore, "ssl_context", make_context)

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
        assert len(contexts) >= 2
    finally:
        client.close()

    assert calls["async_factory"] is truststore._huggingface_async_httpx_client_factory


async def test_huggingface_async_client_preserves_hooks_and_environment_proxy(
    monkeypatch,
):
    from huggingface_hub.utils._http import (
        async_hf_request_event_hook,
        async_hf_response_event_hook,
    )

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    monkeypatch.setattr(truststore, "ssl_context", lambda: context)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.delenv("NO_PROXY", raising=False)

    client = truststore._huggingface_async_httpx_client_factory()
    try:
        assert client._transport._pool._ssl_context is context
        assert client._event_hooks == {
            "request": [async_hf_request_event_hook],
            "response": [async_hf_response_event_hook],
        }
        assert any(
            getattr(pattern, "pattern", None) == "https://"
            and isinstance(transport, httpx.AsyncHTTPTransport)
            for pattern, transport in client._mounts.items()
        )
    finally:
        await client.aclose()


def test_oras_uses_truststore_http_adapter():
    registry = hub.OrasRegistry()

    assert isinstance(
        registry.session.adapters["https://"], truststore.TruststoreHTTPAdapter
    )


async def test_fastmcp_transport_uses_truststore_context(monkeypatch):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    captured = {}

    class FakeClient:
        def __init__(self, transport, *, name, verify):
            captured["transport"] = transport
            captured["name"] = name
            captured["verify"] = verify

        async def __aenter__(self):
            return self

    monkeypatch.setattr(upstream, "ssl_context", lambda: context)
    monkeypatch.setattr(upstream, "Client", FakeClient)
    proxy = upstream.UpstreamProxy(
        {"secure": RemoteMCPServer(url="https://example.test/mcp")}
    )

    await proxy.start()

    assert isinstance(captured["transport"], StreamableHttpTransport)
    assert captured["verify"] is context
