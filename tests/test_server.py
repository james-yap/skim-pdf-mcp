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

from fakes import GRAY, FakeBackend, note


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
                note("green-1", text="why?", bounds=Rect(70, 688, 200, 708)),
                note("gray-1", color=GRAY, bounds=Rect(70, 588, 200, 608)),
            ]
        },
        selections={str(sample_pdf): Selection("world", 1, Rect(100, 688, 150, 708))},
    )


def test_tool_surface_and_annotations(backend: FakeBackend) -> None:
    async def call(client: Client[Any]) -> dict[str, Any]:
        return {t.name: t.annotations for t in await client.list_tools()}

    tools = run_client(backend, call)
    assert set(tools) == {
        "get_open_documents",
        "get_reading_context",
        "get_pending_underlines",
        "mark_processed",
        "clear_processed",
    }
    for name in ("get_reading_context", "get_pending_underlines", "mark_processed"):
        assert tools[name].read_only_hint is False
        assert tools[name].destructive_hint is False
        assert tools[name].idempotent_hint is True
    assert tools["get_open_documents"].read_only_hint is True
    assert tools["clear_processed"].read_only_hint is False
    assert tools["clear_processed"].destructive_hint is True


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
    assert [u["id"] for u in data["pending_underlines"]] == ["green-1"]
    assert data["pending_underlines"][0]["quoted_text"] == "Hello world of math"
    assert data["pending_underlines"][0]["comment"] == "why?"
    assert "Hello world of math" in data["page_text"]


def test_reading_marks_what_it_returns_then_clear_round_trip_through_the_tools(
    backend: FakeBackend,
) -> None:
    async def call(client: Client[Any]) -> tuple[Any, Any, Any]:
        read = await client.call_tool("get_reading_context", {})
        pending = await client.call_tool("get_pending_underlines", {})
        cleared = await client.call_tool("clear_processed", {"ids": ["green-1", "gray-1"]})
        return read.structured_content, pending.structured_content, cleared.structured_content

    read, pending, cleared = run_client(backend, call)
    assert read["marked_processed"] == ["green-1"]
    assert pending["count"] == 0
    assert sorted(cleared["cleared"]) == ["gray-1", "green-1"]


def test_mark_processed_tool_recolors_by_id(backend: FakeBackend) -> None:
    async def call(client: Client[Any]) -> Any:
        return (await client.call_tool("mark_processed", {"ids": ["green-1"]})).structured_content

    assert run_client(backend, call)["marked_processed"] == ["green-1"]


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
