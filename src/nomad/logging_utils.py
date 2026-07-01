from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LEVEL_ORDER = [
    logging.DEBUG,
    logging.INFO,
    logging.WARNING,
    logging.ERROR,
    logging.CRITICAL,
]
_STANDARD_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)
_TO_CLIENT_LEVEL_RE = re.compile(r"^Sending ([A-Z]+) to client")
_DEFAULT_STDERR_OVERRIDES = {
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
}


def parse_log_level(level: str) -> int:
    """Parse a logging level name into its numeric value."""
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {level}")
    return numeric_level


def more_verbose_log_level(level: int) -> int:
    """Return the next more verbose standard logging level."""
    if level <= logging.DEBUG:
        return logging.DEBUG
    try:
        index = _LEVEL_ORDER.index(level)
    except ValueError:
        return level
    return _LEVEL_ORDER[max(index - 1, 0)]


class JsonlFormatter(logging.Formatter):
    """Format log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=UTC,
            ).isoformat(),
            "logger": record.name,
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_FIELDS or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=_json_default)


class LoggerThresholdFilter(logging.Filter):
    """Filter records using a default threshold and logger-specific overrides."""

    def __init__(
        self,
        default_level: int,
        overrides: dict[str, int] | None = None,
    ) -> None:
        super().__init__()
        self._default_level = default_level
        self._overrides = sorted(
            (overrides or {}).items(),
            key=lambda item: len(item[0]),
            reverse=True,
        )

    def filter(self, record: logging.LogRecord) -> bool:
        threshold = self._default_level
        for prefix, level in self._overrides:
            if record.name == prefix or record.name.startswith(f"{prefix}."):
                threshold = level
                break
        return _effective_level(record) >= threshold


def build_jsonl_file_handler(path: Path, *, level: int) -> logging.FileHandler:
    """Create an append-only JSONL file handler."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(JsonlFormatter())
    return handler


def configure_root_logging(
    *,
    stderr_level: int,
    format_string: str = "{asctime} | {levelname:^8} | {name}:{lineno} - {message}",
    log_file: Path | None = None,
    stderr_overrides: dict[str, int] | None = None,
) -> logging.Handler | None:
    """Configure root stderr logging and an optional JSONL log file."""
    file_level = more_verbose_log_level(stderr_level)
    stderr_overrides = (
        _DEFAULT_STDERR_OVERRIDES if stderr_overrides is None else stderr_overrides
    )
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(file_level if log_file is not None else stderr_level)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setFormatter(
        logging.Formatter(
            format_string,
            style="{",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    stderr_handler.addFilter(
        LoggerThresholdFilter(
            default_level=stderr_level,
            overrides=stderr_overrides,
        )
    )
    root.addHandler(stderr_handler)

    if log_file is None:
        return None

    file_handler = build_jsonl_file_handler(log_file, level=file_level)
    root.addHandler(file_handler)

    return file_handler


def attach_handler(logger: logging.Logger, handler: logging.Handler) -> None:
    """Attach ``handler`` to ``logger`` if it is not already present."""
    if handler not in logger.handlers:
        logger.addHandler(handler)


def _effective_level(record: logging.LogRecord) -> int:
    if record.name == "fastmcp.server.context.to_client":
        match = _TO_CLIENT_LEVEL_RE.match(record.getMessage())
        if match is not None:
            parsed = getattr(logging, match.group(1), None)
            if isinstance(parsed, int):
                return parsed
    return record.levelno


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return repr(value)
