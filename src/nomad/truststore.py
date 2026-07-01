from __future__ import annotations

_BOOTSTRAPPED = False
HUGGINGFACE_HTTP_RETRIES = 10
HUGGINGFACE_HTTP_BACKOFF_FACTOR = 0.1


def bootstrap_truststore() -> None:
    """Install system trust roots and configure HTTP clients once per process."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return

    import truststore

    truststore.inject_into_ssl()
    configure_huggingface_http()
    _BOOTSTRAPPED = True


def configure_huggingface_http() -> None:
    """Configure Hugging Face Hub clients with Nomad's TLS/retry policy."""
    try:
        import huggingface_hub
    except ImportError:
        return

    if hasattr(huggingface_hub, "set_client_factory"):
        huggingface_hub.set_client_factory(_huggingface_httpx_client_factory)
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
        )
        for pattern, proxy in get_environment_proxies().items()
    }

    return httpx.Client(
        transport=httpx.HTTPTransport(retries=HUGGINGFACE_HTTP_RETRIES),
        mounts=mounts,
        follow_redirects=True,
        timeout=None,
        event_hooks=event_hooks,
    )


def _huggingface_requests_session_factory():
    import requests
    from requests.adapters import HTTPAdapter
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
        adapter: HTTPAdapter = HTTPAdapter(max_retries=retry)
    else:
        if constants.HF_HUB_OFFLINE:
            adapter = OfflineAdapter()
        else:
            adapter = UniqueRequestIdAdapter(max_retries=retry)

    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
