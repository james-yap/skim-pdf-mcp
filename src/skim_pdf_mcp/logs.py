"""The server's own log file.

Logging goes to a rotating file and to stderr (warnings and above), never to stdout: the stdio
transport uses stdout for the MCP protocol. Only ids, page numbers, paths, tool names, timings
and the full text of errors are logged; document text, note text, nearby text, page images and
note contents are never passed to a logger.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

LOGGER_NAME = "skim_pdf_mcp"
LOG_FILE_ENV = "SKIM_PDF_MCP_LOG_FILE"
LOG_LEVEL_ENV = "SKIM_PDF_MCP_LOG_LEVEL"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3  # server.log plus server.log.1 .. server.log.3: at most ~4 MB on disk

_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def default_log_path() -> Path:
    """``~/Library/Logs/skim-pdf-mcp/server.log`` on macOS, the XDG state dir elsewhere."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Logs" / "skim-pdf-mcp" / "server.log"
    state = Path(os.environ.get("XDG_STATE_HOME") or home / ".local" / "state")
    return state / "skim-pdf-mcp" / "server.log"


def resolve_log_path(flag: str | None) -> Path:
    """``--log-file`` wins over ``SKIM_PDF_MCP_LOG_FILE``, which wins over the default."""
    chosen = flag or os.environ.get(LOG_FILE_ENV)
    return Path(chosen).expanduser() if chosen else default_log_path()


def resolve_log_level(flag: str | None) -> str:
    """``--log-level`` wins over ``SKIM_PDF_MCP_LOG_LEVEL``, which wins over INFO."""
    level = (flag or os.environ.get(LOG_LEVEL_ENV) or "INFO").upper()
    return level if level in LOG_LEVELS else "INFO"


def configure_logging(path: Path, level: str = "INFO") -> bool:
    """Attach the rotating file handler and a stderr handler; returns whether the file works.

    Safe to call again: existing handlers installed by this function are replaced. A log file
    that cannot be created is reported on stderr and does not stop the server.
    """
    logger = logging.getLogger(LOGGER_NAME)
    shutdown_logging()
    logger.setLevel(level)
    logger.propagate = False
    formatter = logging.Formatter(_FORMAT, _DATE_FORMAT)

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setLevel(logging.WARNING)
    stderr.setFormatter(formatter)
    logger.addHandler(stderr)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("Cannot write the log file %s: %s; logging to stderr only", path, exc)
        return False
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return True


def shutdown_logging() -> None:
    """Detach and close every handler on the server's logger."""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = True
