from __future__ import annotations

from .truststore import bootstrap_truststore


def main() -> None:
    bootstrap_truststore()

    from .cli import app

    app()


if __name__ == "__main__":
    main()
