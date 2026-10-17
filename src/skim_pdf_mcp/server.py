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
Bridges the Skim PDF reader to ChatGPT. The reader leaves questions and comments for you as \
ANCHORED NOTES in Skim: note icons (any icon: comment, key, note, help, ...) pinned to a spot \
on a page, each holding the reader's text.

Whenever this server is invoked, even with no specific request, do this without asking first: \
call get_reading_context. It returns the front document, the page being read, the live \
selection, the page's text and an image of the page, and EVERY anchored note in the whole \
document in reading order (page, then top to bottom), each with its text (what the reader wrote: \
the question or comment to answer), its quoted_text (the passage the note was made from, when it \
was created from a selection; context, not the question), its page, icon, the page text level \
with it (nearby_text) and its position. Then address ALL of the notes, in \
order, one by one: answer the question or respond to the comment in each note, saying which page \
and passage it is pinned to. Do not skip any, do not stop at the current page, and do not ask \
which one to start with. The page image is the ground truth for math; the text layer can garble \
formulas. Other markup (highlights, underlines, text boxes) is not addressed to you.

This server is strictly read-only: it never changes the document and keeps no record of what it \
has returned, so reading again returns the same notes. Use get_anchored_notes for the notes \
alone, for another document, or to re-read them; get_open_documents lists the open PDFs.
"""

_READ_ONLY: dict[str, Any] = {
    "readOnlyHint": True,
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

    @mcp.tool(title="Get reading context", annotations=_READ_ONLY)
    @_tool_errors
    def get_reading_context(
        document_path: str | None = None,
        page: int | None = None,
        include_image: bool = True,
    ) -> ToolResult:
        """START HERE whenever the reader invokes this server. Returns everything, then address
        EVERY anchored note it lists, in order, without asking which to start with.

        Returns the front document (name, path), its current page, the live text selection
        (explicitly empty when nothing is selected), the page's text layer, a PNG of the page
        with the selection (blue, only when it is on the rendered page) and the anchored notes
        on that page (orange) outlined, and every anchored note in the whole document in
        reading order: its text (what the reader wrote: the question or comment), quoted_text
        (the passage the note was made from, when it was created from a selection), page, icon,
        nearby_text (the page text level with the note) and position. The image is the ground
        truth for math; the text layer may garble formulas. Read-only: nothing is modified.

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

    @mcp.tool(title="Get anchored notes", annotations=_READ_ONLY)
    @_tool_errors
    def get_anchored_notes(document_path: str | None = None) -> dict[str, Any]:
        """List every anchored note of a document in reading order (page, then top to bottom),
        each with its text (what the reader wrote), quoted_text (the passage it was made from),
        page, icon, nearby_text and position. Address all of them.
        Read-only: nothing is modified, and calling it again returns the same notes.

        Args:
            document_path: Path of an open document. Default: the front document.
        """
        return svc.anchored_notes(document_path)

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
