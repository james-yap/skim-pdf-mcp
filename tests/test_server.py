from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from fastmcp import Client
from fastmcp.client.client import CallToolResult
from mcp.types import ImageContent, TextContent

from skim_pdf_mcp.models import DocumentInfo, Rect, Selection
from skim_pdf_mcp.pdf import open_pdf
from skim_pdf_mcp.server import _parse_args, build_server
from skim_pdf_mcp.service import ReadingService

from fakes import FakeBackend, note


def run_client(backend: FakeBackend, call: Callable[[Client[Any]], Awaitable[Any]]) -> Any:
    server = build_server(ReadingService(backend, opener=open_pdf))

    async def go() -> Any:
        async with Client(server) as client:
            return await call(client)

    return asyncio.run(go())


@pytest.fixture
def backend(sample_pdf: Path) -> FakeBackend:
    doc = DocumentInfo("sample.pdf", str(sample_pdf), page_count=2, current_page=1)
    return FakeBackend(
        [doc],
        notes={
            str(sample_pdf): [
                note("n1", text="why?", icon="help", bounds=Rect(560, 688, 576, 704)),
                note("n2", page=2, text="and here?", icon="key"),
            ]
        },
        selections={str(sample_pdf): Selection("world", 1, Rect(100, 688, 150, 708))},
    )


def test_tool_surface_is_read_only(backend: FakeBackend) -> None:
    async def call(client: Client[Any]) -> dict[str, Any]:
        return {t.name: t.annotations for t in await client.list_tools()}

    tools = run_client(backend, call)
    assert set(tools) == {"get_open_documents", "get_reading_context", "get_anchored_notes"}
    for hints in tools.values():
        assert hints.read_only_hint is True
        assert hints.destructive_hint is False


def test_reading_context_returns_json_text_and_one_png_page(backend: FakeBackend) -> None:
    async def call(client: Client[Any]) -> CallToolResult:
        return await client.call_tool("get_reading_context", {})

    result = run_client(backend, call)
    kinds = [type(c) for c in result.content]
    assert kinds == [TextContent, ImageContent]
    image = result.content[1]
    assert isinstance(image, ImageContent) and image.mime_type == "image/png"
    png = base64.b64decode(image.data)
    assert png.startswith(b"\x89PNG")
    pix = pymupdf.Pixmap(png)
    assert (pix.width, pix.height) == (1275, 1650)
    data = result.structured_content
    assert data is not None
    assert data["selection"]["text"] == "world"
    # Every note of the document comes back, including the one on the page that is not shown.
    assert [(n["id"], n["page"], n["icon"], n["text"]) for n in data["notes"]] == [
        ("n1", 1, "help", "why?"),
        ("n2", 2, "key", "and here?"),
    ]
    assert "Hello world of math" in data["notes"][0]["nearby_text"]
    assert "Hello world of math" in data["page_text"]


def test_anchored_notes_tool_is_repeatable(backend: FakeBackend) -> None:
    async def call(client: Client[Any]) -> tuple[Any, Any]:
        first = await client.call_tool("get_anchored_notes", {})
        second = await client.call_tool("get_anchored_notes", {})
        return first.structured_content, second.structured_content

    first, second = run_client(backend, call)
    assert first == second
    assert first["count"] == 2


def test_skim_errors_reach_the_caller_as_tool_errors() -> None:
    empty = FakeBackend([])

    async def call(client: Client[Any]) -> CallToolResult:
        return await client.call_tool("get_reading_context", {}, raise_on_error=False)

    result = run_client(empty, call)
    assert result.is_error
    assert "No PDF documents are open" in result.content[0].text


def test_cli_defaults_to_stdio_and_loopback_http() -> None:
    args = _parse_args([])
    assert (args.transport, args.host, args.port, args.path) == ("stdio", "127.0.0.1", 8765, "/mcp")
    assert _parse_args(["--transport", "http", "--port", "9000"]).port == 9000
