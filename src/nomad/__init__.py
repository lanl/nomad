"""Nomad's public package.

Importing :mod:`nomad` configures supported network clients with explicit
operating-system-backed TLS contexts.
"""

from .truststore import configure_network_clients

configure_network_clients()
