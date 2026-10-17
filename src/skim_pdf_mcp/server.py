"""FastMCP server: tool definitions and the command-line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import platform
import time
from collections.abc import Callable, Sequence
from functools import wraps
from typing import Any, TypeVar

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from fastmcp.utilities.types import Image

from . import __version__
from .logs import (
    LOG_FILE_ENV,
    LOG_LEVEL_ENV,
    LOG_LEVELS,
    configure_logging,
    default_log_path,
    resolve_log_level,
    resolve_log_path,
)
from .models import SkimError
from .service import ReadingService
from .skim import AppleScriptBackend, SkimNotesReader

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
Bridges the Skim PDF reader to ChatGPT. The reader marks passages to ask about with RED \
underlines (pending) in Skim; processed ones are GRAY. Highlights and other markup belong to \
the reader and are never touched.

Typical flow: call get_reading_context to see the front document, current page, live selection, \
pending underlines and a rendered page image. Answer the reader's questions. Reading marks the \
pending underlines it returns as processed (gray) in the same call, so there is nothing to \
approve or confirm; the response lists them in marked_processed, and any that could not be \
marked are named in warnings. A repeated read within 10 minutes lists the underlines just \
marked again with recently_marked true: they are the same underlines the reader asked about, \
already gray, not new ones. mark_processed re-marks specific ids by hand. clear_processed \
permanently deletes gray underlines and must always be approved by the reader first.
"""

_READ_ONLY: dict[str, Any] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

# Reading tools recolor the pending underlines they return, so they modify the document.
_MARKS_PROCESSED: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

F = TypeVar("F", bound=Callable[..., Any])


def _tool_errors(fn: F) -> F:
    """Log every tool call (name, arguments, outcome, duration) and surface SkimError messages
    to the caller verbatim as MCP tool errors. Failures are logged with the full traceback.
    """
    name = fn.__name__

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.monotonic()
        try:
            result = fn(*args, **kwargs)
        except SkimError as exc:
            log.error(
                "tool=%s outcome=error duration_ms=%.0f args=%s: %s",
                name,
                (time.monotonic() - started) * 1000,
                kwargs,
                exc,
                exc_info=True,
            )
            raise ToolError(str(exc)) from exc
        except Exception:
            log.exception(
                "tool=%s outcome=crash duration_ms=%.0f args=%s",
                name,
                (time.monotonic() - started) * 1000,
                kwargs,
            )
            raise
        log.info(
            "tool=%s outcome=ok duration_ms=%.0f args=%s",
            name,
            (time.monotonic() - started) * 1000,
            kwargs,
        )
        return result

    return wrapper  # type: ignore[return-value]


def build_server(service: ReadingService | None = None) -> FastMCP:
    """Create the MCP server; tests pass a service wired to fakes."""
    svc = service or ReadingService(AppleScriptBackend(notes_fallback=SkimNotesReader()))
    mcp = FastMCP("skim-pdf", instructions=INSTRUCTIONS, version=__version__)

    @mcp.tool(title="List open Skim documents", annotations=_READ_ONLY)
    @_tool_errors
    def get_open_documents() -> dict[str, Any]:
        """List open Skim PDFs, frontmost first, with warnings for skipped path lookups."""
        return svc.open_documents()

    @mcp.tool(title="Get reading context", annotations=_MARKS_PROCESSED)
    @_tool_errors
    def get_reading_context(
        document_path: str | None = None,
        page: int | None = None,
        include_image: bool = True,
    ) -> ToolResult:
        """Everything about what the reader is looking at, in one call.

        Returns the front document (name, path), its current page, the live text selection
        (explicitly empty when nothing is selected), pending red underlines with their quoted
        text, page, color and comment, the page's text layer, and a PNG of the page with the
        selection (blue, only when it is on the rendered page) and the listed underlines on
        that page (red) outlined. The image is the ground truth for math; the text layer
        may garble formulas.

        MODIFIES the reader's Skim document: the pending underlines returned in this response
        (at most 50; pending_truncated says if more remain) are recolored gray (processed) in
        the same call, with no further approval. The ids that were marked are in
        marked_processed; ones that could not be marked are named in warnings. Underlines
        marked by an earlier read in the last 10 minutes are listed again with
        recently_marked true (they stay gray and are not marked twice), so calling this tool
        twice in one conversation does not hide them.

        Args:
            document_path: Path of an open document (see get_open_documents). Default: front.
            page: 1-based page to render. Default: the selection's page, else the current page.
            include_image: Set false to skip rendering the page PNG.
        """
        ctx = svc.reading_context(document_path, page, include_image)
        content: list[Any] = [json.dumps(ctx.data, indent=2)]
        if ctx.png is not None:
            content.append(Image(data=ctx.png, format="png"))
        return ToolResult(content=content, structured_content=ctx.data)

    @mcp.tool(title="Get pending underlines", annotations=_MARKS_PROCESSED)
    @_tool_errors
    def get_pending_underlines(document_path: str | None = None) -> dict[str, Any]:
        """List pending (red) underlines of a document in reading order, with quoted text.

        MODIFIES the reader's Skim document: every underline returned is recolored gray
        (processed) in the same call, with no further approval. The ids that were marked are
        in marked_processed; ones that could not be marked are named in warnings. Underlines
        marked by an earlier read in the last 10 minutes are listed again with
        recently_marked true (they stay gray and are not marked twice), so calling this tool
        twice in one conversation does not hide them.

        Args:
            document_path: Path of an open document. Default: the front document.
        """
        return svc.pending_underlines(document_path)

    @mcp.tool(title="Mark underlines processed", annotations=_MARKS_PROCESSED)
    @_tool_errors
    def mark_processed(ids: list[str], document_path: str | None = None) -> dict[str, Any]:
        """Recolor pending (red) underlines gray by id. MODIFIES the reader's Skim document.

        Reading tools already mark the underlines they return, so this is only for re-marking
        specific ids by hand (for example ones a read reported it could not mark). Highlights
        and underlines of other colors are skipped, never changed. Nothing is
        deleted.

        Args:
            ids: Underline ids from get_pending_underlines or get_reading_context.
            document_path: Path of an open document. Default: the front document.
        """
        return svc.mark_processed(ids, document_path)

    @mcp.tool(
        title="Clear processed underlines",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    @_tool_errors
    def clear_processed(ids: list[str], document_path: str | None = None) -> dict[str, Any]:
        """PERMANENTLY DELETE processed (gray) underlines by id. MODIFIES the Skim document.

        Only run after the reader has explicitly approved clearing these ids. Deletes exactly
        the ids passed, and only if they are gray underlines: pending (red) underlines,
        highlights and other markup are skipped.

        Args:
            ids: Ids of gray underlines to delete.
            document_path: Path of an open document. Default: the front document.
        """
        return svc.clear_processed(ids, document_path)

    return mcp


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="skim-pdf-mcp",
        description="MCP server bridging the Skim PDF reader to ChatGPT.",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http", "streamable-http"),
        default="stdio",
        help="stdio (default) or streamable HTTP ('http' and 'streamable-http' are the same).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address (default: loopback)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    parser.add_argument("--path", default="/mcp", help="HTTP endpoint path (default: /mcp)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--log-file",
        default=None,
        metavar="PATH",
        help=(
            f"log file (default: {default_log_path()}; env {LOG_FILE_ENV}). "
            "Rotated at 1 MB, 3 backups kept."
        ),
    )
    parser.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        default=None,
        help=(
            f"log verbosity (default: INFO; env {LOG_LEVEL_ENV}). DEBUG logs every osascript call."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    log_path = resolve_log_path(args.log_file)
    configure_logging(log_path, resolve_log_level(args.log_level))
    log.info(
        "Starting skim-pdf-mcp %s: transport=%s python=%s platform=%s log_file=%s",
        __version__,
        args.transport,
        platform.python_version(),
        platform.platform(),
        log_path,
    )
    server = build_server()
    if args.transport == "stdio":
        server.run(transport="stdio", show_banner=False)
    else:
        log.info("Serving HTTP on %s:%d%s", args.host, args.port, args.path)
        server.run(
            transport="http", host=args.host, port=args.port, path=args.path, show_banner=False
        )


if __name__ == "__main__":
    main()
