from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from skim_pdf_mcp.logs import (
    LOG_FILE_ENV,
    LOG_LEVEL_ENV,
    configure_logging,
    default_log_path,
    resolve_log_level,
    resolve_log_path,
    shutdown_logging,
)
from skim_pdf_mcp.models import AnchoredNote, DocumentInfo, Rect, Selection, SkimError
from skim_pdf_mcp.pdf import open_pdf
from skim_pdf_mcp.server import build_server
from skim_pdf_mcp.service import ReadingService
from skim_pdf_mcp.skim import run_process

from fakes import FakeBackend, note

STDERR_DETAIL = (
    "execution error: Skim got an error: Application is not running. (-600)\n"
    "second line of osascript stderr"
)
COMMENT = "SECRET-COMMENT-TEXT"
QUOTE = "Hello world of math"  # the text under the lines beside the note in the sample PDF


@pytest.fixture
def log_file(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "logs" / "server.log"
    assert configure_logging(path, "INFO")
    yield path
    shutdown_logging()


def call_tool(backend: FakeBackend, name: str, args: dict[str, Any] | None = None) -> Any:
    server = build_server(ReadingService(backend, opener=open_pdf))

    async def go() -> Any:
        async with Client(server) as client:
            return await client.call_tool(name, args or {}, raise_on_error=False)

    return asyncio.run(go())


def sample_backend(sample_pdf: Path) -> FakeBackend:
    doc = DocumentInfo("sample.pdf", str(sample_pdf), page_count=2, current_page=1)
    return FakeBackend(
        [doc],
        notes={
            str(sample_pdf): [note("n1", text=COMMENT, bounds=Rect(70, 688, 200, 708))],
        },
        selections={str(sample_pdf): Selection(QUOTE, 1, Rect(70, 688, 200, 708))},
    )


class FailingListBackend(FakeBackend):
    """A backend whose note listing fails the way a refused osascript call does."""

    def __init__(self, inner: FakeBackend) -> None:
        super().__init__(inner.documents, inner.notes, inner.selections)

    def list_notes(self, path: str) -> list[AnchoredNote]:
        raise SkimError(f"'osascript' failed: {STDERR_DETAIL}")


def test_failing_tool_call_logs_full_error_and_no_document_content(
    log_file: Path, sample_pdf: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ok = call_tool(sample_backend(sample_pdf), "get_reading_context")
    assert not ok.is_error

    failing = FailingListBackend(sample_backend(sample_pdf))
    result = call_tool(failing, "get_anchored_notes")
    assert result.is_error

    text = log_file.read_text(encoding="utf-8")
    assert "tool=get_reading_context outcome=ok" in text
    assert "tool=get_anchored_notes outcome=error" in text
    assert STDERR_DETAIL in text  # both stderr lines, untruncated
    assert "Traceback" in text
    for content in (COMMENT, QUOTE, "text of page"):
        assert content not in text
    assert capsys.readouterr().out == ""  # stdout belongs to the stdio protocol


def test_subprocess_failure_logs_complete_stderr_and_never_stdout(log_file: Path) -> None:
    assert configure_logging(log_file, "DEBUG")
    # The marker is assembled at run time so it never appears in the logged command line.
    out = "print('STDOUT-' + 'DOCUMENT-TEXT')"
    fail = f"import sys; {out}; sys.stderr.write('err one\\nerr two'); sys.exit(3)"
    with pytest.raises(SkimError):
        run_process([sys.executable, "-c", fail], 10)
    run_process([sys.executable, "-c", out], 10)

    text = log_file.read_text(encoding="utf-8")
    assert "exit status 3" in text
    assert "err one\nerr two" in text
    assert "STDOUT-DOCUMENT-TEXT" not in text


def test_log_path_and_level_precedence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(LOG_FILE_ENV, raising=False)
    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
    assert resolve_log_path(None) == default_log_path()
    assert resolve_log_level(None) == "INFO"
    monkeypatch.setenv(LOG_FILE_ENV, str(tmp_path / "env.log"))
    monkeypatch.setenv(LOG_LEVEL_ENV, "debug")
    assert resolve_log_path(None) == tmp_path / "env.log"
    assert resolve_log_level(None) == "DEBUG"
    assert resolve_log_path(str(tmp_path / "flag.log")) == tmp_path / "flag.log"
    assert resolve_log_level("ERROR") == "ERROR"


def test_unwritable_log_location_does_not_stop_the_server(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    try:
        assert configure_logging(blocker / "server.log") is False
    finally:
        shutdown_logging()
