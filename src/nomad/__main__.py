from __future__ import annotations

from .truststore import configure_network_clients


def main() -> None:
    configure_network_clients()

    from .cli import app

    app()


if __name__ == "__main__":
    main()
