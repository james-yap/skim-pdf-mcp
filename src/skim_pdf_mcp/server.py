"""FastMCP server: tool definitions and the command-line entry point."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from functools import wraps
from typing import Any, TypeVar

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from fastmcp.utilities.types import Image

from . import __version__
from .models import SkimError
from .service import ReadingService
from .skim import AppleScriptBackend, SkimNotesReader

INSTRUCTIONS = """\
Bridges the Skim PDF reader to ChatGPT. The reader marks passages to ask about with RED \
underlines (pending) in Skim; processed ones are GRAY. Highlights and other markup belong to \
the reader and are never touched.

Typical flow: call get_reading_context to see the front document, current page, live selection, \
pending underlines and a rendered page image. Answer the reader's questions, then - only after \
the reader approves - call mark_processed with the ids you handled. Reading never modifies \
anything. clear_processed permanently deletes gray underlines and must always be approved by \
the reader first.
"""

_READ_ONLY: dict[str, Any] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

F = TypeVar("F", bound=Callable[..., Any])


def _tool_errors(fn: F) -> F:
    """Surface SkimError messages to the caller verbatim as MCP tool errors."""

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except SkimError as exc:
            raise ToolError(str(exc)) from exc

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
        """Everything about what the reader is looking at, in one call.

        Returns the front document (name, path), its current page, the live text selection
        (explicitly empty when nothing is selected), pending red underlines with their quoted
        text, page, color and comment, the page's text layer, and a PNG of the page with the
        selection (blue, only when it is on the rendered page) and the pending underlines
        on that page (red) outlined. The image is the ground truth for math; the text layer
        may garble formulas. Read-only.

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

    @mcp.tool(title="Get pending underlines", annotations=_READ_ONLY)
    @_tool_errors
    def get_pending_underlines(document_path: str | None = None) -> dict[str, Any]:
        """List pending (red) underlines of a document in reading order, with quoted text.

        Args:
            document_path: Path of an open document. Default: the front document.
        """
        return svc.pending_underlines(document_path)

    @mcp.tool(
        title="Mark underlines processed",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    @_tool_errors
    def mark_processed(ids: list[str], document_path: str | None = None) -> dict[str, Any]:
        """Recolor pending (red) underlines gray by id. MODIFIES the reader's Skim document.

        Only run after the reader has approved. Highlights and underlines of other colors are
        skipped, never changed. Nothing is deleted.

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
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    server = build_server()
    if args.transport == "stdio":
        server.run(transport="stdio", show_banner=False)
    else:
        server.run(
            transport="http", host=args.host, port=args.port, path=args.path, show_banner=False
        )


if __name__ == "__main__":
    main()
