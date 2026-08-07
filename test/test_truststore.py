from __future__ import annotations

import ssl
import sys
import types

import httpx
import pytest
import requests
from fastmcp.client.auth.oauth import OAuth
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from fastmcp.mcp_config import RemoteMCPServer
from urllib3.response import HTTPResponse

from nomad import hub, otel, truststore
from nomad.gateway import upstream


def poison_certificate_environment(monkeypatch):
    for variable in (
        "CURL_CA_BUNDLE",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
    ):
        monkeypatch.setenv(variable, "/alternate/certificates")


def test_ssl_context_is_os_backed_and_ignores_certificate_environment(monkeypatch):
    import truststore as truststore_package

    poison_certificate_environment(monkeypatch)

    context = truststore.ssl_context()

    assert isinstance(context, truststore_package.SSLContext)
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_requests_adapter_uses_truststore_for_https_proxy(monkeypatch):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    monkeypatch.setattr(truststore, "ssl_context", lambda: context)

    manager = truststore.TruststoreHTTPAdapter().proxy_manager_for(
        "https://proxy.example:8443",
        proxy_ssl_context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
    )

    assert manager.proxy_ssl_context is context


@pytest.mark.parametrize("verify", [False, "/alternate/ca.pem"])
def test_requests_session_send_forces_truststore_verification(monkeypatch, verify):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    monkeypatch.setattr(truststore, "ssl_context", lambda: context)
    adapter = truststore.TruststoreHTTPAdapter()
    captured = {}

    class FakePool:
        cert_reqs = None
        ca_certs = None
        ca_cert_dir = None
        cert_file = None
        key_file = None

        def urlopen(self, **kwargs):
            return HTTPResponse(status=200, body=b"", preload_content=False)

    def connection_from_host(host, port=None, scheme="http", pool_kwargs=None):
        captured.update(pool_kwargs or {})
        return FakePool()

    monkeypatch.setattr(
        adapter.poolmanager, "connection_from_host", connection_from_host
    )
    session = requests.Session()
    session.mount("https://", adapter)
    request = requests.Request("GET", "https://example.test").prepare()

    response = session.send(request, verify=verify, stream=True)

    assert response.status_code == 200
    assert captured["ssl_context"] is context
    assert "ca_certs" not in captured
    assert "ca_cert_dir" not in captured
    assert "cert_reqs" not in captured


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

    for variable in (
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    poison_certificate_environment(monkeypatch)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    def make_context():
        return context

    monkeypatch.setattr(truststore, "ssl_context", make_context)

    truststore.configure_huggingface_http()

    client = calls["factory"]()
    try:
        assert isinstance(client, httpx.Client)
        assert client.follow_redirects is True
        https_transport = next(
            transport
            for pattern, transport in client._mounts.items()
            if getattr(pattern, "pattern", None) == "https://"
        )
        assert isinstance(https_transport, httpx.HTTPTransport)
        assert client._transport._pool._ssl_context is context
        assert https_transport._pool._ssl_context is context
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
    poison_certificate_environment(monkeypatch)

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


async def test_huggingface_async_client_supports_versions_without_hooks(monkeypatch):
    fake_http = types.ModuleType("huggingface_hub.utils._http")
    monkeypatch.setitem(sys.modules, "huggingface_hub.utils._http", fake_http)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    monkeypatch.setattr(truststore, "ssl_context", lambda: context)

    client = truststore._huggingface_async_httpx_client_factory()
    try:
        assert client._transport._pool._ssl_context is context
        assert client._event_hooks == {"request": [], "response": []}
    finally:
        await client.aclose()


def test_oras_uses_truststore_http_adapter():
    registry = hub.OrasRegistry()

    assert isinstance(
        registry.session.adapters["https://"], truststore.TruststoreHTTPAdapter
    )


def test_otlp_exporter_uses_truststore_and_signal_endpoint():
    kwargs = otel._otlp_exporter_kwargs("https://collector.example/", "traces")
    session = kwargs["session"]

    assert isinstance(session.adapters["https://"], truststore.TruststoreHTTPAdapter)
    assert kwargs["endpoint"] == "https://collector.example/v1/traces"


def test_legacy_huggingface_session_uses_truststore_adapter(monkeypatch):
    import huggingface_hub

    if not hasattr(huggingface_hub, "configure_http_backend"):
        pytest.skip("requires the legacy Hugging Face Requests backend")

    from huggingface_hub.utils._http import (
        UniqueRequestIdAdapter,
        get_session,
        reset_sessions,
    )

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    monkeypatch.setattr(truststore, "ssl_context", lambda: context)
    truststore.configure_huggingface_http()
    reset_sessions()
    session = get_session()
    adapter = session.adapters["https://"]
    request = requests.Request("GET", "https://example.test").prepare()

    _, pool_kwargs = adapter.build_connection_pool_key_attributes(request, True)

    assert isinstance(adapter, UniqueRequestIdAdapter)
    assert pool_kwargs["ssl_context"] is context


@pytest.mark.parametrize(
    ("url", "transport_type"),
    [
        ("https://example.test/mcp", StreamableHttpTransport),
        ("https://example.test/sse", SSETransport),
    ],
)
async def test_fastmcp_transport_and_oauth_use_truststore_context(
    monkeypatch, url, transport_type
):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    poison_certificate_environment(monkeypatch)

    async def fake_enter(client):
        return client

    monkeypatch.setattr(upstream, "ssl_context", lambda: context)
    monkeypatch.setattr(upstream.Client, "__aenter__", fake_enter)
    proxy = upstream.UpstreamProxy({"secure": RemoteMCPServer(url=url, auth="oauth")})

    with pytest.warns(UserWarning, match="in-memory token storage"):
        await proxy.start()

    client = proxy._clients["secure"]
    assert isinstance(client.transport, transport_type)
    assert client.transport.verify is context
    assert isinstance(client.transport.auth, OAuth)

    oauth_client = client.transport.auth.httpx_client_factory()
    try:
        assert oauth_client._transport._pool._ssl_context is context
    finally:
        await oauth_client.aclose()
