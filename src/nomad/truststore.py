from __future__ import annotations

import ssl

from requests.adapters import HTTPAdapter

_CONFIGURED = False
HUGGINGFACE_HTTP_RETRIES = 10
HUGGINGFACE_HTTP_BACKOFF_FACTOR = 0.1


def ssl_context() -> ssl.SSLContext:
    """Return a client context backed by the operating system trust store."""
    import truststore

    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


class TruststoreHTTPAdapter(HTTPAdapter):
    """Use an OS-backed TLS context for requests and HTTPS proxies."""

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(
            request, verify, cert
        )
        # Requests resolves REQUESTS_CA_BUNDLE/CURL_CA_BUNDLE into ``verify``
        # before the adapter sees the request. Discard every CA-file setting so
        # this adapter always has one trust source: the operating-system store.
        for key in ("ca_certs", "ca_cert_dir", "cert_reqs"):
            pool_kwargs.pop(key, None)
        pool_kwargs["ssl_context"] = ssl_context()
        return host_params, pool_kwargs


def configure_network_clients() -> None:
    """Configure supported network clients with explicit truststore contexts."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    configure_huggingface_http()
    _CONFIGURED = True


def configure_huggingface_http() -> None:
    """Configure Hugging Face Hub clients with Nomad's TLS/retry policy."""
    try:
        import huggingface_hub
    except ImportError:
        return

    if hasattr(huggingface_hub, "set_client_factory"):
        huggingface_hub.set_client_factory(_huggingface_httpx_client_factory)
        if hasattr(huggingface_hub, "set_async_client_factory"):
            huggingface_hub.set_async_client_factory(
                _huggingface_async_httpx_client_factory
            )
    elif hasattr(huggingface_hub, "configure_http_backend"):
        huggingface_hub.configure_http_backend(_huggingface_requests_session_factory)


def _huggingface_httpx_client_factory():
    import httpx
    from httpx._utils import get_environment_proxies

    event_hooks = {}
    try:
        from huggingface_hub.utils._http import hf_request_event_hook
    except ImportError:
        pass
    else:
        event_hooks["request"] = [hf_request_event_hook]

    mounts = {
        pattern: None
        if proxy is None
        else httpx.HTTPTransport(
            proxy=proxy,
            retries=HUGGINGFACE_HTTP_RETRIES,
            verify=ssl_context(),
        )
        for pattern, proxy in get_environment_proxies().items()
    }

    return httpx.Client(
        transport=httpx.HTTPTransport(
            retries=HUGGINGFACE_HTTP_RETRIES,
            verify=ssl_context(),
        ),
        mounts=mounts,
        follow_redirects=True,
        timeout=None,
        event_hooks=event_hooks,
    )


def _huggingface_async_httpx_client_factory():
    import httpx
    from huggingface_hub.utils._http import (
        async_hf_request_event_hook,
        async_hf_response_event_hook,
    )

    return httpx.AsyncClient(
        verify=ssl_context(),
        follow_redirects=True,
        timeout=None,
        event_hooks={
            "request": [async_hf_request_event_hook],
            "response": [async_hf_response_event_hook],
        },
    )


def _huggingface_requests_session_factory():
    import requests
    from urllib3.util.retry import Retry

    retry = Retry(
        total=HUGGINGFACE_HTTP_RETRIES,
        connect=HUGGINGFACE_HTTP_RETRIES,
        read=HUGGINGFACE_HTTP_RETRIES,
        backoff_factor=HUGGINGFACE_HTTP_BACKOFF_FACTOR,
        allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}),
        backoff_jitter=0.5,
    )

    try:
        from huggingface_hub import constants
        from huggingface_hub.utils._http import OfflineAdapter, UniqueRequestIdAdapter
    except ImportError:
        adapter: HTTPAdapter = TruststoreHTTPAdapter(max_retries=retry)
    else:
        if constants.HF_HUB_OFFLINE:
            adapter = OfflineAdapter()
        else:

            class TruststoreUniqueRequestIdAdapter(
                UniqueRequestIdAdapter, TruststoreHTTPAdapter
            ):
                pass

            adapter = TruststoreUniqueRequestIdAdapter(max_retries=retry)

    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
